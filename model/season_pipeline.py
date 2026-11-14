"""A5 pipeline: fit season types on the feature tables and write data/processed/models/a5."""

from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from sklearn.cluster import KMeans

from model import season_types as st
from model.settings import ModelConfig

PROFILE_COLUMNS = [
    "rain_mm",
    "ndvi_crop_sg",
    "power_tmax_mean_c",
    "power_heat_days",
    "water_balance_mm",
]
NONNEGATIVE = ["rain_mm", "power_heat_days"]


def season_type_frame(year: pd.DataFrame, static: pd.DataFrame, cfg: ModelConfig) -> pd.DataFrame:
    """LGA-years with weather anomalies, the greenness check column and the fit flag."""
    cols = list(cfg.season_types.features)
    frame = year[["lga", "year"]].join(st.within_lga_anomalies(year, cols))
    frame = frame.join(year[cols].add_suffix("_value"))
    frame["ndvi_z"] = year[cfg.stress_model.target]
    frame["low_cropland"] = frame["lga"].map(static.set_index("lga")["low_cropland"]).astype(bool)
    frame["complete"] = frame[cols].notna().all(axis=1)
    frame["fit"] = frame["complete"] & ~frame["low_cropland"]
    return frame


def assign_types(
    frame: pd.DataFrame, km: KMeans, names: dict[int, str], cols: list[str], cfg: ModelConfig
) -> pd.DataFrame:
    """Season type, centroid distance, margin and GMM probabilities for every complete row."""
    rows = frame[frame["complete"]].reset_index(drop=True)
    x_all, x_fit = rows[cols].to_numpy(), rows.loc[rows["fit"], cols].to_numpy()
    rows["cluster"] = km.predict(x_all)
    rows["season_type"] = rows["cluster"].map(names)
    dist = km.transform(x_all)
    rows["dist_to_centroid"] = dist[np.arange(len(dist)), rows["cluster"]]
    rows["margin"] = np.sort(dist, axis=1)[:, 1] - rows["dist_to_centroid"]
    proba, gmm_label = st.gmm_probabilities(
        x_fit, x_all, km.predict(x_fit), len(names), cfg.random_seed
    )
    for cluster, name in names.items():
        rows[f"p_{name.lower().replace(' ', '_')}"] = proba[:, cluster].round(3)
    rows["gmm_agrees"] = gmm_label == rows["cluster"].to_numpy()
    rows["stressed"] = rows["ndvi_z"] < cfg.stressed_ndvi_z
    return rows


def summarise(
    selection: pd.DataFrame, types: pd.DataFrame, k: int, p_value: float
) -> dict[str, Any]:
    """Scores for the chosen k plus the greenness check, for a5_summary.json and the log."""
    fitted = types[~types["low_cropland"]]
    chosen = selection.set_index("k").loc[k]
    stressed = fitted.groupby("season_type")["stressed"].mean()
    return {
        "rows_fit": int(len(fitted)),
        "rows_assigned": int(len(types)),
        "k": k,
        "k_best_silhouette": int(selection.loc[selection["silhouette"].idxmax(), "k"]),
        "silhouette": round(float(chosen["silhouette"]), 3),
        "davies_bouldin": round(float(chosen["davies_bouldin"]), 3),
        "seed_ari_min": round(float(chosen["seed_ari_min"]), 3),
        "gmm_k_best_bic": int(selection.loc[selection["gmm_bic"].idxmin(), "k"]),
        "gmm_agreement": round(float(fitted["gmm_agrees"].mean()), 3),
        "types": {k_: int(v) for k_, v in fitted["season_type"].value_counts().items()},
        "stressed_share_by_type": stressed.round(3).to_dict(),
        "stressed_share_gap": round(float(stressed.max() - stressed.min()), 3),
        "stressed_gap_p_year_block": round(p_value, 3),
    }


def write_tables(
    types: pd.DataFrame, centroids: pd.DataFrame, names: dict[int, str], out: Path
) -> None:
    """Types per LGA-year, centroids with their greenness, LGA shares and the yearly summary."""
    types.drop(columns=["complete", "fit"]).round(4).to_csv(
        out / "a5_lga_year_types.csv", index=False
    )
    fitted = types[~types["low_cropland"]]
    stats = fitted.groupby("cluster").agg(
        share_of_years=("year", "size"),
        ndvi_z_mean=("ndvi_z", "mean"),
        share_stressed=("stressed", "mean"),
    )
    stats["share_of_years"] /= len(fitted)
    stats = stats.join(fitted.groupby("cluster")[list(fitted.filter(like="_value"))].mean())
    cent = centroids.assign(season_type=[names[i] for i in centroids.index]).join(stats)
    cent.round(3).to_csv(out / "a5_centroids.csv", index_label="cluster")
    shares = pd.crosstab(types["lga"], types["season_type"], normalize="index").round(3)
    shares.reset_index().to_csv(out / "a5_lga_type_share.csv", index=False)
    yearly = fitted.groupby("year").agg(
        statewide_type=("season_type", lambda s: s.mode().iloc[0]),
        share_of_lgas=("season_type", lambda s: s.value_counts(normalize=True).iloc[0]),
        mean_ndvi_z=("ndvi_z", "mean"),
    )
    yearly.round(2).reset_index().to_csv(out / "a5_year_summary.csv", index=False)


def write_profiles_and_selection(
    types: pd.DataFrame, month: pd.DataFrame, selection: pd.DataFrame, cfg: ModelConfig, out: Path
) -> None:
    """Monthly profiles per LGA and type (for the season strip) and the k selection table."""
    sc = cfg.season_types
    profiles = st.monthly_profiles(month, types, PROFILE_COLUMNS, sc.profile_min_years)
    profiles[NONNEGATIVE] = profiles[NONNEGATIVE].clip(lower=0)
    profiles.round(3).to_csv(out / "a5_monthly_profiles.csv", index=False)
    selection.assign(chosen=selection["k"] == sc.k).round(4).to_csv(
        out / "a5_model_selection.csv", index=False
    )
