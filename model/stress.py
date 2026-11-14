"""A6. Crop stress model: predict an LGA-year's peak cropland NDVI anomaly from its weather.

Target and features are both within-LGA anomalies. Feature climatology uses inputs only, never
the target, so it cannot leak across folds. Ridge is the baseline and LightGBM the main model,
both judged against climatology (predicting the LGA's normal) on leave-one-year-out and
senatorial-zone folds (engineering standards, section 4). Settings: config/models.yaml.
"""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import lightgbm as lgb
import numpy as np
import optuna
import pandas as pd
import shap
from sklearn.linear_model import Ridge, RidgeCV
from sklearn.metrics import mean_absolute_error, mean_squared_error
from sklearn.model_selection import GroupKFold
from sklearn.pipeline import Pipeline, make_pipeline
from sklearn.preprocessing import StandardScaler

SMAP = "season_smap_anomaly_z"


def window_sums(month: pd.DataFrame, column: str, months: list[int]) -> pd.Series:
    """Sum a monthly column over the given months per LGA-year; NaN unless every month is there."""
    m = month[month["month"].isin(months)]
    grouped = m.groupby(["lga", "year"])[column]
    total = grouped.sum(min_count=1)
    complete = grouped.count() == len(months)
    return total.where(complete)


def feature_climatology(df: pd.DataFrame, columns: list[str]) -> pd.DataFrame:
    """Per-LGA mean and sd of each column. Index: LGA; columns: (stat, feature)."""
    grouped = df.groupby("lga")[columns]
    return pd.concat({"mean": grouped.mean(), "sd": grouped.std()}, axis=1)


def to_anomalies(
    df: pd.DataFrame, clim: pd.DataFrame, weather: list[str], static: list[str]
) -> pd.DataFrame:
    """Weather columns as within-LGA z-scores; static columns and SMAP (already a z) unchanged.

    A weather column with no variation in an LGA gets 0 where the value is present.
    """
    anomaly_cols = [c for c in weather if c != SMAP]
    mean = clim["mean"][anomaly_cols].reindex(df["lga"]).to_numpy()
    sd = clim["sd"][anomaly_cols].reindex(df["lga"]).to_numpy()
    raw = df[anomaly_cols].to_numpy(dtype=float)
    with np.errstate(divide="ignore", invalid="ignore"):
        z = np.where(sd > 0, (raw - mean) / sd, np.where(np.isnan(raw), np.nan, 0.0))
    out = pd.DataFrame(z, columns=anomaly_cols, index=df.index)
    if SMAP in weather:
        out[SMAP] = df[SMAP]
    return pd.concat([out[weather], df[static]], axis=1)


def ridge_matrix(x: pd.DataFrame) -> pd.DataFrame:
    """Ridge cannot take NaN: missing SMAP (before 2015) becomes 0, the LGA's normal.

    No missing indicator: it is 1 for every year before 2015, so it acts as an era marker and
    let Ridge fit a greenness trend instead of weather (tested 14 Nov 2026, docs/model-results.md).
    """
    return x.fillna(0.0)


def fit_ridge(x: pd.DataFrame, y: np.ndarray, alphas: np.ndarray) -> Pipeline:
    """Standardize, then Ridge with alpha chosen by efficient leave-one-out CV."""
    return make_pipeline(StandardScaler(), RidgeCV(alphas=alphas)).fit(ridge_matrix(x), y)


def early_stopped_trees(
    x: pd.DataFrame,
    y: np.ndarray,
    years: np.ndarray,
    params: dict[str, Any],
    rounds: int,
    min_trees: int,
    val_share: float,
    seed: int,
) -> int:
    """Tree count from early stopping on a block of held-out training years."""
    unique_years = np.unique(years)
    n_val = max(2, int(len(unique_years) * val_share))
    val_years = np.random.default_rng(seed).choice(unique_years, size=n_val, replace=False)
    val = np.isin(years, val_years)
    model = lgb.LGBMRegressor(**params, random_state=seed)
    model.fit(
        x[~val],
        y[~val],
        eval_set=[(x[val], y[val])],
        callbacks=[lgb.early_stopping(rounds, verbose=False)],
    )
    return max(int(model.best_iteration_ or 0), min_trees)


