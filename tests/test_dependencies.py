"""Checks that every library chosen in requirements.in imports at its pinned version."""

import importlib

import pytest

# Import names, which differ from the names in requirements.in for several libraries.
LIBRARY_MODULES = [
    "dask",
    "dotenv",
    "earthaccess",
    "ee",
    "fastapi",
    "geemap",
    "geopandas",
    "h5py",
    "jinja2",
    "lightgbm",
    "matplotlib",
    "notebook",
    "numpy",
    "optuna",
    "pandas",
    "pandera.pandas",
    "PIL",
    "pyarrow",
    "pydantic",
    "pydantic_settings",
    "pymannkendall",
    "requests",
    "rioxarray",
    "rosetta",
    "SALib",
    "scipy",
    "shap",
    "shapely",
    "sklearn",
    "uvicorn",
    "xarray",
    "yaml",
]


@pytest.mark.parametrize("module_name", LIBRARY_MODULES)
def test_library_imports(module_name: str) -> None:
    """A clean resolve does not prove compiled libraries load together, so import each one."""
    assert importlib.import_module(module_name).__name__ == module_name
