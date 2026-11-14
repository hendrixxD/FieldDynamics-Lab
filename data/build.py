"""Rebuild the feature tables from the processed source tables: python -m data.build

AGENTS.md names this command. It runs the steps that need no network, in order, each as its own
module so each keeps its own command line:

1. data.onset_a2: rain onset, cessation and dry spells per LGA-year (A2), from CHIRPS daily.
2. data.features_join: every processed source joined into the three feature tables, with the
   data dictionary and the range checks.

The pulls that fill data/processed/ (Earth Engine, NASA POWER, KDBS, ECOCROP) need network access
and credentials, so they are run on their own; docs/sources.md lists each with its command.
Then python -m model.build rebuilds every model output.
"""

import logging
import subprocess
import sys

STEPS = ("data.onset_a2", "data.features_join")

log = logging.getLogger("data.build")


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    for module in STEPS:
        log.info("Running python -m %s", module)
        subprocess.run([sys.executable, "-m", module], check=True)
    log.info("Feature tables rebuilt in data/processed/features/")


if __name__ == "__main__":
    main()