def fit_lgbm(
    x: pd.DataFrame,
    y: np.ndarray,
    params: dict[str, Any],
    n_trees: int,
    seed: int,
    alpha: float | None = None,
) -> lgb.LGBMRegressor:
    """LightGBM with a fixed tree count; a quantile model when alpha is given."""
    extra: dict[str, Any] = {"n_estimators": n_trees}
    if alpha is not None:
        extra |= {"objective": "quantile", "alpha": alpha}
    return lgb.LGBMRegressor(**{**params, **extra}, random_state=seed).fit(x, y)


def bootstrap_interval(
    x_train: pd.DataFrame,
    y_train: np.ndarray,
    x_test: pd.DataFrame,
    alpha: float,
    quantiles: list[float],
    runs: int,
    seed: int,
) -> np.ndarray:
    """Ridge prediction interval by bootstrap: refit on resampled rows, add a resampled residual.

    Returns one row per test row, one column per quantile.
    """
    rng = np.random.default_rng(seed)
    r_train, r_test = ridge_matrix(x_train), ridge_matrix(x_test)
    draws = np.empty((runs, len(x_test)))
    for b in range(runs):
        idx = rng.integers(0, len(y_train), len(y_train))
        model = make_pipeline(StandardScaler(), Ridge(alpha=alpha)).fit(
            r_train.iloc[idx], y_train[idx]
        )
        resid = y_train[idx] - model.predict(r_train.iloc[idx])
        draws[b] = model.predict(r_test) + rng.choice(resid, size=len(x_test))
    return np.quantile(draws, quantiles, axis=0).T


def climatology_rmse_skill(y: np.ndarray, pred: np.ndarray) -> float:
    """RMSE skill against predicting 0 (the LGA's own normal): 1 - RMSE_model / RMSE_normal."""
    return float(1 - np.sqrt(np.mean((y - pred) ** 2)) / np.sqrt(np.mean(y**2)))


def score_predictions(cv: pd.DataFrame, models: list[str]) -> pd.DataFrame:
    """RMSE, MAE, skill against climatology and correlation per scheme and model."""
    rows = []
    for scheme, d in cv.groupby("scheme"):
        y = d["target"].to_numpy()
        for name in models:
            pred = d[name].to_numpy()
            rows.append(
                {
                    "scheme": scheme,
                    "model": name,
                    "n": len(y),
                    "rmse": float(np.sqrt(mean_squared_error(y, pred))),
                    "mae": float(mean_absolute_error(y, pred)),
                    "skill_vs_climatology": climatology_rmse_skill(y, pred),
                    "corr": float(np.corrcoef(y, pred)[0, 1]) if np.std(pred) > 0 else np.nan,
                }
            )
    return pd.DataFrame(rows)


def interval_coverage(cv: pd.DataFrame, intervals: dict[str, tuple[str, str]]) -> pd.DataFrame:
    """Share of outcomes inside each named interval, and its mean width, per scheme."""
    rows = []
    for scheme, d in cv.groupby("scheme"):
        for name, (low, high) in intervals.items():
            inside = (d["target"] >= d[low]) & (d["target"] <= d[high])
            rows.append(
                {
                    "scheme": scheme,
                    "interval": name,
                    "coverage": float(inside.mean()),
                    "mean_width": float((d[high] - d[low]).mean()),
                }
            )
    return pd.DataFrame(rows)


