"""Checks that the packages behind the build commands can be imported."""

import importlib

import pytest


@pytest.mark.parametrize("package_name", ["data", "model", "api"])
def test_package_imports(package_name: str) -> None:
    """`python -m data.build` and `python -m model.build` fail if these stop importing."""
    assert importlib.import_module(package_name).__name__ == package_name
