#!/usr/bin/env python3
"""
Feature join (PRD A1 output): every processed source in one place, per LGA.

Reads data/processed/** (built by the source scripts) and writes
data/processed/features/:

  lga_month_features.parquet  one row per LGA x month, 1981-01 to latest.
                              PRD's lga_month_features.parquet: the input to
                              A3 indicators, A5 clusters and A6 stress model.
  lga_year_features.parquet   one row per LGA x year: A2 season metrics, season
                              aggregates of every source, and the A6 target
                              (Aug-Sep cropland NDVI anomaly). The A5/A6 table.
  lga_static.parquet          one row per LGA: cropland, terrain, soil, rain
                              climatology, A2 normals, 12-month bare probability.
  data_dictionary.csv         every column: table, source, unit, meaning.
  *.csv                       CSV copies of the three tables, for quick looks.

Rules applied (from the data review, 14 November 2026)
  rainfall      rain_mm is CHIRPS everywhere (1981 on, gauge-blended, consistent
                with A2 onset). IMERG adds intensity and extremes from 2000.
                POWER rainfall is never used (spikes above 1,000 mm/day).
  urban LGAs    Kaduna North and Kaduna South are flagged urban_lga and
                low_cropland. Their IMERG values are flagged imerg_suspect
                (27-39% above CHIRPS; one 11 km pixel dominates).
  SMAP          days inside the 2019 and 2022 SMAP outages are counted as
                smap_model_only_days (land-model values, no satellite input).
  POWER solar   power_rs_filled_days > 0 marks months (1981-83) whose solar
                radiation and so ET0 were filled with FAO-56 eq. 50.
  MODIS ET      kept for anomalies only (absolute ET reads low for this
                savanna); et_ratio_anom is the within-LGA, same-month z-score.
  MODIS LST     values below 20 C by day or 5 C by night are cloud residue
                and set to missing.
  NDVI 2025-26  MODIS peak NDVI jumps ~+0.025 from 2025 while VIIRS and 30 m
                HLS stay flat: Terra orbit drift. Those years are
                target_suspect; target_train_z (the A6 training target) is
                blank for them and uses a 2000-2024 baseline. Note the target's
                year-to-year signal is small (~0.02 NDVI) and MODIS/VIIRS
                anomalies agree only at r~0.27 (2012-2022): A6 is indicative.

Run (from the repo root)
  python -m data.features_join
"""

from __future__ import annotations

import argparse
import datetime as dt
import logging
import re
import sys
from pathlib import Path

import numpy as np
import pandas as pd

log = logging.getLogger("features_join")

REPO_ROOT = Path(__file__).resolve().parents[1]
URBAN = {"kadunanorth", "kadunasouth"}
SMAP_OUTAGES = [
    (dt.date(2019, 6, 19), dt.date(2019, 7, 23)),
    (dt.date(2022, 8, 6), dt.date(2022, 9, 20)),
]
SEASON = range(4, 11)  # April-October growing season
PEAK = (8, 9)  # A6 target window
TERRA_DRIFT_FROM = 2025  # MODIS years flagged target_suspect
DISPLAY = {"jemaa": "Jema'a"}  # fix title-case artefact "Jema'A"


def key(name):
    k = re.sub(r"[^a-z]", "", str(name).lower())
    return {"zangokataf": "zangonkataf"}.get(k, k)


class Sources:
    def __init__(self, root: Path):
        self.root = root
        self.missing = []

    def read(self, rel, required=True):
        p = self.root / rel
        if not p.exists():
            if required:
                sys.exit(f"Missing required input {p}")
            self.missing.append(rel)
            return None
        df = pd.read_csv(p)
        if "lga" in df.columns:
            df["lga_key"] = df["lga"].map(key)
            df = df.drop(columns="lga")
        return df


def prefixed(df, prefix, keep, keys=("lga_key", "year", "month")):
    cols = [c for c in keep if c in df.columns]
    out = df[list(keys) + cols].copy()
    return out.rename(columns={c: f"{prefix}{c}" for c in cols})


