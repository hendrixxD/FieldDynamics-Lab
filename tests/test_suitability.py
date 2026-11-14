"""EcoCrop suitability pieces with hand-checked inputs (Hijmans 2001 trapezoid; USDA triangle)."""

import numpy as np
import pandas as pd
import pytest

from model import suitability as su


def test_trapezoid_is_zero_outside_one_inside_linear_between() -> None:
    x = np.array([5.0, 10.0, 14.0, 18.0, 30.0, 40.0, 47.0, 50.0])
    got = su.trapezoid(x, 10, 18, 33, 47)  # ECOCROP maize temperatures
    assert got == pytest.approx([0, 0, 0.5, 1, 1, 0.5, 0, 0])


def test_texture_groups_follow_the_usda_triangle() -> None:
    sand = pd.Series([85.0, 60.0, 40.0, 55.0, 20.0])
    clay = pd.Series([5.0, 15.0, 20.0, 25.0, 45.0])
    # sand, sandy loam, loam, sandy clay loam (stays medium), clay
    assert su.usda_texture_group(sand, clay).tolist() == [
        "light",
        "light",
        "medium",
        "medium",
        "heavy",
    ]


def test_texture_fit_reads_optimal_tolerated_and_wide() -> None:
    group = pd.Series(["light", "medium", "heavy"])
    assert su.texture_fit(group, "M", "L M").tolist() == ["tolerated", "optimal", "outside"]
    assert su.texture_fit(group, "M", "W").tolist() == ["tolerated", "optimal", "tolerated"]