def tune_lgbm(
    x: pd.DataFrame,
    y: np.ndarray,
    years: np.ndarray,
    base: dict[str, Any],
    trials: int,
    seed: int,
) -> tuple[dict[str, Any], float]:
    """Optuna search over shallow LightGBM settings, scored by 5-fold year-grouped CV RMSE.

    Tuned on the same years it is later scored on, so its scores are an optimistic upper bound.
    """
    folds = list(GroupKFold(n_splits=5).split(x, y, years))

    def objective(trial: optuna.Trial) -> float:
        params = {
            **base,
            "num_leaves": trial.suggest_int("num_leaves", 4, 16),
            "min_child_samples": trial.suggest_int("min_child_samples", 10, 60),
            "learning_rate": trial.suggest_float("learning_rate", 0.01, 0.2, log=True),
            "n_estimators": trial.suggest_int("n_estimators", 20, 600, log=True),
            "reg_lambda": trial.suggest_float("reg_lambda", 1e-3, 30, log=True),
            "colsample_bytree": trial.suggest_float("colsample_bytree", 0.4, 1.0),
        }
        errors = [
            lgb.LGBMRegressor(**params, random_state=seed)
            .fit(x.iloc[tr], y[tr])
            .predict(x.iloc[te])
            - y[te]
            for tr, te in folds
        ]
        return float(np.sqrt(np.mean(np.concatenate(errors) ** 2)))

    optuna.logging.set_verbosity(optuna.logging.WARNING)
    study = optuna.create_study(sampler=optuna.samplers.TPESampler(seed=seed))
    study.optimize(objective, n_trials=trials)
    return {**base, **study.best_params}, float(study.best_value)


def tree_shap(model: lgb.LGBMRegressor, x: pd.DataFrame) -> pd.DataFrame:
    """Per-row SHAP values from shap.TreeExplainer."""
    values = shap.TreeExplainer(model).shap_values(x)
    return pd.DataFrame(values, columns=x.columns, index=x.index)


def linear_shap(model: Pipeline, x: pd.DataFrame) -> pd.DataFrame:
    """Exact SHAP for the standardized Ridge: coefficient times scaled feature."""
    scaled = model.named_steps["standardscaler"].transform(ridge_matrix(x))
    contrib = scaled * model.named_steps["ridgecv"].coef_
    out = pd.DataFrame(contrib, columns=ridge_matrix(x).columns, index=x.index)
    return out


def permutation_shares(
    predict: Callable[[pd.DataFrame], np.ndarray],
    x: pd.DataFrame,
    y: np.ndarray,
    repeats: int,
    seed: int,
) -> pd.Series:
    """Permutation importance: mean rise in MSE when one column is shuffled, as shares.

    Negative rises (shuffling helped) count as 0. Breiman (2001), as in sklearn.inspection.
    """
    rng = np.random.default_rng(seed)
    base = np.mean((y - predict(x)) ** 2)
    rises = {}
    for col in x.columns:
        shuffled = x.copy()
        losses = []
        for _ in range(repeats):
            shuffled[col] = rng.permutation(x[col].to_numpy())
            losses.append(np.mean((y - predict(shuffled)) ** 2) - base)
        rises[col] = max(float(np.mean(losses)), 0.0)
    gain = pd.Series(rises)
    return gain / gain.sum() if gain.sum() > 0 else gain


@dataclass(frozen=True)
class CVSettings:
    """Everything a validation fold needs besides the data."""

    params: dict[str, Any]
    tuned: dict[str, Any]
    alphas: np.ndarray
    quantiles: list[float]
    es_rounds: int
    es_min_trees: int
    es_val_share: float
    bootstrap_runs: int
    seed: int


def quantile_tag(q: float) -> str:
    """Column suffix for a quantile: 0.1 -> q10."""
    return f"q{round(q * 100)}"


def fold_predictions(
    xtr: pd.DataFrame, ytr: np.ndarray, years_tr: np.ndarray, xte: pd.DataFrame, s: CVSettings
) -> dict[str, np.ndarray]:
    """Fit every model on one training fold and predict its held-out rows.

    LightGBM's tree count comes from early stopping inside the training fold; the tuned model
    keeps its Optuna tree count.
    """
    ridge = fit_ridge(xtr, ytr, s.alphas)
    trees = early_stopped_trees(
        xtr, ytr, years_tr, s.params, s.es_rounds, s.es_min_trees, s.es_val_share, s.seed
    )
    tuned_trees = int(s.tuned["n_estimators"])
    preds = {
        "climatology": np.zeros(len(xte)),
        "ridge": ridge.predict(ridge_matrix(xte)),
        "lgbm": fit_lgbm(xtr, ytr, s.params, trees, s.seed).predict(xte),
        "lgbm_tuned": fit_lgbm(xtr, ytr, s.tuned, tuned_trees, s.seed).predict(xte),
    }
    for q in s.quantiles:
        model = fit_lgbm(xtr, ytr, s.params, trees, s.seed, alpha=q)
        preds[f"lgbm_{quantile_tag(q)}"] = model.predict(xte)
    alpha = float(ridge.named_steps["ridgecv"].alpha_)
    boot = bootstrap_interval(xtr, ytr, xte, alpha, s.quantiles, s.bootstrap_runs, s.seed)
    for i, q in enumerate(s.quantiles):
        preds[f"boot_{quantile_tag(q)}"] = boot[:, i]
    return preds


