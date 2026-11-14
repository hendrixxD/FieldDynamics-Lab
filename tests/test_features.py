"""Data validation on the committed feature tables (AGENTS.md, Testing)."""

import pandas as pd

from data.features_join import check

FEATURES = "data/processed/features"
MONTH = pd.read_parquet(f"{FEATURES}/lga_month_features.parquet")
YEAR = pd.read_parquet(f"{FEATURES}/lga_year_features.parquet")
STATIC = pd.read_parquet(f"{FEATURES}/lga_static.parquet")


def test_feature_tables_pass_the_join_checks() -> None:
    """23 LGAs, no duplicate LGA-months, no zero ET0, values in physical ranges."""
    assert check(MONTH, YEAR, STATIC) == []


def test_ranges_the_standards_name() -> None:
    """Soil moisture 0 to 0.6, NDVI -1 to 1, rain never negative (engineering standards)."""
    assert MONTH["smap_sm_rootzone"].dropna().between(0, 0.6).all()
    assert MONTH["ndvi_crop_sg"].dropna().between(-1, 1).all()
    assert (MONTH["rain_mm"].dropna() >= 0).all()


def test_every_column_is_in_the_data_dictionary() -> None:
    dictionary = pd.read_csv(f"{FEATURES}/data_dictionary.csv")
    for name, table in (
        ("lga_month_features", MONTH),
        ("lga_year_features", YEAR),
        ("lga_static", STATIC),
    ):
        listed = set(dictionary.loc[dictionary["table"] == name, "column"])
        assert set(table.columns) <= listed, sorted(set(table.columns) - listed)