def as_bool(s):
    return s.astype(str).str.lower().isin(["true", "1"])


# --------------------------------------------------------------------------- #
# Monthly table
# --------------------------------------------------------------------------- #
def build_monthly(src, lgas):
    chirps = src.read("chirps/chirps_lga_monthly.csv")
    chirps["complete_month"] = as_bool(chirps["complete_month"])
    m = prefixed(
        chirps,
        "chirps_",
        ["rain_mm", "rain_days", "heavy_days_50mm", "max_daily_rain_mm", "complete_month"],
    )
    m["rain_mm"] = m["chirps_rain_mm"].where(m["chirps_complete_month"])
    m["rain_source"] = "CHIRPS v2.0"

    power = src.read("power/power_lga_monthly.csv")
    if "rs_filled_days" not in power.columns:
        sys.exit(
            "power_lga_monthly.csv has no rs_filled_days: rebuild POWER with the solar fix first"
        )
    power["complete_month"] = as_bool(power["complete_month"])
    m = m.merge(
        prefixed(
            power,
            "power_",
            [
                "tmax_mean_c",
                "tmin_mean_c",
                "heat_days",
                "cold_days",
                "rh_mean_pct",
                "et0_pm_mm",
                "et0_hg_mm",
                "rs_filled_days",
                "complete_month",
            ],
        ),
        on=["lga_key", "year", "month"],
        how="outer",
    )
    m["water_balance_mm"] = m["rain_mm"] - m["power_et0_pm_mm"]

    imerg = src.read("imerg/imerg_lga_monthly.csv")
    for c in ("trmm_era", "complete_month"):
        imerg[c] = as_bool(imerg[c])
    m = m.merge(
        prefixed(
            imerg,
            "imerg_",
            [
                "rain_mm",
                "rain_days",
                "heavy_days_50mm",
                "extreme_days_p95",
                "max_daily_rain_mm",
                "peak_rate_mmhr",
                "trmm_era",
                "complete_month",
            ],
        ),
        on=["lga_key", "year", "month"],
        how="left",
    )
    m["imerg_suspect"] = m["lga_key"].isin(URBAN)

    smap = src.read("smap/lga_monthly.csv")
    sm = prefixed(smap, "smap_", ["sm_rootzone", "sm_surface", "sm_rootzone_wetness", "anomaly_z"])
    daily = src.read("smap/lga_daily.csv", required=False)
    if daily is not None:
        d = pd.to_datetime(daily["date"]).dt.date
        out = np.zeros(len(daily), bool)
        for a, b in SMAP_OUTAGES:
            out |= (d >= a) & (d <= b)
        daily["model_only"] = out
        cnt = daily.groupby(["lga_key", "year", "month"], as_index=False).agg(
            smap_days=("date", "nunique"), smap_model_only_days=("model_only", "sum")
        )
        sm = sm.merge(cnt, on=["lga_key", "year", "month"], how="left")
    m = m.merge(sm, on=["lga_key", "year", "month"], how="left")

    ndvi = src.read("ndvi/ndvi_lga_monthly.csv")
    ndvi["bare"] = as_bool(ndvi["bare"])
    m = m.merge(
        prefixed(
            ndvi, "", ["ndvi_crop_sg", "ndvi_all", "interpolated_share", "bare_threshold", "bare"]
        ).rename(columns={"interpolated_share": "ndvi_interpolated_share"}),
        on=["lga_key", "year", "month"],
        how="left",
    )

    opt = [
        ("extras/viirs_ndvi/viirs_ndvi_lga_monthly.csv", "viirs_", ["ndvi", "ndvi_crop"]),
        (
            "extras/gldas/gldas_lga_monthly.csv",
            "gldas_",
            ["rootzone_mm", "sm_0_10cm", "evap_mm_month"],
        ),
        ("extras/et/et_lga_monthly.csv", "modis_", ["et_mm_month", "pet_mm_month", "et_ratio"]),
        ("extras/lst/lst_lga_monthly.csv", "lst_", ["lst_day_c", "lst_night_c"]),
        (
            "extras/era5land/era5land_lga_monthly.csv",
            "era5_",
            [
                "tmax_c",
                "tmin_c",
                "tdew_c",
                "rs_mj_m2",
                "wind2m_ms",
                "precip_mm_month",
                "swvl1_0_7cm",
                "swvl2_7_28cm",
            ],
        ),
    ]
    for rel, prefix, cols in opt:
        df = src.read(rel, required=False)
        if df is not None:
            m = m.merge(prefixed(df, prefix, cols), on=["lga_key", "year", "month"], how="left")

    if "lst_lst_day_c" in m:
        m = m.rename(columns={"lst_lst_day_c": "lst_day_c", "lst_lst_night_c": "lst_night_c"})
        m.loc[m["lst_day_c"] < 20, "lst_day_c"] = np.nan
        m.loc[m["lst_night_c"] < 5, "lst_night_c"] = np.nan
    if "modis_et_ratio" in m:
        g = m.groupby(["lga_key", "month"])["modis_et_ratio"]
        m["modis_et_ratio_anom"] = (m["modis_et_ratio"] - g.transform("mean")) / g.transform("std")

    m = m.merge(lgas, on="lga_key", how="left")
    m["date"] = pd.to_datetime(dict(year=m["year"], month=m["month"], day=1))
    lead = [
        "lga",
        "lga_key",
        "year",
        "month",
        "date",
        "urban_lga",
        "rain_mm",
        "rain_source",
        "water_balance_mm",
    ]
    m = m[lead + [c for c in m.columns if c not in lead]]
    return m.sort_values(["lga", "year", "month"]).reset_index(drop=True)


