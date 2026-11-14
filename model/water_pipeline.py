"""A9 pipeline: run the FAO-56 water balance for every crop and LGA-year, write the a9 outputs.

Each crop is planted on its LGA-year's A2 onset date and runs for its FAO-56 stage lengths on
CHIRPS daily rain and POWER daily Penman-Monteith ET0 (Hargreaves rerun as the baseline).
"""

from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd

from model import water_balance as wb
from model.settings import Crop, ModelConfig

STAGE_LABELS = {
    "init": "establishment",
    "dev": "early growth",
    "mid": "flowering and filling",
    "late": "ripening",
}


@dataclass
class DailyInputs:
    """Daily weather as (date x LGA) arrays sharing one date index, plus soil and onset."""

    dates: pd.DatetimeIndex
    lgas: list[str]
    grids: dict[str, np.ndarray]
    awc_top: pd.Series
    awc_sub: pd.Series
    onset: pd.DataFrame


def canonical_lga(names: pd.Series, canonical: list[str]) -> pd.Series:
    """Map LGA names to the feature tables' spelling, ignoring case ("Jema'A" -> "Jema'a").

    Raises ValueError for a name with no match, so a misspelt LGA never drops out silently.
    """
    lookup = {name.casefold(): name for name in canonical}
    mapped = names.str.casefold().map(lookup)
    unknown = sorted(names[mapped.isna()].unique())
    if unknown:
        raise ValueError(f"LGA names not in the feature tables: {unknown}")
    return mapped


def to_grid(df: pd.DataFrame, column: str, dates: pd.DatetimeIndex, lgas: list[str]) -> np.ndarray:
    """A long (lga, date) column as a (date x LGA) array on the given date grid."""
    wide = df.pivot(index="date", columns="lga", values=column)
    return wide.reindex(index=dates, columns=lgas).to_numpy(dtype=float)


def load_inputs(cfg: ModelConfig, canonical: list[str]) -> DailyInputs:
    """Daily rain, ET0 (both methods), wind, RHmin and the filled-solar flag on one date grid."""
    paths = cfg.paths
    rain = pd.read_csv(paths.rain_daily, parse_dates=["date"])
    power = pd.read_parquet(paths.power_daily)
    rain["lga"] = canonical_lga(rain["lga"], canonical)
    power["lga"] = canonical_lga(power["lga"], canonical)
    power["rhmin_pct"] = wb.rh_min_pct(power["T2MDEW"].to_numpy(), power["T2M_MAX"].to_numpy())
    dates = pd.date_range(power["date"].min(), power["date"].max(), freq="D")
    lgas = sorted(power["lga"].unique())
    grids = {
        "rain_mm": to_grid(rain, "precip_mm", dates, lgas),
        "et0_pm_mm": to_grid(power, "ET0_PM", dates, lgas),
        "et0_hg_mm": to_grid(power, "ET0_HG", dates, lgas),
        "wind_m_s": to_grid(power, "WS2M", dates, lgas),
        "rhmin_pct": to_grid(power, "rhmin_pct", dates, lgas),
        "rs_filled": to_grid(power, "rs_filled", dates, lgas),
    }
    soil = pd.read_csv(paths.soil)
    soil = soil.assign(lga=canonical_lga(soil["lga"], canonical)).set_index("lga")
    onset = pd.read_csv(paths.onset, parse_dates=["onset"])
    onset["lga"] = canonical_lga(onset["lga"], canonical)
    years = cfg.water_balance.years
    onset = onset[onset["year"].between(years.start, years.end) & onset["onset"].notna()]
    return DailyInputs(
        dates,
        lgas,
        grids,
        soil["awc_mm_per_m_0_20cm"].reindex(lgas),
        soil["awc_mm_per_m_20_50cm"].reindex(lgas),
        onset.reset_index(drop=True),
    )


