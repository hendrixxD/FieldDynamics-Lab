"""Rebuild every model output: python -m model.build

Training lives in the notebooks (AGENTS.md, Modeling). This command runs them in the order set
in config/models.yaml, top to bottom in a fresh kernel, and saves each executed notebook in
place, so the outputs in data/processed/models/ and the notebook cells always come from the same
run. Formulas live in model/ as tested functions that the notebooks import.

    python -m model.build                 # every notebook in order
    python -m model.build --only a9_water_balance
"""

import argparse
import logging
import time
from pathlib import Path

import nbformat
from nbclient import NotebookClient

from model.engine_data import export_bundle
from model.settings import load_model_config

log = logging.getLogger(__name__)


def run_notebook(path: Path, timeout_s: int) -> float:
    """Execute a notebook top to bottom in a fresh kernel and save it; returns seconds taken."""
    if not path.exists():
        raise FileNotFoundError(f"Training notebook not found: {path}")
    start = time.perf_counter()
    book = nbformat.read(path, as_version=4)
    client = NotebookClient(
        book,
        timeout=timeout_s,
        kernel_name="python3",
        resources={"metadata": {"path": str(path.parent)}},
    )
    client.execute()
    nbformat.write(book, path)
    return time.perf_counter() - start


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the training notebooks in order.")
    parser.add_argument("--only", nargs="+", help="run just these notebooks, by name")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    cfg = load_model_config()
    names = args.only or cfg.build.notebooks
    unknown = sorted(set(names) - set(cfg.build.notebooks))
    if unknown:
        raise ValueError(f"Not in config/models.yaml build.notebooks: {unknown}")
    for name in names:
        log.info("Running %s", name)
        seconds = run_notebook(cfg.paths.notebooks / f"{name}.ipynb", cfg.build.timeout_s)
        log.info("  done in %.0f s", seconds)
    log.info("Engine bundle for the app written to %s", export_bundle(cfg))


if __name__ == "__main__":
    main()