# --------------------------------------------------------------------------- #
# Yearly table (A5 / A6)
# --------------------------------------------------------------------------- #
def season_agg(m):
    s = m[m["month"].isin(SEASON)]
    agg = {
        "rain_mm": ("rain_mm", "sum"),
        "chirps_heavy_days_50mm": ("chirps_heavy_days_50mm", "sum"),
        "power_heat_days": ("power_heat_days", "sum"),
        "power_et0_pm_mm": ("power_et0_pm_mm", "sum"),
        "power_tmax_mean_c": ("power_tmax_mean_c", "mean"),
        "water_balance_mm": ("water_balance_mm", "sum"),
        "imerg_heavy_days_50mm": ("imerg_heavy_days_50mm", "sum"),
        "imerg_extreme_days_p95": ("imerg_extreme_days_p95", "sum"),
        "imerg_peak_rate_mmhr": ("imerg_peak_rate_mmhr", "max"),
        "smap_sm_rootzone": ("smap_sm_rootzone", "mean"),
        "smap_anomaly_z": ("smap_anomaly_z", "mean"),
        "smap_model_only_days": ("smap_model_only_days", "sum"),
        "gldas_rootzone_mm": ("gldas_rootzone_mm", "mean"),
        "modis_et_ratio_anom": ("modis_et_ratio_anom", "mean"),
        "lst_day_c": ("lst_day_c", "mean"),
        "months": ("month", "nunique"),
    }
    agg = {f"season_{k}": v for k, v in agg.items() if v[0] in s.columns}
    out = s.groupby(["lga_key", "year"], as_index=False).agg(**agg)
    # A season value needs its source to cover the season: sums need all 7
    # months (a missing month would silently undercount), means at least 5.
    counts = s.groupby(["lga_key", "year"], as_index=False).agg(
        **{f"n_{k}": (v[0], "count") for k, v in agg.items() if k != "season_months"}
    )
    out = out.merge(counts, on=["lga_key", "year"], how="left")
    for k, (_col, how) in agg.items():
        if k == "season_months":
            continue
        need = len(SEASON) if how == "sum" else 5
        out.loc[out[f"n_{k}"] < need, k] = np.nan
    return out.drop(columns=[c for c in out.columns if c.startswith("n_season_")])