def season_windows(
    inputs: DailyInputs, length_days: int
) -> tuple[dict[str, np.ndarray], np.ndarray]:
    """Each daily grid cut to (n, length_days) from each LGA-year's onset date.

    Returns the windows and a mask of rows whose rain and ET0 are complete for the whole season.
    """
    start = inputs.dates.get_indexer(inputs.onset["onset"])
    col = pd.Index(inputs.lgas).get_indexer(inputs.onset["lga"])
    rows = start[:, None] + np.arange(length_days)[None, :]
    inside = (start >= 0) & (rows[:, -1] < len(inputs.dates))
    rows = np.clip(rows, 0, len(inputs.dates) - 1)
    windows = {name: g[rows, col[:, None]] for name, g in inputs.grids.items()}
    complete = (
        inside
        & np.isfinite(windows["rain_mm"]).all(axis=1)
        & np.isfinite(windows["et0_pm_mm"]).all(axis=1)
    )
    return windows, complete


def crop_kc(crop: Crop, windows: dict[str, np.ndarray], cfg: ModelConfig) -> np.ndarray:
    """Daily Kc (n, days) with Kc mid and Kc end adjusted for each row's own climate (Eq. 62)."""
    lengths = tuple(crop.stages_days.model_dump().values())
    bounds = wb.stage_bounds(lengths)
    kcc = cfg.water_balance.kc_climate

    def climate_adjusted(kc_table: float, stage: int) -> np.ndarray:
        cut = slice(bounds[stage], bounds[stage + 1])
        wind = np.clip(windows["wind_m_s"][:, cut].mean(axis=1), *kcc.wind_range_m_s)
        rhmin = np.clip(windows["rhmin_pct"][:, cut].mean(axis=1), *kcc.rhmin_range_pct)
        return wb.adjust_kc_for_climate(
            kc_table,
            wind,
            rhmin,
            crop.height_m,
            kcc.wind_ref_m_s,
            kcc.rhmin_ref_pct,
            kcc.wind_coef,
            kcc.rh_coef,
            kcc.height_exponent,
        )

    kc_mid = climate_adjusted(crop.kc.mid, 2)
    kc_end = (
        climate_adjusted(crop.kc.end, 3)
        if crop.kc.end >= kcc.kc_end_min_to_adjust
        else np.full(len(kc_mid), crop.kc.end)
    )
    w = wb.kc_weights(lengths)
    return crop.kc.ini * w[0][None, :] + kc_mid[:, None] * w[1] + kc_end[:, None] * w[2]


def run_crop(
    name: str, crop: Crop, inputs: DailyInputs, cfg: ModelConfig, et0: str = "et0_pm_mm"
) -> tuple[pd.DataFrame, wb.BucketResult, np.ndarray]:
    """Water balance for one crop over every complete LGA-year; one result row per LGA-year."""
    wbc, lengths = cfg.water_balance, tuple(crop.stages_days.model_dump().values())
    windows, complete = season_windows(inputs, crop.stages_days.total)
    windows = {k: v[complete] for k, v in windows.items()}
    keys = inputs.onset.loc[complete, ["lga", "year", "onset", "length_days"]]
    keys = keys.rename(columns={"length_days": "rainy_season_days"}).reset_index(drop=True)
    roots = wb.root_depth_m(lengths, wbc.root_depth_initial_m, crop.root_depth_max_m)
    taw = wb.total_available_water_mm(
        roots,
        inputs.awc_top.reindex(keys["lga"]).to_numpy(),
        inputs.awc_sub.reindex(keys["lga"]).to_numpy(),
        wbc.topsoil_depth_m,
    )
    pa = wbc.p_adjust
    result = wb.run_bucket(
        windows["rain_mm"],
        windows[et0],
        crop_kc(crop, windows, cfg),
        taw,
        crop.depletion_fraction_p,
        pa.slope,
        pa.reference_etc_mm_day,
        pa.min,
        pa.max,
    )
    rows = summarise_rows(keys, result, windows, lengths, wbc.stress_ks)
    rows.insert(0, "crop", name)
    return rows, result, taw


