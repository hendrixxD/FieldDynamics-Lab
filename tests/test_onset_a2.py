import datetime as dt

from data import onset_a2 as a2


def test_final_dry_run_is_the_season_ending_not_a_dry_spell() -> None:
    #           wet  dry x3   wet  dry x4 (reaches the end)
    series = [5.0, 0, 0, 0, 5.0, 0, 0, 0, 0]
    assert a2.runs_of_dry(series) == [(1, 3), (5, 4)]
    assert a2.mid_season_dry_runs(series) == [(1, 3)]


def _rain(year: int, dry_runs: dict[int, int]) -> dict[dt.date, float]:
    """A wet season from 1 May with dry runs {day of season: length}, dry from 15 Oct."""
    rain = {}
    day, end = dt.date(year, 1, 1), dt.date(year, 12, 31)
    dry_days = {
        dt.date(year, 5, 1) + dt.timedelta(start + k)
        for start, length in dry_runs.items()
        for k in range(length)
    }
    while day <= end:
        in_season = dt.date(year, 5, 1) <= day < dt.date(year, 10, 15)
        rain[day] = 12.0 if in_season and day not in dry_days else 0.0
        day += dt.timedelta(1)
    return rain


def test_season_counts_ten_day_spells_with_their_own_threshold() -> None:
    eight = a2.season(_rain(2020, {60: 8}), 2020)
    assert eight["longest_dry_spell_days"] == 8
    assert eight["dry_spells_10plus"] == 0  # 8 days: over the 7-day false start, not a long spell
    eleven = a2.season(_rain(2020, {60: 11}), 2020)
    assert eleven["dry_spells_10plus"] == 1
    assert eleven["longest_dry_spell_start"].month == 6
    assert [n for _, n in eight["spells"]] == [8]  # 7+ day spells are listed for the strip


def test_season_end_dry_down_is_not_the_longest_spell() -> None:
    result = a2.season(_rain(2020, {60: 4}), 2020)
    assert result["cessation"] is not None
    assert result["longest_dry_spell_days"] == 4  # not the dry days before cessation