def build_yearly(src, m, lgas):
    a2 = src.read("a2/a2_lga_year.csv")
    y = a2[
        [
            "lga_key",
            "year",
            "onset_doy",
            "cessation_doy",
            "length_days",
            "season_rain_mm",
            "longest_dry_spell_days",
            "dry_spells_10plus",
        ]
    ].rename(columns=lambda c: c if c in ("lga_key", "year") else f"a2_{c}")
    annual = src.read("chirps/chirps_lga_annual.csv")
    annual["complete_year"] = as_bool(annual["complete_year"])
    y = y.merge(
        annual[["lga_key", "year", "annual_mm", "complete_year"]].rename(
            columns={"annual_mm": "chirps_annual_mm", "complete_year": "chirps_complete_year"}
        ),
        on=["lga_key", "year"],
        how="outer",
    )
    y = y.merge(season_agg(m), on=["lga_key", "year"], how="left")

    peak = src.read("ndvi/ndvi_lga_peak_season.csv")
    y = y.merge(
        peak[["lga_key", "year", "peak_ndvi_crop", "anomaly_z", "interpolated_share"]].rename(
            columns={
                "peak_ndvi_crop": "target_peak_ndvi_crop",
                "anomaly_z": "target_ndvi_anomaly_z",
                "interpolated_share": "target_interpolated_share",
            }
        ),
        on=["lga_key", "year"],
        how="left",
    )
    # MODIS Terra drift: from 2025 MODIS peak NDVI jumps ~+0.025 while VIIRS and
    # 30 m HLS stay flat (review, 14 Nov 2026). Flag those years and rebuild the
    # A6 training anomaly on a 2000-2024 baseline so they do not bias the rest.
    y["target_suspect"] = y["year"] >= TERRA_DRIFT_FROM
    base = y[(y["year"] < TERRA_DRIFT_FROM) & y["target_peak_ndvi_crop"].notna()]
    stats = base.groupby("lga_key")["target_peak_ndvi_crop"].agg(["mean", "std"])
    y = y.join(stats, on="lga_key")
    y["target_train_z"] = ((y["target_peak_ndvi_crop"] - y["mean"]) / y["std"]).where(
        ~y["target_suspect"]
    )
    y = y.drop(columns=["mean", "std"])

    vi = m[m["month"].isin(PEAK)].groupby(["lga_key", "year"], as_index=False)
    if "viirs_ndvi_crop" in m:
        v = vi.agg(viirs_peak_ndvi_crop=("viirs_ndvi_crop", "mean"))
        g = v.groupby("lga_key")["viirs_peak_ndvi_crop"]
        v["viirs_ndvi_anomaly_z"] = (v["viirs_peak_ndvi_crop"] - g.transform("mean")) / g.transform(
            "std"
        )
        y = y.merge(v, on=["lga_key", "year"], how="left")

    hls = src.read("extras/hls/hls_lga_seasonal.csv", required=False)
    if hls is not None:
        y = y.merge(
            hls[
                [
                    "lga_key",
                    "year",
                    "dry_ndvi_crop",
                    "peak_ndvi_crop",
                    "peak_minus_dry_crop",
                    "dry_clear_obs_crop",
                ]
            ]
            .add_prefix("hls_")
            .rename(columns={"hls_lga_key": "lga_key", "hls_year": "year"}),
            on=["lga_key", "year"],
            how="left",
        )

    y = y.merge(lgas, on="lga_key", how="left")
    lead = ["lga", "lga_key", "year", "urban_lga"]
    y = y[lead + [c for c in y.columns if c not in lead]]
    return y.sort_values(["lga", "year"]).reset_index(drop=True)