def summarise_rows(
    keys: pd.DataFrame,
    result: wb.BucketResult,
    windows: dict[str, np.ndarray],
    lengths: tuple[int, int, int, int],
    stress_ks: float,
) -> pd.DataFrame:
    """Season and per-stage need, use and shortfall for each LGA-year."""
    out = keys.rename(columns={"onset": "plant_date"})
    out["harvest_date"] = out["plant_date"] + pd.Timedelta(days=sum(lengths) - 1)
    # PRD season-length rule: does the crop finish inside the A2 rainy season?
    out["fits_season"] = sum(lengths) <= out["rainy_season_days"]
    out["water_need_mm"] = result.etc_mm.sum(axis=1)
    out["water_used_mm"] = result.eta_mm.sum(axis=1)
    out["shortfall_mm"] = out["water_need_mm"] - out["water_used_mm"]
    out["met_share"] = out["water_used_mm"] / out["water_need_mm"]
    out["season_rain_mm"] = windows["rain_mm"].sum(axis=1)
    out["stress_days"] = (result.ks < stress_ks).sum(axis=1)
    out["et0_filled_share"] = np.nan_to_num(windows["rs_filled"]).mean(axis=1)
    need = wb.stage_totals(result.etc_mm, lengths)
    short = need - wb.stage_totals(result.eta_mm, lengths)
    for i, stage in enumerate(wb.STAGES):
        out[f"shortfall_{stage}_mm"] = short[:, i]
        out[f"shortfall_{stage}_share"] = short[:, i] / need[:, i]
    return out


@dataclass
class CropRun:
    """Penman-Monteith rows for one crop, the Hargreaves baseline rows, and the daily result."""

    rows: pd.DataFrame
    hargreaves: pd.DataFrame
    result: wb.BucketResult
    taw: np.ndarray


def run_all(crops: dict[str, Crop], inputs: DailyInputs, cfg: ModelConfig) -> dict[str, CropRun]:
    """Penman-Monteith and Hargreaves runs for every crop."""
    runs = {}
    for name, crop in crops.items():
        rows, result, taw = run_crop(name, crop, inputs, cfg)
        hargreaves = run_crop(name, crop, inputs, cfg, et0="et0_hg_mm")[0]
        runs[name] = CropRun(rows, hargreaves, result, taw)
    return runs


def mark_short(rows: pd.DataFrame, shortfall_share: float) -> pd.DataFrame:
    """Flag seasons where water used fell more than shortfall_share below need."""
    return rows.assign(ran_short=rows["met_share"] < 1 - shortfall_share)


def frequency_table(rows: pd.DataFrame, keys: list[str]) -> pd.DataFrame:
    """Per group: years, median need, use, rain and shortfall, share of years short, and the
    riskiest stage (largest mean shortfall share)."""
    share_cols = [f"shortfall_{s}_share" for s in wb.STAGES]
    grouped = rows.groupby(keys)
    table = grouped.agg(
        years=("year", "size"),
        water_need_mm=("water_need_mm", "median"),
        water_used_mm=("water_used_mm", "median"),
        season_rain_mm=("season_rain_mm", "median"),
        shortfall_mm=("shortfall_mm", "median"),
        stress_days=("stress_days", "median"),
        short_share=("ran_short", "mean"),
        fits_season_share=("fits_season", "mean"),
        et0_filled_share=("et0_filled_share", "mean"),
    )
    stage_means = grouped[share_cols].mean()
    table = table.join(stage_means)
    riskiest = stage_means.to_numpy().argmax(axis=1)
    # A stage only counts as riskiest if it loses something on average.
    any_loss = stage_means.to_numpy().max(axis=1) > 0
    table["riskiest_stage"] = np.where(any_loss, np.array(wb.STAGES)[riskiest], "none")
    table["riskiest_stage_label"] = table["riskiest_stage"].map(STAGE_LABELS)
    return table.reset_index()


