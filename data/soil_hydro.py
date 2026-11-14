#!/usr/bin/env python3
"""
Soil water properties, hydrologic soil group and erodibility per Kaduna LGA.

PRD A8 (soil water properties) and the soil half of A10 (erosion and
infiltration index), computed from the iSDAsoil cropland statistics that
data/isda.py already produced. No downloads.

Input   : data/raw/isda/isda_lga_stats_cropland.csv
          (per LGA, cropland only, real units: sand/silt/clay %, organic carbon
          g/kg, bulk density g/cm3, stone %, bedrock depth cm; depths 0-20 and
          20-50 cm; spatial mean plus p10/p50/p90 within the LGA)
Output  : data/processed/soil/lga_soil_hydro.csv, one row per LGA

Methods
  Soil water (A8): Saxton & Rawls (2006), Soil Sci. Soc. Am. J. 70:1569-1578,
    equations 1-5 and 15-16 with the bulk-density adjustment (eqs. 6-9), so
    compaction measured by iSDA lowers porosity and conductivity:
      wilting point (1500 kPa), field capacity (33 kPa), saturation,
      available water capacity (mm per m), saturated conductivity Ksat (mm/h).
    Organic matter = organic carbon x 1.724 (van Bemmelen), capped at 8% as
    the equations require.
  Hydrologic soil group: USDA NRCS National Engineering Handbook Part 630,
    Chapter 7 (2009), Table 7-1. Uses the least permeable of the two iSDA
    layers and iSDA bedrock depth as the depth to an impermeable layer.
    Water-table criteria are not applied (no water-table data).
      bedrock < 50 cm            -> D
      bedrock 50-100 cm: Ksat > 40 um/s A, 10-40 B, 1-10 C, <= 1 D
      bedrock > 100 cm : Ksat > 10 um/s A, 4-10  B, 0.4-4 C, <= 0.4 D
  Erodibility K (USLE/RUSLE K factor): Williams (1995) EPIC equation, from
    sand, silt, clay and organic carbon of the 0-20 cm layer. It is the
    standard substitute for the Wischmeier nomograph when very fine sand is
    not measured (as in iSDA). k_factor is in EPIC/SWAT units
    (0.013 t m2 h / (m3 t cm)); k_factor_si = x 0.1317, in t ha h / (ha MJ mm).
    Williams' K runs lower than the nomograph, so k_class ranks LGAs within
    Kaduna (lower / middle / higher third) instead of using absolute cut-offs.

Sensitivity, not percentiles
  Columns ending _p10 / _p50 / _p90 are the outputs recomputed from each input
  property's within-LGA p10 / p50 / p90 (sand p10 with clay p10, and so on).
  They are NOT percentiles of the output, since the inputs' extremes do not
  occur together; read them only as a rough sensitivity for the A12
  confidence aggregator.

Finding on Kaduna (iSDA cropland, Nov 2026)
  All 23 LGAs fall in hydrologic group B (Ksat ~16-28 mm/h, bedrock deeper
  than iSDA's 200 cm limit). K differs by only ~10% between LGAs: siltier
  northern LGAs (Kudan, Sabon Gari, Zaria) are the most erodible, the more
  organic, clay-richer south (Kaura, Zangon Kataf) the least. So the soil term
  separates LGAs only weakly; erodibility_rel keeps it on its true scale.

Run (from the repo root)
  python -m data.soil_hydro
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

import numpy as np
import pandas as pd

log = logging.getLogger("soil_hydro")

REPO_ROOT = Path(__file__).resolve().parents[1]
DEPTHS = ["0_20", "20_50"]
OC_TO_OM = 1.724
ENGINE_TOPSOIL = ("sand_content", "nitrogen_total", "carbon_organic")


def col(prop, depth, stat):
    return f"{prop}__{depth}__value_{stat}"


# --------------------------------------------------------------------------- #
# Soil water: Saxton and Rawls, 2006
# --------------------------------------------------------------------------- #
def saxton_rawls(sand_pct, clay_pct, oc_gkg, bd_gcm3):
    """Returns dict of wp, fc, sat (m3/m3), awc_mm_per_m, ksat_mm_h."""
    S = np.asarray(sand_pct, float) / 100  # noqa: N806 - paper notation
    C = np.asarray(clay_pct, float) / 100  # noqa: N806 - paper notation
    OM = np.clip(np.asarray(oc_gkg, float) / 10 * OC_TO_OM, 0, 8)  # noqa: N806 - % by weight, paper notation
    bd = np.asarray(bd_gcm3, float)

    t1500 = (
        -0.024 * S
        + 0.487 * C
        + 0.006 * OM
        + 0.005 * S * OM
        - 0.013 * C * OM
        + 0.068 * S * C
        + 0.031
    )
    wp = t1500 + (0.14 * t1500 - 0.02)

    t33 = (
        -0.251 * S
        + 0.195 * C
        + 0.011 * OM
        + 0.006 * S * OM
        - 0.027 * C * OM
        + 0.452 * S * C
        + 0.299
    )
    fc = t33 + (1.283 * t33**2 - 0.374 * t33 - 0.015)

    ts33 = (
        0.278 * S + 0.034 * C + 0.022 * OM - 0.018 * S * OM - 0.027 * C * OM - 0.584 * S * C + 0.078
    )
    s33 = ts33 + (0.636 * ts33 - 0.107)
    sat = fc + s33 - 0.097 * S + 0.043

    # Density adjustment (eqs. 6-9): iSDA bulk density replaces normal density
    sat_df = 1 - bd / 2.65
    fc_df = fc - 0.2 * (sat - sat_df)
    wp_df = wp  # 1500 kPa water is not density-adjusted
    fc_df = np.maximum(fc_df, wp_df + 0.01)
    sat_df = np.maximum(sat_df, fc_df + 0.01)

    B = (np.log(1500) - np.log(33)) / (np.log(fc_df) - np.log(wp_df))  # noqa: N806 - paper notation
    lam = 1 / B
    ksat = 1930 * (sat_df - fc_df) ** (3 - lam)

    return {
        "wp": wp_df,
        "fc": fc_df,
        "sat": sat_df,
        "awc_mm_per_m": (fc_df - wp_df) * 1000,
        "ksat_mm_h": ksat,
    }


# --------------------------------------------------------------------------- #
# Hydrologic soil group, NEH 630 Ch. 7 Table 7-1
# --------------------------------------------------------------------------- #
def hydrologic_group(ksat_mm_h, bedrock_cm):
    k = np.asarray(ksat_mm_h, float) / 3.6  # mm/h -> um/s
    d = np.asarray(bedrock_cm, float)
    out = np.empty(k.shape, dtype=object)
    shallow, mid = d < 50, (d >= 50) & (d <= 100)
    deep = ~shallow & ~mid
    out[shallow] = "D"
    out[mid] = np.select([k[mid] > 40, k[mid] > 10, k[mid] > 1], ["A", "B", "C"], "D")
    out[deep] = np.select([k[deep] > 10, k[deep] > 4, k[deep] > 0.4], ["A", "B", "C"], "D")
    return out


# --------------------------------------------------------------------------- #
# Erodibility, Williams (1995) EPIC
# --------------------------------------------------------------------------- #
def k_factor_williams(sand_pct, silt_pct, clay_pct, oc_gkg):
    san = np.asarray(sand_pct, float)
    sil = np.asarray(silt_pct, float)
    cla = np.asarray(clay_pct, float)
    c = np.asarray(oc_gkg, float) / 10  # organic carbon, %
    f_csand = 0.2 + 0.3 * np.exp(-0.256 * san * (1 - sil / 100))
    f_clsi = (sil / (cla + sil)) ** 0.3
    f_orgc = 1 - 0.25 * c / (c + np.exp(3.72 - 2.95 * c))
    sn1 = 1 - san / 100
    f_hisand = 1 - 0.7 * sn1 / (sn1 + np.exp(-5.51 + 22.9 * sn1))
    return f_csand * f_clsi * f_orgc * f_hisand


US_TO_SI = 0.1317  # K in EPIC/SWAT units (0.013 t m2 h / (m3 t cm)) -> t ha h / (ha MJ mm)


def k_class(k):
    """Relative erodibility within Kaduna (terciles). Williams' K runs lower than
    the Wischmeier nomograph, so fixed absolute cut-offs would mislead; ranking
    LGAs against each other is what the A10 index needs."""
    lo, hi = np.nanquantile(k, [1 / 3, 2 / 3])
    return np.select([k <= lo, k <= hi], ["lower", "middle"], "higher")


# --------------------------------------------------------------------------- #
# Build
# --------------------------------------------------------------------------- #
def build(stats, bedrock_stat_col):
    out = pd.DataFrame({"lga": stats["lga"]})
    for stat in ("mean", "p10", "p50", "p90"):
        layers = {}
        for depth in DEPTHS:
            layers[depth] = saxton_rawls(
                stats[col("sand_content", depth, stat)],
                stats[col("clay_content", depth, stat)],
                stats[col("carbon_organic", depth, stat)],
                stats[col("bulk_density", depth, stat)],
            )
        sfx = "" if stat == "mean" else f"_{stat}"
        for depth in DEPTHS:
            for k in ("wp", "fc", "sat", "awc_mm_per_m", "ksat_mm_h"):
                if stat == "mean" or k in ("awc_mm_per_m", "ksat_mm_h"):
                    out[f"{k}_{depth}cm{sfx}"] = layers[depth][k]
        # profile AWC to 50 cm, mm of water the top half-metre can hold for crops
        if stat == "mean":
            out["awc_0_50cm_mm"] = (
                layers["0_20"]["awc_mm_per_m"] * 0.20 + layers["20_50"]["awc_mm_per_m"] * 0.30
            )
        kmin = np.minimum(layers["0_20"]["ksat_mm_h"], layers["20_50"]["ksat_mm_h"])
        out[f"ksat_min_mm_h{sfx}"] = kmin
        out[f"k_factor{sfx}"] = k_factor_williams(
            stats[col("sand_content", "0_20", stat)],
            stats[col("silt_content", "0_20", stat)],
            stats[col("clay_content", "0_20", stat)],
            stats[col("carbon_organic", "0_20", stat)],
        )

    out["bedrock_depth_cm"] = stats[bedrock_stat_col]
    out["hydrologic_group"] = hydrologic_group(out["ksat_min_mm_h"], out["bedrock_depth_cm"])
    out["hydrologic_group_p10"] = hydrologic_group(
        out["ksat_min_mm_h_p10"], out["bedrock_depth_cm"]
    )
    out["k_factor_si"] = out["k_factor"] * US_TO_SI
    out["k_class"] = k_class(out["k_factor"])
    # Erodibility relative to the Kaduna average (1.0 = average). Deliberately NOT
    # min-max scaled: across LGAs K differs by only ~10%, about the same as the
    # spread inside one LGA, and stretching that to 0-1 would let soil dominate the
    # A10 index. Slope (SRTM) and rain intensity (IMERG) should carry most of A10.
    out["erodibility_rel"] = out["k_factor"] / out["k_factor"].mean()
    # Topsoil means the rotation engine reads for Striga risk and LGA need. Carried through
    # under their iSDA names so the engine never reads data/raw, which CI does not have.
    for prop in ENGINE_TOPSOIL:
        out[col(prop, "0_20", "mean")] = stats[col(prop, "0_20", "mean")]
    return out


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--stats", default=str(REPO_ROOT / "data/raw/isda/isda_lga_stats_cropland.csv"))
    ap.add_argument("--out", default=str(REPO_ROOT / "data/processed/soil/lga_soil_hydro.csv"))
    args = ap.parse_args()

    stats = pd.read_csv(args.stats)
    need = [
        col(p, d, s)
        for p in ("sand_content", "silt_content", "clay_content", "carbon_organic", "bulk_density")
        for d in DEPTHS
        for s in ("mean", "p10", "p50", "p90")
    ] + [col(p, "0_20", "mean") for p in ENGINE_TOPSOIL]
    missing = [c for c in need if c not in stats.columns]
    if missing:
        sys.exit(f"Missing columns in {args.stats}: {missing[:6]}...")
    bedrock = next(
        (c for c in stats.columns if c.startswith("bedrock_depth") and c.endswith("value_mean")),
        None,
    )
    if bedrock is None:
        sys.exit("No bedrock_depth ... value_mean column found")

    out = build(stats, bedrock)
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    out.round(4).to_csv(args.out, index=False)

    show = out[
        [
            "lga",
            "awc_0_50cm_mm",
            "ksat_min_mm_h",
            "bedrock_depth_cm",
            "hydrologic_group",
            "k_factor",
            "k_class",
        ]
    ].sort_values("k_factor", ascending=False)
    log.info(show.to_string(index=False, float_format=lambda v: f"{v:.3f}"))
    log.info(f"\nHydrologic groups: {out['hydrologic_group'].value_counts().to_dict()}")
    log.info(f"Wrote {len(out)} LGAs to {args.out}")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    main()