# --------------------------------------------------------------------------- #
# Static table
# --------------------------------------------------------------------------- #
def build_static(src, lgas):
    s = lgas.copy()
    crop = src.read("ndvi/lga_cropland.csv")
    s = s.merge(crop[["lga_key", "cropland_share"]], on="lga_key", how="left")
    s["low_cropland"] = (s["cropland_share"] < 0.10) | s["urban_lga"]

    terr = src.read("extras/srtm/srtm_lga_terrain.csv", required=False)
    if terr is not None:
        keep = [
            "elev_m",
            "slope_pct",
            "slope_pct_crop",
            "slope_pct_crop_p90",
            "crop_share_slope_gt2",
            "crop_share_slope_gt5",
            "crop_share_slope_gt8",
        ]
        s = s.merge(
            terr[["lga_key"] + keep]
            .add_prefix("srtm_")
            .rename(columns={"srtm_lga_key": "lga_key"}),
            on="lga_key",
            how="left",
        )

    soil = src.read("soil/lga_soil_hydro.csv")
    keep = [
        "wp_0_20cm",
        "fc_0_20cm",
        "sat_0_20cm",
        "awc_0_50cm_mm",
        "ksat_min_mm_h",
        "bedrock_depth_cm",
        "hydrologic_group",
        "k_factor",
        "k_factor_si",
        "k_class",
        "erodibility_rel",
    ]
    s = s.merge(
        soil[["lga_key"] + [c for c in keep if c in soil]]
        .add_prefix("soil_")
        .rename(columns={"soil_lga_key": "lga_key"}),
        on="lga_key",
        how="left",
    )

    ind = src.read("imerg/imerg_lga_indicators.csv")
    s = s.merge(
        ind.drop(columns=["years"])
        .add_prefix("imerg_")
        .rename(columns={"imerg_lga_key": "lga_key"}),
        on="lga_key",
        how="left",
    )
    s["imerg_suspect"] = s["urban_lga"]

    annual = src.read("chirps/chirps_lga_annual.csv")
    annual = annual[as_bool(annual["complete_year"])]
    norm = (
        annual[annual["year"].between(1991, 2020)]
        .groupby("lga_key")
        .agg(
            chirps_annual_mm_1991_2020=("annual_mm", "mean"),
            chirps_season_mm_1991_2020=("season_mm", "mean"),
            chirps_annual_mm_sd=("annual_mm", "std"),
        )
        .reset_index()
    )
    s = s.merge(norm, on="lga_key", how="left")

    a2 = src.read("a2/a2_lga_summary.csv")
    keep = [
        "onset_normal_1991_2020",
        "onset_normal_doy",
        "onset_p20_early",
        "onset_p80_late",
        "cessation_median",
        "cessation_normal_doy",
        "length_median_days",
        "longest_dry_spell_median_days",
        "share_years_dry_spell_10plus",
        "onset_trend_days_per_decade",
        "onset_trend_p",
    ]
    s = s.merge(
        a2[["lga_key"] + keep].add_prefix("a2_").rename(columns={"a2_lga_key": "lga_key"}),
        on="lga_key",
        how="left",
    )

    bare = src.read("ndvi/ndvi_bare_probability.csv")
    s = s.merge(
        bare.rename(columns={f"m{i:02d}": f"bare_prob_m{i:02d}" for i in range(1, 13)}),
        on="lga_key",
        how="left",
    )
    lead = ["lga", "lga_key", "urban_lga", "low_cropland", "cropland_share"]
    return (
        s[lead + [c for c in s.columns if c not in lead]].sort_values("lga").reset_index(drop=True)
    )


