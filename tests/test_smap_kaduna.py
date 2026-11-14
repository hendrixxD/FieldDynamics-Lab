"""Checks the SMAP Kaduna script's windowing, LGA mapping and summaries on known inputs."""

import argparse
import datetime
import sys
from pathlib import Path
from types import SimpleNamespace

import geopandas as gpd
import numpy as np
import pandas as pd
import pytest
from shapely.geometry import box

from data import smap_kaduna as smap

# A 4 x 4 grid of 1-degree cells, centres at lat 13..10 (north first, as on EASE) and lon 6..9.
LAT = np.array([13.0, 12.0, 11.0, 10.0])
LON = np.array([6.0, 7.0, 8.0, 9.0])


def fake_h5(values: dict[str, np.ndarray] | None = None) -> dict[str, np.ndarray]:
    lon_grid, lat_grid = np.meshgrid(LON, LAT)
    h5 = {"cell_lat": lat_grid, "cell_lon": lon_grid}
    for path in smap.VARIABLES.values():
        h5[path] = (values or {}).get(path, np.zeros_like(lat_grid))
    return h5


def two_lgas() -> gpd.GeoDataFrame:
    # "Big" covers four cell centres; "Tiny" sits between centres and catches none.
    return gpd.GeoDataFrame(
        {"lga": ["Big", "Tiny"]},
        geometry=[box(5.5, 11.5, 7.5, 13.5), box(8.2, 10.2, 8.4, 10.4)],
        crs=4326,
    )


def test_parse_months_range_and_single() -> None:
    assert smap.parse_months("4-10") == [4, 5, 6, 7, 8, 9, 10]
    assert smap.parse_months("7") == [7]


def test_parse_months_rejects_reversed_range() -> None:
    with pytest.raises(argparse.ArgumentTypeError):
        smap.parse_months("10-4")


def test_bbox_of_adds_buffer() -> None:
    w, s, e, n = smap.bbox_of(two_lgas(), buffer_deg=0.5)
    assert (w, s, e, n) == pytest.approx((5.0, 9.7, 8.9, 14.0))


def test_grid_window_covers_bbox() -> None:
    window, lat_sub, lon_sub = smap.grid_window(fake_h5(), (6.5, 10.5, 8.5, 12.5))
    assert window == (1, 3, 1, 3)
    assert lat_sub.tolist() == [12.0, 11.0]
    assert lon_sub.tolist() == [7.0, 8.0]


def test_grid_window_outside_grid_fails() -> None:
    with pytest.raises(RuntimeError):
        smap.grid_window(fake_h5(), (20.0, 20.0, 21.0, 21.0))


def test_read_window_masks_fill_values() -> None:
    rootzone = np.full((4, 4), 0.25)
    rootzone[1, 1] = -9999.0
    data = smap.read_window(fake_h5({"Geophysical_Data/sm_rootzone": rootzone}), (1, 3, 1, 3))
    assert data["sm_rootzone"].dtype == np.float32
    assert np.isnan(data["sm_rootzone"][0, 0])
    assert data["sm_rootzone"][1, 1] == pytest.approx(0.25)


def test_cell_lga_map_falls_back_to_nearest_cell() -> None:
    mapping = smap.cell_lga_map(LAT, LON, two_lgas())
    big = mapping[mapping["lga"] == "Big"]
    assert sorted(zip(big["i"], big["j"], strict=True)) == [(0, 0), (0, 1), (1, 0), (1, 1)]
    assert not big["nearest_fallback"].any()
    tiny = mapping[mapping["lga"] == "Tiny"].iloc[0]
    assert (tiny["i"], tiny["j"], tiny["nearest_fallback"]) == (3, 2, True)


def write_day(cache: Path, day: str, rootzone: float, wetness: float) -> None:
    shape = (LAT.size, LON.size)
    np.savez(
        cache / f"{day}.npz",
        sm_rootzone=np.full(shape, rootzone, dtype="float32"),
        sm_surface=np.full(shape, rootzone, dtype="float32"),
        sm_rootzone_wetness=np.full(shape, wetness, dtype="float32"),
    )