def cross_validate(
    x: pd.DataFrame, y: np.ndarray, years: np.ndarray, groups: np.ndarray, s: CVSettings
) -> pd.DataFrame:
    """Held-out predictions for every model, one fold per value of groups, in x's row order."""
    parts = []
    for group in np.unique(groups):
        te = groups == group
        preds = fold_predictions(x[~te], y[~te], years[~te], x[te], s)
        parts.append(pd.DataFrame(preds, index=x.index[te]))
    return pd.concat(parts).loc[x.index]


def scenario_features(
    anomalies: pd.DataFrame,
    types: pd.Series,
    lga: pd.Series,
    weather: list[str],
    static: pd.DataFrame,
    min_years: int,
) -> pd.DataFrame:
    """Feature vector for every LGA and season type, for scenario predictions.

    Weather is the mean anomaly over the LGA's own years of that type when it has at least
    min_years of them, else the type's statewide mean. Static columns come from the LGA.
    """
    frame = anomalies[weather].assign(lga=lga.to_numpy(), season_type=types.to_numpy())
    frame = frame.dropna(subset=["season_type"])
    own = frame.groupby(["lga", "season_type"])[weather].mean()
    n_years = frame.groupby(["lga", "season_type"]).size().rename("years_of_type")
    state = frame.groupby("season_type")[weather].mean()
    grid = pd.MultiIndex.from_product([static.index, state.index], names=["lga", "season_type"])
    out = pd.DataFrame(index=grid).join(n_years).fillna({"years_of_type": 0}).reset_index()
    use_own = (out["years_of_type"] >= min_years).to_numpy()[:, None]
    own_vals = out[["lga", "season_type"]].join(own, on=["lga", "season_type"])[weather]
    state_vals = out[["season_type"]].join(state, on="season_type")[weather]
    out[weather] = np.where(use_own, own_vals, state_vals)
    out["scenario_source"] = np.where(use_own[:, 0], "lga_years", "statewide_type_mean")
    out = out.join(static, on="lga")
    out["years_of_type"] = out["years_of_type"].astype(int)
    return out


def observed_record(
    target: pd.Series, types: pd.Series, lga: pd.Series, stressed_z: float
) -> pd.DataFrame:
    """Observed greenness per LGA and season type: years, mean z, share of years stressed."""
    frame = pd.DataFrame(
        {"lga": lga.to_numpy(), "season_type": types.to_numpy(), "z": target.to_numpy()}
    ).dropna()
    frame["stressed"] = frame["z"] < stressed_z
    return (
        frame.groupby(["lga", "season_type"])
        .agg(
            observed_years=("z", "size"),
            observed_mean_z=("z", "mean"),
            observed_share_stressed=("stressed", "mean"),
        )
        .reset_index()
    )


def signal_diagnostics(x: pd.DataFrame, y: np.ndarray, years: np.ndarray) -> pd.DataFrame:
    """Where any weather-greenness link lives, per feature.

    r_between_years: correlation of the yearly means (one point per year).
    r_within_year: correlation after removing each year's mean (LGA against LGA).
    loyo_skill: leave-one-year-out RMSE skill of a one-feature linear fit.
    """
    filled = x.fillna(0.0)
    year_means = filled.assign(target=y).groupby(years).mean()
    within = filled - filled.groupby(years).transform("mean")
    y_within = y - pd.Series(y).groupby(years).transform("mean").to_numpy()
    rows = []
    for col in x.columns:
        pred = np.empty(len(y))
        for year in np.unique(years):
            te = years == year
            slope, intercept = np.polyfit(filled.loc[~te, col], y[~te], 1)
            pred[te] = slope * filled.loc[te, col] + intercept
        rows.append(
            {
                "feature": col,
                "r_pooled": float(np.corrcoef(filled[col], y)[0, 1]),
                "r_between_years": float(year_means[[col, "target"]].corr().iloc[0, 1]),
                "r_within_year": float(np.corrcoef(within[col], y_within)[0, 1]),
                "loyo_skill": climatology_rmse_skill(y, pred),
            }
        )
    return pd.DataFrame(rows)