# --------------------------------------------------------------------------- #
# Dictionary and checks
# --------------------------------------------------------------------------- #
DICT = [  # (pattern, source, unit, meaning)
    (r"^lga$", "geoBoundaries ADM2", "", "LGA display name"),
    (r"^lga_key$", "derived", "", "normalised LGA key used for joins"),
    (r"^(year|month|date)$", "", "", "time key"),
    (
        r"^urban_lga$",
        "PRD decision",
        "bool",
        "Kaduna North / Kaduna South (urban, little cropland)",
    ),
    (
        r"^low_cropland$",
        "ESA WorldCover + PRD",
        "bool",
        "cropland < 10% or urban: show notice, not cards",
    ),
    (
        r"^rain_mm$",
        "CHIRPS v2.0",
        "mm/month",
        "rainfall used everywhere in the app (complete months only)",
    ),
    (r"^rain_source$", "", "", "source of rain_mm"),
    (r"^water_balance_mm$", "CHIRPS - POWER", "mm/month", "rain minus FAO-56 reference ET0"),
    (
        r"^chirps_",
        "CHIRPS v2.0 (UCSB-CHG/CHIRPS/DAILY)",
        "mm, days",
        "gauge-blended daily rain, 0.05 deg",
    ),
    (
        r"^power_",
        "NASA POWER (MERRA-2/CERES, AG)",
        "C, %, mm, days",
        "temperature, humidity, ET0; rainfall excluded",
    ),
    (
        r"^power_rs_filled_days$",
        "NASA POWER + FAO-56 eq.50",
        "days",
        "days with filled solar radiation (1981-83)",
    ),
    (r"^imerg_suspect$", "review", "bool", "IMERG unreliable here (urban single-pixel inflation)"),
    (
        r"^imerg_",
        "GPM IMERG V07 Final",
        "mm, days, mm/h",
        "satellite rain, extremes, 30-min peak rate",
    ),
    (
        r"^smap_model_only_days$",
        "SMAP L4 outage dates",
        "days",
        "land-model-only SMAP days in the month",
    ),
    (r"^smap_", "SMAP L4 SPL4SMGP V008", "m3/m3, z", "surface and root-zone soil moisture"),
    (
        r"^(ndvi_crop_sg|ndvi_all|ndvi_interpolated_share|bare_threshold|bare)$",
        "MODIS MOD13Q1 V6.1 + WorldCover",
        "NDVI, share, bool",
        "cropland NDVI (Savitzky-Golay), bare-month flag (A4)",
    ),
    (r"^viirs_", "VIIRS VNP13A1 V2", "NDVI", "NDVI cross-check for MODIS"),
    (r"^gldas_", "GLDAS-2.1 Noah", "mm, m3/m3", "modelled soil moisture 2000 on (pre-SMAP)"),
    (r"^modis_", "MODIS MOD16A2GF V6.1", "mm, ratio, z", "actual/potential ET; use anomalies only"),
    (r"^lst_", "MODIS MOD11A2 V6.1", "C", "land surface temperature, good-QC only"),
    (r"^era5_", "ERA5-Land daily", "C, MJ/m2, m/s, mm, m3/m3", "independent check on POWER"),
    (r"^a2_", "A2 onset_a2.py on CHIRPS", "day of year, days, mm", "onset, cessation, dry spells"),
    (
        r"^season_",
        "season (Apr-Oct) aggregate",
        "as source",
        "growing-season sum/mean of the monthly column",
    ),
    (r"^target_suspect$", "review", "bool", "MODIS Terra drift years (2025 on): do not train on"),
    (
        r"^target_train_z$",
        "MODIS NDVI (A6 target)",
        "z",
        "A6 TRAINING TARGET: Aug-Sep cropland NDVI z vs 2000-2024, blank when suspect",
    ),
    (
        r"^target_",
        "MODIS NDVI (A6 target)",
        "NDVI, z",
        "Aug-Sep cropland NDVI and its within-LGA z-score",
    ),
    (
        r"^hls_",
        "NASA HLS v2 (Landsat 8/9 + Sentinel-2)",
        "NDVI, count",
        "30 m dry/peak-season cropland NDVI",
    ),
    (
        r"^srtm_",
        "NASA SRTM 30 m",
        "m, %, share",
        "elevation, slope, cropland share above slope thresholds",
    ),
    (
        r"^soil_",
        "iSDAsoil + Saxton-Rawls 2006 + Williams 1995",
        "m3/m3, mm, mm/h, K",
        "soil water, group, erodibility",
    ),
    (r"^cropland_share$", "ESA WorldCover v200", "share", "share of LGA in cropland (class 40)"),
    (r"^bare_prob_", "MODIS NDVI (A4)", "share of years", "probability the month is bare, per LGA"),
    (r"^chirps_annual|^chirps_season", "CHIRPS v2.0", "mm", "1991-2020 rainfall normals"),
]


