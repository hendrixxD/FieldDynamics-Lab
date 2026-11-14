"""Kaduna State Bureau of Statistics retail prices per LGA, tidied for the rotation engine.

Input: data/raw/kdbs/kdbs_retail_prices_2026-11-14.xlsx, the full export of the KDBS market
price API (https://kdbs-server.azurewebsites.net/api/v1/market-prices), 24 commodities in all
23 LGAs plus a state average row. The API gives no collection date; the file name carries the
extraction date.

Output: data/processed/extras/kdbs/kdbs_lga_prices.csv, one row per LGA and commodity, with the
crop it maps to (config/crops.yaml, kdbs_commodities) where the measure is comparable.

    python -m data.kdbs_prices
"""

import argparse
import logging
from pathlib import Path

import pandas as pd

from model.settings import load_crop_config

REPO_ROOT = Path(__file__).resolve().parents[1]
RAW = REPO_ROOT / "data/raw/kdbs/kdbs_retail_prices_2026-11-14.xlsx"
OUT = REPO_ROOT / "data/processed/extras/kdbs/kdbs_lga_prices.csv"
SHEET = "By LGA (long format)"
HEADER_ROW = 3  # title rows above the table
STATE_ROW = "State Average Price"
log = logging.getLogger("kdbs_prices")


def read_long(path: Path) -> pd.DataFrame:
    """The long sheet as lga, commodity, price_ngn; state average row dropped."""
    raw = pd.read_excel(path, sheet_name=SHEET, header=HEADER_ROW, engine="openpyxl")
    table = raw.iloc[:, 2:].set_axis(["lga", "commodity", "unit_raw", "price_ngn"], axis=1)
    table = table.dropna(subset=["lga", "price_ngn"])
    return table[table["lga"] != STATE_ROW].drop(columns="unit_raw").reset_index(drop=True)


def tidy(table: pd.DataFrame, crop_of: dict[str, str]) -> pd.DataFrame:
    """Add the crop each commodity maps to and fix the one LGA spelling KDBS differs on."""
    lgas = table["lga"].replace({"Jemaa": "Jema'a"})
    check(table)
    return table.assign(
        lga=lgas, crop=table["commodity"].map(crop_of), price_ngn=table["price_ngn"].astype(float)
    )


def check(table: pd.DataFrame) -> None:
    """Fail loudly unless every LGA has every commodity once, with a positive price."""
    counts = table.groupby("commodity")["lga"].nunique()
    if table["lga"].nunique() != 23 or (counts != 23).any():
        raise ValueError(f"expected 23 LGAs for every commodity:\n{counts[counts != 23]}")
    if (table["price_ngn"] <= 0).any() or table.duplicated(["lga", "commodity"]).any():
        raise ValueError("non-positive or duplicated prices")


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--raw", default=str(RAW))
    ap.add_argument("--out", default=str(OUT))
    args = ap.parse_args()
    crop_of = {v: k for k, v in load_crop_config().kdbs_commodities.items()}
    table = tidy(read_long(Path(args.raw)), crop_of)
    missing = set(crop_of) - set(table["commodity"])
    if missing:
        raise ValueError(f"mapped commodities not in the KDBS file: {missing}")
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    table.to_csv(args.out, index=False)
    log.info(table.dropna(subset=["crop"]).groupby("crop")["price_ngn"].describe().round(0))
    log.info(f"wrote {args.out}")


if __name__ == "__main__":
    main()
