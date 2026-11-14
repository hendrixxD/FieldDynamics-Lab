import numpy as np
import pandas as pd

from model import backtest as bt


def test_loyo_share_leaves_out_the_own_year() -> None:
    rows = pd.DataFrame({"g": ["a", "a", "a", "b"], "v": [1.0, 0.0, 0.0, 1.0]})
    share = bt.loyo_share(rows, "v", ["g"])
    assert share.tolist()[:3] == [0.0, 0.5, 0.5]  # first row sees only the two zeros
    assert np.isnan(share.iloc[3])  # a group with one year has no other year


def test_hit_rates_by_hand() -> None:
    warning = pd.Series([True, True, False, False, True])
    bad = pd.Series([True, False, True, False, False])
    r = bt.hit_rates(warning, bad)
    assert r["hit_rate"] == 0.5  # 1 of 2 bad years warned
    assert r["false_alarm_rate"] == 2 / 3  # 2 of 3 good years warned
    assert r["bad_share_if_warned"] == 1 / 3 and r["bad_share_if_not_warned"] == 0.5


def test_backtest_table_uses_the_season_type_and_skips_untyped_years() -> None:
    rows = pd.DataFrame(
        {
            "lga": ["A"] * 6,
            "year": [2000, 2001, 2002, 2003, 2004, 2005],
            "crop": ["maize"] * 6,
            "season_type": ["Late", "Late", "Late", "Wet", "Wet", None],
            "fits_season": [False, False, True, True, True, True],
        }
    )
    t = bt.backtest_table(rows, 0.8).set_index("year")
    assert 2005 not in t.index
    assert t.loc[2000, "fit_share_type"] == 0.5 and t.loc[2000, "warned_type"]
    assert t.loc[2003, "fit_share_type"] == 1.0 and not t.loc[2003, "warned_type"]
    assert t.loc[2000, "failed"] and not t.loc[2003, "failed"]