def dictionary(tables):
    rows = []
    for tname, df in tables.items():
        for c in df.columns:
            src, unit, meaning = "", "", ""
            for pat, s, u, mn in DICT:
                if re.search(pat, c):
                    src, unit, meaning = s, u, mn
                    break
            rows.append(
                {
                    "table": tname,
                    "column": c,
                    "dtype": str(df[c].dtype),
                    "non_null_share": round(df[c].notna().mean(), 3),
                    "source": src,
                    "unit": unit,
                    "meaning": meaning,
                }
            )
    return pd.DataFrame(rows)


def check(m, y, s):
    problems = []
    n = s["lga"].nunique()
    if n != 23:
        problems.append(f"static table has {n} LGAs, expected 23")
    dup = m.duplicated(["lga_key", "year", "month"]).sum()
    if dup:
        problems.append(f"{dup} duplicate LGA-month rows")
    if m["lga"].isna().any():
        problems.append(
            f"LGA keys without a boundary: {sorted(m.loc[m['lga'].isna(), 'lga_key'].unique())}"
        )
    if (m["power_et0_pm_mm"] == 0).any():
        problems.append("zero ET0 months present (POWER solar fix not applied)")
    for col, lo, hi in [
        ("rain_mm", 0, 1200),
        ("power_tmax_mean_c", 20, 45),
        ("smap_sm_rootzone", 0.02, 0.6),
        ("ndvi_crop_sg", -0.2, 1),
    ]:
        v = m[col].dropna()
        if len(v) and (v.min() < lo or v.max() > hi):
            problems.append(f"{col} outside {lo}-{hi}: {v.min():.3g} to {v.max():.3g}")
    return problems


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--processed", default=str(REPO_ROOT / "data/processed"))
    ap.add_argument("--out", default=None, help="default: <processed>/features")
    args = ap.parse_args()
    root = Path(args.processed)
    out = Path(args.out) if args.out else root / "features"
    out.mkdir(parents=True, exist_ok=True)

    import geopandas as gpd

    names = gpd.read_file(root / "kaduna_lgas.geojson")["lga"]
    lgas = pd.DataFrame({"lga_key": names.map(key)})
    lgas["lga"] = [DISPLAY.get(k, n) for k, n in zip(lgas["lga_key"], names, strict=True)]
    lgas["urban_lga"] = lgas["lga_key"].isin(URBAN)

    src = Sources(root)
    log.info("Building monthly table")
    m = build_monthly(src, lgas)
    log.info("Building yearly table")
    y = build_yearly(src, m, lgas)
    log.info("Building static table")
    s = build_static(src, lgas)

    tables = {"lga_month_features": m, "lga_year_features": y, "lga_static": s}
    for name, df in tables.items():
        df.to_parquet(out / f"{name}.parquet", index=False)
        df.to_csv(out / f"{name}.csv", index=False)
        log.info(f"  {name}: {len(df):,} rows x {df.shape[1]} columns")
    dictionary(tables).to_csv(out / "data_dictionary.csv", index=False)

    if src.missing:
        log.info(f"  optional inputs not found (columns skipped): {src.missing}")
    problems = check(m, y, s)
    for p in problems:
        log.info(f"  CHECK: {p}")
    log.info(f"{'Done' if not problems else 'Done with warnings'}. Files in {out}/")
    return 1 if problems else 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    sys.exit(main())
