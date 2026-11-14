"""A6 pipeline: validate the stress models, apply the honesty rule, write the a6 outputs."""

import logging
from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd

from model import stress
from model.settings import ModelConfig

log = logging.getLogger(__name__)
SCHEMES = ("leave_one_year_out", "senatorial_zone")


@dataclass
class StressData:
    """The full LGA-year table, its feature anomalies, and the training subset."""

    table: pd.DataFrame
    x_all: pd.DataFrame
    train: pd.DataFrame
    x: pd.DataFrame
    y: np.ndarray


def stress_table(
    year: pd.DataFrame, month: pd.DataFrame, static: pd.DataFrame, cfg: ModelConfig
) -> pd.DataFrame:
    """LGA-years with window sums, mid-season Tmax, static columns and zone."""
    sm = cfg.stress_model
    windows = {
        name: stress.window_sums(month, w.column, w.months) for name, w in sm.windows.items()
    }
    mid = month[month["month"].isin(sm.tmax_mid_months)]
    windows["tmax_mid_c"] = mid.groupby(["lga", "year"])["power_tmax_mean_c"].mean()
    df = year.join(pd.DataFrame(windows), on=["lga", "year"])
    df = df.merge(static[["lga", "low_cropland", *sm.static_features]], on="lga", how="left")
    df["zone"] = df["lga"].map(cfg.lga_zone)
    return df


def prepare(
    year: pd.DataFrame, month: pd.DataFrame, static: pd.DataFrame, cfg: ModelConfig
) -> StressData:
    """Anomaly features for every LGA-year; training rows have a target, cropland and weather."""
    sm = cfg.stress_model
    weather, core = list(sm.weather_features), [c for c in sm.weather_features if c != stress.SMAP]
    df = stress_table(year, month, static, cfg)
    clim = stress.feature_climatology(df, core)
    x_all = stress.to_anomalies(df, clim, weather, list(sm.static_features))
    keep = (
        df[sm.target].notna() & ~df["low_cropland"].astype(bool) & x_all[core].notna().all(axis=1)
    )
    train = df[keep.to_numpy()].reset_index(drop=True)
    x = x_all[keep.to_numpy()].reset_index(drop=True)
    return StressData(df, x_all, train, x, train[sm.target].to_numpy())


def cv_settings(cfg: ModelConfig, tuned: dict[str, Any]) -> stress.CVSettings:
    """Fold settings from config/models.yaml plus the Optuna result."""
    sm = cfg.stress_model
    grid = sm.ridge_alphas
    return stress.CVSettings(
        params=dict(sm.lightgbm),
        tuned=tuned,
        alphas=np.logspace(grid.log10_min, grid.log10_max, grid.count),
        quantiles=sm.quantiles,
        es_rounds=sm.early_stopping_rounds,
        es_min_trees=sm.early_stopping_min_trees,
        es_val_share=sm.early_stopping_year_share,
        bootstrap_runs=sm.bootstrap_runs,
        seed=cfg.random_seed,
    )


def validate(data: StressData, s: stress.CVSettings) -> pd.DataFrame:
    """Held-out predictions under leave-one-year-out and senatorial-zone folds."""
    frames = []
    for scheme, column in zip(SCHEMES, ("year", "zone"), strict=True):
        log.info("A6 cross-validation: %s", scheme)
        years = data.train["year"].to_numpy()
        groups = data.train[column].to_numpy()
        preds = stress.cross_validate(data.x, data.y, years, groups, s)
        keys = data.train[["lga", "year", "zone"]].assign(scheme=scheme, target=data.y)
        frames.append(pd.concat([keys, preds], axis=1))
    return pd.concat(frames, ignore_index=True)