# --------------------------------------------------------------------------- #
# Leakage checks (engineering standards, section 4). Each reruns leave-one-year-out with one
# possible leak removed; if a score moves materially, that leak was inflating it.
# --------------------------------------------------------------------------- #
def skill_vs_climatology(y: np.ndarray, pred: np.ndarray) -> float:
    """1 - RMSE(model) / RMSE(predicting 0, the LGA's normal). Above 0 means skill."""
    return 1.0 - float(np.sqrt(np.mean((pred - y) ** 2)) / np.sqrt(np.mean(y**2)))


def infold_anomaly_cv(
    table: pd.DataFrame,
    raw_target: str,
    weather: list[str],
    static: list[str],
    alphas: np.ndarray,
) -> pd.DataFrame:
    """Leave-one-year-out Ridge where every LGA normal comes from the training years only.

    The shipped pipeline standardizes target and features against each LGA's mean and sd over
    all 25 years, so the held-out year contributes 1/25 of its own normal. Here the normals
    are recomputed inside each fold, so the held-out year never sees itself.
    """
    core = [c for c in weather if c != SMAP]
    years = table["year"].to_numpy()
    parts = []
    for held in np.unique(years):
        tr, te = table[years != held], table[years == held]
        clim = feature_climatology(tr, core)
        stats = tr.groupby("lga")[raw_target].agg(["mean", "std"])
        y_tr, y_te = (
            (
                (d[raw_target] - stats["mean"].reindex(d["lga"]).to_numpy())
                / stats["std"].reindex(d["lga"]).to_numpy()
            ).to_numpy()
            for d in (tr, te)
        )
        model = fit_ridge(to_anomalies(tr, clim, weather, static), y_tr, alphas)
        pred = model.predict(ridge_matrix(to_anomalies(te, clim, weather, static)))
        parts.append(
            pd.DataFrame({"year": held, "lga": te["lga"].to_numpy(), "y": y_te, "ridge": pred})
        )
    return pd.concat(parts, ignore_index=True)


def shuffled_target_skill(
    x: pd.DataFrame, y: np.ndarray, years: np.ndarray, alphas: np.ndarray, runs: int, seed: int
) -> np.ndarray:
    """Leave-one-year-out Ridge skill after shuffling the target, once per run.

    Shuffling breaks every real link between weather and greenness. If the pipeline leaked the
    target into the features, Ridge would still score above 0 on shuffled targets.
    """
    rng = np.random.default_rng(seed)
    skills = np.empty(runs)
    for run in range(runs):
        y_shuffled = rng.permutation(y)
        pred = np.empty_like(y_shuffled)
        for held in np.unique(years):
            te = years == held
            model = fit_ridge(x[~te], y_shuffled[~te], alphas)
            pred[te] = model.predict(ridge_matrix(x[te]))
        skills[run] = skill_vs_climatology(y_shuffled, pred)
    return skills


def nested_tuned_cv(
    x: pd.DataFrame, y: np.ndarray, years: np.ndarray, base: dict[str, Any], trials: int, seed: int
) -> np.ndarray:
    """Leave-one-year-out predictions from LightGBM tuned on the training years only.

    The shipped tuned model is tuned once on all years, then scored on those same years, which
    flatters it. Here Optuna runs again inside every fold and never sees the held-out year.
    """
    pred = np.empty_like(y, dtype=float)
    for held in np.unique(years):
        te = years == held
        params, _ = tune_lgbm(x[~te], y[~te], years[~te], base, trials, seed)
        pred[te] = lgb.LGBMRegressor(**params, random_state=seed).fit(x[~te], y[~te]).predict(x[te])
    return pred
