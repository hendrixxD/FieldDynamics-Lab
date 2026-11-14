"""A5. Season-type clustering: group LGA-years into named season types.

Every feature is an anomaly against the LGA's own mean, so a "late start" is late for that LGA;
raw values would only rediscover north versus south. K-means is the main model and a Gaussian
mixture the sanity check (engineering standards, section 4). Settings: config/models.yaml.
"""

import numpy as np
import pandas as pd
from sklearn.cluster import KMeans
from sklearn.metrics import adjusted_rand_score, davies_bouldin_score, silhouette_score
from sklearn.mixture import GaussianMixture


def within_lga_anomalies(df: pd.DataFrame, columns: list[str]) -> pd.DataFrame:
    """Z-score each column within its LGA: (x - LGA mean) / LGA sd.

    A column with no variation in an LGA (no 10-day dry spell in any year) gets 0, not NaN.
    Returns the same index as df, with the anomaly columns only.
    """
    grouped = df.groupby("lga")[columns]
    sd = grouped.transform("std").replace(0, np.nan)
    z = (df[columns] - grouped.transform("mean")) / sd
    no_variation = sd.isna() & df[columns].notna()
    return z.mask(no_variation, 0.0)


def score_k(
    x: np.ndarray, k_candidates: list[int], n_init: int, seed: int, stability_seeds: int
) -> pd.DataFrame:
    """Silhouette, Davies-Bouldin, seed stability and GMM BIC for each candidate k.

    Stability is the adjusted Rand index between the seed-0 labels and each other seed.
    """
    rows = []
    for k in k_candidates:
        labels = [
            KMeans(k, n_init=n_init, random_state=seed + s).fit_predict(x)
            for s in range(stability_seeds)
        ]
        ari = [adjusted_rand_score(labels[0], other) for other in labels[1:]]
        gmm = GaussianMixture(k, covariance_type="full", n_init=5, random_state=seed).fit(x)
        rows.append(
            {
                "k": k,
                "silhouette": silhouette_score(x, labels[0]),
                "davies_bouldin": davies_bouldin_score(x, labels[0]),
                "seed_ari_min": float(np.min(ari)),
                "seed_ari_mean": float(np.mean(ari)),
                "smallest_cluster_share": float(np.bincount(labels[0]).min() / len(x)),
                "gmm_bic": gmm.bic(x),
            }
        )
    return pd.DataFrame(rows)


def name_clusters(centroids: pd.DataFrame, min_z: float) -> dict[int, str]:
    """Name clusters from centroids given in within-LGA z units.

    The centroid nearest zero is Typical. Each other name claims the free cluster that shows its
    trait most, if by at least min_z. Unclaimed clusters are "Type <n>".
    """
    c = centroids
    traits = {
        "Late start": c["a2_onset_doy"] - 0.5 * c["a2_length_days"],
        "Dry spell": c["a2_longest_dry_spell_days"] + c["a2_dry_spells_10plus"],
        "Wet": c["season_rain_mm"],
        "Short and dry": -c["a2_length_days"] - c["season_rain_mm"],
        "Early start": -c["a2_onset_doy"] + 0.5 * c["a2_length_days"],
    }
    names = {int((c**2).sum(axis=1).idxmin()): "Typical"}
    for label, score in traits.items():
        free = score.drop(index=list(names), errors="ignore")
        if not free.empty and free.max() >= min_z:
            names[int(free.idxmax())] = label
    return {int(i): names.get(int(i), f"Type {i}") for i in c.index}


def gmm_probabilities(
    x_fit: np.ndarray, x_all: np.ndarray, kmeans_labels: np.ndarray, k: int, seed: int
) -> tuple[np.ndarray, np.ndarray]:
    """Fit a k-component GMM and express its membership as K-means cluster probabilities.

    Each GMM component maps to the K-means cluster it overlaps most. Returns the probability
    matrix (rows of x_all, one column per K-means cluster) and the GMM hard label mapped the same
    way, so agreement with K-means can be measured.
    """
    gmm = GaussianMixture(k, covariance_type="full", n_init=10, random_state=seed).fit(x_fit)
    fit_comp = gmm.predict(x_fit)
    overlap = pd.crosstab(fit_comp, kmeans_labels)
    comp_to_cluster = overlap.idxmax(axis=1).reindex(range(k)).fillna(-1).astype(int).to_numpy()
    proba_comp = gmm.predict_proba(x_all)
    proba = np.zeros((len(x_all), k))
    for comp in range(k):
        if comp_to_cluster[comp] >= 0:
            proba[:, comp_to_cluster[comp]] += proba_comp[:, comp]
    return proba, comp_to_cluster[gmm.predict(x_all)]


def stressed_share_gap(labels: np.ndarray, stressed: np.ndarray) -> float:
    """Largest minus smallest share of stressed years across season types."""
    shares = pd.Series(stressed, dtype=float).groupby(labels).mean()
    return float(shares.max() - shares.min())


def year_block_p(types: pd.DataFrame, runs: int, seed: int) -> float:
    """Permutation p-value for the stressed-share gap between types.

    LGAs in the same year share weather, so they are not independent. Each permutation moves a
    whole year's labels onto another year's greenness. types needs year, lga, season_type and a
    boolean stressed column, with every LGA present in every year.
    """
    labels = types.pivot(index="year", columns="lga", values="season_type")
    stressed = types.pivot(index="year", columns="lga", values="stressed").astype(float)
    observed = stressed_share_gap(labels.to_numpy().ravel(), stressed.to_numpy().ravel())
    rng = np.random.default_rng(seed)
    null = np.array(
        [
            stressed_share_gap(
                labels.to_numpy()[rng.permutation(len(labels))].ravel(), stressed.to_numpy().ravel()
            )
            for _ in range(runs)
        ]
    )
    return float(np.mean(null >= observed))


def monthly_profiles(
    month: pd.DataFrame, types: pd.DataFrame, value_columns: list[str], min_years: int
) -> pd.DataFrame:
    """Mean of each value column per LGA, season type and calendar month.

    Where an LGA has fewer than min_years of a type, the profile is the LGA's own all-years
    month mean plus the type's statewide departure from the statewide month mean.
    """
    m = month.merge(types[["lga", "year", "season_type"]], on=["lga", "year"])
    keys = ["lga", "season_type", "month"]
    own = m.groupby(keys)[value_columns].mean()
    years = m.groupby(keys)["year"].nunique().rename("n_years")
    grid = pd.MultiIndex.from_product(
        [sorted(m.lga.unique()), sorted(m.season_type.unique()), range(1, 13)], names=keys
    )
    out = pd.DataFrame(index=grid).join(own).join(years).fillna({"n_years": 0}).reset_index()
    shift = (
        m.groupby(["season_type", "month"])[value_columns].mean()
        - m.groupby("month")[value_columns].mean()
    )
    lga_month = m.groupby(["lga", "month"])[value_columns].mean()
    fallback = (
        out[keys].join(lga_month, on=["lga", "month"])[value_columns]
        + out[keys].join(shift, on=["season_type", "month"])[value_columns]
    )
    use_own = out["n_years"] >= min_years
    out[value_columns] = out[value_columns].where(use_own, fallback)
    out["source"] = np.where(use_own, "lga", "statewide_shift")
    out["n_years"] = out["n_years"].astype(int)
    return out
