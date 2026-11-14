"""FAO ECOCROP crop ranges for our crops, read from the Recocrop R package (Hijmans, GPL-3).

Input: inst/parameters/ecocrop.rds from Recocrop 0.4-2 on GitHub's CRAN mirror, checked against
the SHA-256 in config/models.yaml (suitability.ecocrop_source, ecocrop_sha256).

Output: data/raw/ecocrop/ecocrop_kaduna_crops.csv, one row per crop key in
suitability.ecocrop_names, with every ECOCROP column. Notebook a15 reads it.

    python -m data.ecocrop
"""

import hashlib
import logging
import tempfile
from pathlib import Path

import pandas as pd
import rdata
import requests

from model.settings import SuitabilityConfig, load_model_config

REPO_ROOT = Path(__file__).resolve().parents[1]
log = logging.getLogger("ecocrop")


def download(url: str, sha256: str) -> bytes:
    """Fetch the .rds file and refuse it if its hash is not the pinned one."""
    body = requests.get(url, timeout=60).content
    digest = hashlib.sha256(body).hexdigest()
    if digest != sha256:
        raise ValueError(f"ECOCROP file hash {digest} is not the pinned {sha256}")
    return body


def read_rds(body: bytes) -> pd.DataFrame:
    """The whole ECOCROP table (about 1,700 plants) as a DataFrame."""
    with tempfile.NamedTemporaryFile(suffix=".rds") as tmp:
        tmp.write(body)
        tmp.flush()
        return rdata.read_rds(tmp.name)


def select_crops(table: pd.DataFrame, names: dict[str, str]) -> pd.DataFrame:
    """One row per crop key, crop column first; raises if a configured NAME is missing."""
    missing = sorted(set(names.values()) - set(table["NAME"]))
    if missing:
        raise ValueError(f"Not in ECOCROP: {missing}")
    keys = pd.DataFrame({"crop": list(names), "NAME": list(names.values())})
    ours = table[table["NAME"].isin(keys["NAME"])]  # some other plants share a NAME; ours don't
    return keys.merge(ours, on="NAME", how="left", validate="one_to_one")


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    sc: SuitabilityConfig = load_model_config().suitability
    rows = select_crops(read_rds(download(sc.ecocrop_source, sc.ecocrop_sha256)), sc.ecocrop_names)
    out = REPO_ROOT / sc.ecocrop
    out.parent.mkdir(parents=True, exist_ok=True)
    rows.to_csv(out, index=False)
    log.info("Wrote %d crops to %s", len(rows), out)


if __name__ == "__main__":
    main()