def test_aggregate_and_summaries(tmp_path: Path) -> None:
    np.savez(tmp_path / "grid.npz", window=np.array([0, 4, 0, 4]), lat=LAT, lon=LON)
    write_day(tmp_path, "20230701", rootzone=0.2, wetness=0.4)
    write_day(tmp_path, "20240701", rootzone=0.3, wetness=0.6)
    mapping = smap.cell_lga_map(LAT, LON, two_lgas())

    daily = smap.aggregate_daily(tmp_path, mapping)
    assert len(daily) == 4  # 2 LGAs x 2 days; grid.npz is not read as a day
    big_2024 = daily[(daily["lga"] == "Big") & (daily["year"] == 2024)].iloc[0]
    assert big_2024["sm_rootzone"] == pytest.approx(0.3)
    assert big_2024["n_cells"] == 4

    monthly = smap.monthly_summary(daily)
    big = monthly[monthly["lga"] == "Big"].set_index("year")
    assert big.loc[2024, "anomaly_rootzone"] == pytest.approx(0.05)

    season = smap.season_summary(daily, dry_percentile=50, season_months=[7])
    season = season.set_index(["lga", "year"])
    assert season.loc[("Big", 2024), "wettest_rank"] == 1
    assert season.loc[("Big", 2024), "anomaly_pct"] == pytest.approx(20.0)
    assert season.loc[("Tiny", 2023), "nearest_fallback"]


def test_search_granules_keeps_one_granule_per_day(monkeypatch: pytest.MonkeyPatch) -> None:
    names = [
        "SMAP_L4_SM_gph_20240401T133000_Vv8011_001.h5",
        "SMAP_L4_SM_gph_20240401T163000_Vv8011_001.h5",  # wrong hour
        "SMAP_L4_SM_gph_20240402T133000_Vv8011_001.h5",
        "SMAP_L4_SM_gph_20240301T133000_Vv8011_001.h5",  # month outside the season
    ]
    granules = [{"umm": {"GranuleUR": name}} for name in names]
    fake = SimpleNamespace(search_data=lambda **_: granules)
    monkeypatch.setitem(sys.modules, "earthaccess", fake)

    picked = smap.search_granules([2024], [4], (0, 0, 1, 1), "133000", every_n_days=1)
    assert [day for day, _ in picked] == ["20240401", "20240402"]


def test_monthly_summary_columns_are_complete() -> None:
    daily = pd.DataFrame(
        {
            "lga": ["A", "A"],
            "year": [2023, 2024],
            "month": [7, 7],
            "sm_rootzone": [0.2, 0.4],
            "sm_rootzone_wetness": [0.5, 0.5],
            "sm_surface": [0.1, 0.1],
        }
    )
    monthly = smap.monthly_summary(daily)
    assert monthly["clim_rootzone"].tolist() == pytest.approx([0.3, 0.3])
    assert monthly["anomaly_z"].notna().all()


def test_season_summary_ignores_days_outside_the_season(tmp_path: Path) -> None:
    np.savez(tmp_path / "grid.npz", window=np.array([0, 4, 0, 4]), lat=LAT, lon=LON)
    write_day(tmp_path, "20240115", rootzone=0.05, wetness=0.1)  # dry season, outside
    write_day(tmp_path, "20240715", rootzone=0.3, wetness=0.6)
    daily = smap.aggregate_daily(tmp_path, smap.cell_lga_map(LAT, LON, two_lgas()))
    season = smap.season_summary(daily, dry_percentile=20, season_months=[4, 5, 6, 7, 8, 9, 10])
    big = season[season["lga"] == "Big"].iloc[0]
    assert big["season_rootzone"] == pytest.approx(0.3)
    assert big["days"] == 1


def test_wanted_days_respects_record_start_and_today() -> None:
    days = smap.wanted_days([2015], [3, 4], every_n_days=1, today=datetime.date(2015, 4, 3))
    assert days == ["20150331", "20150401", "20150402"]


def test_wanted_days_every_nth_day() -> None:
    days = smap.wanted_days([2024], [2], every_n_days=10, today=datetime.date(2025, 1, 1))
    assert days == ["20240201", "20240211", "20240221"]


def test_save_ee_days_writes_only_wanted_days(tmp_path: Path) -> None:
    props = {}
    for day, value in [("20240401", 0.2), ("20240402", 0.3)]:
        for name in smap.VARIABLES:
            props[f"{day}_1330_{name}"] = [[value, -9999.0]]
    props["20240401_1330_sm_profile"] = [[0.9, 0.9]]  # a band the script does not use

    assert smap.save_ee_days(props, {"20240401"}, tmp_path) == 1
    saved = np.load(tmp_path / "20240401.npz")
    assert sorted(saved.files) == sorted(smap.VARIABLES)
    assert saved["sm_rootzone"][0, 0] == pytest.approx(0.2)
    assert np.isnan(saved["sm_rootzone"][0, 1])
    assert not (tmp_path / "20240402.npz").exists()


def test_save_ee_days_rejects_a_day_with_missing_variables(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        smap.save_ee_days({"20240401_1330_sm_rootzone": [[0.2]]}, {"20240401"}, tmp_path)
