"""ECOCROP crop selection: one row per crop key, refusing a NAME the table lacks."""

import pandas as pd
import pytest

from data.ecocrop import select_crops

TABLE = pd.DataFrame(
    {
        "NAME": ["Maize", "Cowpea", "Myrtle *", "Myrtle *"],
        "TOPMN": [18, 20, 15, 16],
    }
)


def test_select_crops_keeps_config_order_and_names_crop_first() -> None:
    rows = select_crops(TABLE, {"cowpea": "Cowpea", "maize": "Maize"})
    assert list(rows.columns[:2]) == ["crop", "NAME"]
    assert rows["crop"].tolist() == ["cowpea", "maize"]
    assert rows["TOPMN"].tolist() == [20, 18]


def test_select_crops_ignores_duplicate_names_of_other_plants() -> None:
    assert len(select_crops(TABLE, {"maize": "Maize"})) == 1


def test_select_crops_refuses_a_missing_name() -> None:
    with pytest.raises(ValueError, match="Not in ECOCROP"):
        select_crops(TABLE, {"yam": "White yam"})
