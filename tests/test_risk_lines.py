"""Weather risk lines (A14) with small hand-made daily tables."""

import pandas as pd

from model import risk_lines as rl


def test_longest_run_counts_the_longest_true_stretch_per_group() -> None:
    flag = pd.Series([True, True, False, True, True, True, False, True])
    group = pd.Series(["a"] * 6 + ["b"] * 2)
    assert rl.longest_run(flag, [group]).to_dict() == {"a": 3, "b": 1}


def test_late_window_keeps_only_the_weeks_before_harvest() -> None:
    windows = pd.DataFrame(
        {
            "plant_date": pd.to_datetime(["2020-06-01", "2020-06-01"]),
            "harvest_date": pd.to_datetime(["2020-09-28", "2020-06-10"]),
        }
    )
    late = rl.add_late_window(windows, weeks=4)
    assert late["plant_date"].dt.strftime("%Y-%m-%d").tolist() == ["2020-08-31", "2020-06-01"]


def test_humid_flag_needs_high_rh_and_warm_days() -> None:
    daily = pd.DataFrame(
        {
            "lga": ["A"] * 4,
            "date": pd.to_datetime(["2020-07-01", "2020-07-02", "2020-07-03", "2020-07-04"]),
            "RH2M": [60.0, 70.0, 95.0, 96.0],
            "T2M": [25.0, 25.0, 25.0, 35.0],
        }
    )
    flagged = rl.add_humid_flag(daily, rh_quantile=0.5, t_low=20, t_high=32)
    assert flagged["humid"].tolist() == [False, False, True, False]


def test_share_of_seasons_by_type_and_overall() -> None:
    longest = pd.Series(
        [12, 3],
        index=pd.MultiIndex.from_tuples(
            [("A", 2020, "groundnut"), ("A", 2021, "groundnut")], names=["lga", "year", "crop"]
        ),
    )
    windows = pd.DataFrame(
        {
            "lga": ["A", "A"],
            "year": [2020, 2021],
            "crop": ["groundnut"] * 2,
            "season_type": ["Dry spell", "Typical"],
        }
    )
    out = rl.share_of_seasons(longest, 10, windows).iloc[0]
    assert out["share_all_years"] == 0.5
    assert out["share_Dry spell"] == 1.0
    assert out["share_Typical"] == 0.0