def summarise_cv(cv: pd.DataFrame, quantiles: list[float]) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Model metrics and interval coverage from held-out predictions."""
    metrics = stress.score_predictions(cv, ["climatology", "ridge", "lgbm", "lgbm_tuned"])
    low, high = (stress.quantile_tag(q) for q in quantiles)
    loyo = cv["scheme"] == SCHEMES[0]
    resid_low, resid_high = np.quantile(cv.loc[loyo, "target"] - cv.loc[loyo, "ridge"], quantiles)
    cv = cv.assign(resid_low=cv["ridge"] + resid_low, resid_high=cv["ridge"] + resid_high)
    intervals = {
        "lgbm_quantile": (f"lgbm_{low}", f"lgbm_{high}"),
        "ridge_bootstrap": (f"boot_{low}", f"boot_{high}"),
        "ridge_loyo_residual": ("resid_low", "resid_high"),
    }
    return metrics, stress.interval_coverage(cv, intervals)


def ship_decision(metrics: pd.DataFrame, min_skill: float) -> dict[str, Any]:
    """Honesty rule: LightGBM ships only if it beats Ridge in both schemes, and the shipped model
    counts as skilful only if it beats climatology by more than min_skill in both."""
    rmse = metrics.set_index(["scheme", "model"])["rmse"]
    skill = metrics.set_index(["scheme", "model"])["skill_vs_climatology"]
    lgbm_wins = all(rmse[(s, "lgbm")] < rmse[(s, "ridge")] for s in SCHEMES)
    shipped = "lgbm" if lgbm_wins else "ridge"
    has_skill = all(skill[(s, shipped)] > min_skill for s in SCHEMES)
    return {
        "shipped": shipped,
        "lgbm_beats_ridge": lgbm_wins,
        "has_skill": has_skill,
        "skill_loyo": round(float(skill[(SCHEMES[0], shipped)]), 3),
        "skill_zone": round(float(skill[(SCHEMES[1], shipped)]), 3),
        "card_source": "a6_model" if has_skill else "weather_risk_a2_a9",
    }


def final_models(data: StressData, s: stress.CVSettings, shipped: str) -> dict[str, Any]:
    """Ridge and LightGBM refit on every training row; shipped names the one cards would use."""
    years = data.train["year"].to_numpy()
    trees = stress.early_stopped_trees(
        data.x, data.y, years, s.params, s.es_rounds, s.es_min_trees, s.es_val_share, s.seed
    )
    return {
        "ridge": stress.fit_ridge(data.x, data.y, s.alphas),
        "lgbm": stress.fit_lgbm(data.x, data.y, s.params, trees, s.seed),
        "shipped": shipped,
    }


def predict_with(final: dict[str, Any], x: pd.DataFrame) -> np.ndarray:
    """Predictions from the shipped model."""
    if final["shipped"] == "lgbm":
        return np.asarray(final["lgbm"].predict(x))
    return np.asarray(final["ridge"].predict(stress.ridge_matrix(x)))


def shap_shares(values: pd.DataFrame) -> pd.Series:
    """Mean absolute SHAP per feature, as shares of the total."""
    mean_abs = values.abs().mean()
    return mean_abs / mean_abs.sum()


def importance(final: dict[str, Any], data: StressData, cfg: ModelConfig) -> pd.DataFrame:
    """SHAP shares for both models and permutation shares for the shipped one."""
    sm = cfg.stress_model
    perm = stress.permutation_shares(
        lambda d: predict_with(final, d), data.x, data.y, sm.permutation_repeats, cfg.random_seed
    )
    table = pd.DataFrame(
        {
            "label": pd.Series(sm.labels),
            "ridge_shap_share": shap_shares(stress.linear_shap(final["ridge"], data.x)),
            "lgbm_shap_share": shap_shares(stress.tree_shap(final["lgbm"], data.x)),
            "shipped_permutation_share": perm,
        }
    ).loc[list(data.x.columns)]
    return table.sort_values(f"{final['shipped']}_shap_share", ascending=False)


def lga_drivers(final: dict[str, Any], data: StressData, labels: dict[str, str]) -> pd.DataFrame:
    """Top three SHAP drivers per LGA for the shipped model, with their shares."""
    sv = (
        stress.tree_shap(final["lgbm"], data.x)
        if final["shipped"] == "lgbm"
        else stress.linear_shap(final["ridge"], data.x)
    )
    per_lga = sv.abs().groupby(data.train["lga"].to_numpy()).mean()
    per_lga = per_lga.div(per_lga.sum(axis=1), axis=0)
    ranked = np.argsort(-per_lga.to_numpy(), axis=1)[:, :3]
    rows = np.arange(len(per_lga))
    drivers = pd.DataFrame(index=per_lga.index)
    for i in range(3):
        drivers[f"driver_{i + 1}"] = [labels[c] for c in per_lga.columns[ranked[:, i]]]
        drivers[f"driver_{i + 1}_share"] = per_lga.to_numpy()[rows, ranked[:, i]].round(3)
    return drivers


SCENARIO_COLUMNS = [
    "lga",
    "season_type",
    "years_of_type",
    "scenario_source",
    "expected_stress_z",
    "range_low_z",
    "range_high_z",
    "observed_years",
    "observed_mean_z",
    "observed_share_stressed",
    "low_cropland",
    "model",
    "has_skill",
    "card_source",
]


def scenarios(
    data: StressData,
    types: pd.DataFrame,
    final: dict[str, Any],
    resid: tuple[float, float],
    decision: dict[str, Any],
    cfg: ModelConfig,
) -> pd.DataFrame:
    """Expected stress and range per LGA and season type, beside the observed record."""
    sm = cfg.stress_model
    weather, static_cols = list(sm.weather_features), list(sm.static_features)
    df = data.table
    season = df[["lga", "year"]].merge(types[["lga", "year", "season_type"]], how="left")
    static = df.groupby("lga")[static_cols].first()
    scen = stress.scenario_features(
        data.x_all, season["season_type"], df["lga"], weather, static, sm.scenario_min_years
    )
    scen["expected_stress_z"] = predict_with(final, scen[weather + static_cols])
    scen["range_low_z"] = scen["expected_stress_z"] + resid[0]
    scen["range_high_z"] = scen["expected_stress_z"] + resid[1]
    record = stress.observed_record(
        df[sm.target], season["season_type"], df["lga"], cfg.stressed_ndvi_z
    )
    scen = scen.merge(record, on=["lga", "season_type"], how="left")
    scen["low_cropland"] = scen["lga"].map(df.groupby("lga")["low_cropland"].first())
    scen = scen.assign(
        model=decision["shipped"],
        has_skill=decision["has_skill"],
        card_source=decision["card_source"],
    )
    return scen[SCENARIO_COLUMNS]


def residual_range(cv: pd.DataFrame, model: str, quantiles: list[float]) -> tuple[float, float]:
    """10th and 90th percentile of the model's leave-one-year-out errors."""
    loyo = cv[cv["scheme"] == SCHEMES[0]]
    low, high = np.quantile(loyo["target"] - loyo[model], quantiles)
    return float(low), float(high)


def summarise(
    data: StressData,
    decision: dict[str, Any],
    tuned: tuple[dict[str, Any], float],
    final: dict[str, Any],
    resid: tuple[float, float],
    diag: pd.DataFrame,
    params: dict[str, Any],
) -> dict[str, Any]:
    """Headline numbers for a6_summary.json."""
    changed = {k: v for k, v in tuned[0].items() if params.get(k) != v}
    return {
        "rows": len(data.y),
        "lgas": int(data.train["lga"].nunique()),
        **decision,
        "optuna_cv_rmse_optimistic": round(tuned[1], 4),
        "tuned_params": changed,
        "ridge_alpha": float(final["ridge"].named_steps["ridgecv"].alpha_),
        "residual_range": [round(resid[0], 3), round(resid[1], 3)],
        "max_abs_r_between_years": round(float(diag["r_between_years"].abs().max()), 3),
        "max_abs_r_within_year": round(float(diag["r_within_year"].abs().max()), 3),
        "best_single_feature_loyo_skill": round(float(diag["loyo_skill"].max()), 3),
    }