def smap_check(run: CropRun, smap: pd.DataFrame, years: tuple[int, int]) -> pd.DataFrame:
    """Correlation per LGA between modelled root-zone wetness (1 - Dr / TAW) and SMAP L4
    root-zone soil moisture on the same in-season days. docs/model-evaluation.md: water balance
    against SMAP, by correlation."""
    wetness = daily_long(run, 1 - run.result.depletion_mm / run.taw, "wetness")
    wetness = wetness[wetness["year"].between(*years)]
    joined = wetness.merge(smap[["lga", "date", "sm_rootzone"]], on=["lga", "date"], how="inner")
    per_lga = joined.groupby("lga").apply(
        lambda d: pd.Series(
            {
                "days": len(d),
                "pearson_r": d["wetness"].corr(d["sm_rootzone"]),
                "spearman_r": d["wetness"].corr(d["sm_rootzone"], method="spearman"),
            }
        ),
        include_groups=False,
    )
    return per_lga.reset_index()


def headline(
    rows: pd.DataFrame,
    summary: pd.DataFrame,
    by_type: pd.DataFrame,
    check: pd.DataFrame,
    hg: pd.DataFrame,
) -> dict[str, Any]:
    """Statewide numbers for a9_summary.json and the experiment log."""
    crop_short = rows.groupby("crop")["ran_short"].mean().round(3)
    type_short = by_type.groupby(["crop", "season_type"])["short_share"].mean().unstack().round(3)
    return {
        "lga_years_per_crop": rows.groupby("crop").size().to_dict(),
        "years": [int(rows["year"].min()), int(rows["year"].max())],
        "short_share_by_crop": crop_short.to_dict(),
        "short_share_by_crop_hargreaves": hg.groupby("crop")["ran_short"].mean().round(3).to_dict(),
        "short_share_by_crop_and_type": type_short.to_dict(orient="index"),
        "median_water_need_mm": rows.groupby("crop")["water_need_mm"].median().round(0).to_dict(),
        "riskiest_stage_statewide": summary.groupby("crop")["riskiest_stage"]
        .agg(lambda s: s.mode().iloc[0])
        .to_dict(),
        "fits_season_share_by_crop": rows.groupby("crop")["fits_season"].mean().round(3).to_dict(),
        "smap_pearson_median": round(float(check["pearson_r"].median()), 3),
        "smap_pearson_range": [
            round(float(check["pearson_r"].min()), 3),
            round(float(check["pearson_r"].max()), 3),
        ],
    }


def daily_long(run: CropRun, values: np.ndarray, name: str) -> pd.DataFrame:
    """A (rows, days) array as a long (lga, year, date, value) table using each row's plant date."""
    n_days = values.shape[1]
    plant = run.rows["plant_date"].to_numpy()
    return pd.DataFrame(
        {
            "lga": np.repeat(run.rows["lga"].to_numpy(), n_days),
            "year": np.repeat(run.rows["year"].to_numpy(), n_days),
            "date": (plant[:, None] + np.arange(n_days).astype("timedelta64[D]")).ravel(),
            name: values.ravel(),
        }
    )


def modis_et_check(run: CropRun, month: pd.DataFrame) -> pd.DataFrame:
    """Bucket actual ET summed over calendar months that lie wholly inside the crop season,
    beside MODIS MOD16 actual ET for the same LGA-month (mm/month). One row per LGA-month."""
    daily = daily_long(run, run.result.eta_mm, "bucket_et_mm")
    daily["month"] = daily["date"].dt.month
    monthly = daily.groupby(["lga", "year", "month"]).agg(
        bucket_et_mm=("bucket_et_mm", "sum"),
        days=("date", "size"),
        days_in_month=("date", lambda d: d.iloc[0].days_in_month),
    )
    monthly = monthly[monthly["days"] == monthly["days_in_month"]].reset_index()
    modis = month[["lga", "year", "month", "modis_et_mm_month"]].dropna()
    return monthly.merge(modis, on=["lga", "year", "month"], how="inner")
