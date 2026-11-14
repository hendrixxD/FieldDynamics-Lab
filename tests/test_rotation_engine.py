import json
from pathlib import Path

import pytest

from model.engine_data import (
    export_bundle,
    load_bundle,
    load_engine_data,
    season_fit_table,
    striga_soil_base,
    tercile_score,
)
from model.rotation_engine import DIMENSIONS, PRIORITIES, Request, score_request

DATA = load_engine_data()
FARM = {"lga": "Zaria", "past_seasons": [["maize"], ["maize"]]}


def run(**changes: object) -> dict:
    return score_request(Request(**{**FARM, **changes}), DATA)


def test_three_cards_and_current_first() -> None:
    result = run()
    assert [c["badge"] for c in result["cards"]] == ["Current", "Option A", "Option B"]
    assert all(set(c["rows"]) == set(DIMENSIONS) for c in result["cards"])


def test_deterministic() -> None:
    assert run(priorities=["add_nitrogen"]) == run(priorities=["add_nitrogen"])


@pytest.mark.parametrize(
    "change",
    [
        {"lga": "Ikara"},
        {"water": "Irrigation"},
        {"equipment": "Tractor"},
        {"soil": "Not sure"},
        {"past_seasons": [["maize"], ["soybean"]]},
        {"sell": "Mostly home use"},
        {"season_type": "Late start"},
        {"priorities": ["feed_animals", "extra_income"]},
    ],
)
def test_every_input_changes_something(change: dict) -> None:
    """PRD acceptance: changing any input in I1 to I8 changes a score, ranking or text."""
    assert run(**change) != run()


def test_each_priority_changes_the_weights() -> None:
    base = run()["weights"]
    for priority, row in PRIORITIES.items():
        assert run(priorities=[priority])["weights"][row] > base[row]


def test_alternatives_differ_from_current() -> None:
    result = run(past_seasons=[["maize"], ["soybean"]])
    assert all(c["seasons"] != [["maize"], ["soybean"]] for c in result["cards"][1:])


def test_candidates_come_from_data_and_studies_only_annotate() -> None:
    result = run()
    options = result["cards"][1:]
    assert all(c["source"] == "data" for c in options)
    assert any(c["studies"] for c in options)  # Zaria: a studied sequence makes the cut


def test_rows_need_data_for_every_crop() -> None:
    result = run(past_seasons=[["maize"], ["soybean"]])
    assert result["cards"][0]["rows"]["forage"]["score"] is None  # soybean forage is a gap


def test_striga_risk_follows_field_history() -> None:
    assert run()["striga_risk"]["level"] == "High"
    assert run(past_seasons=[["soybean"]])["striga_risk"]["level"] == "Low"


def test_too_many_priorities_rejected() -> None:
    with pytest.raises(ValueError):
        Request(**FARM, priorities=list(PRIORITIES)[:4])


def test_tercile_scores_and_soil_base() -> None:
    import pandas as pd

    sand = pd.Series([10.0, 20.0, 30.0])
    assert tercile_score(sand, higher_is_worse=True).tolist() == [0, 1, 2]
    assert set(
        striga_soil_base(
            pd.DataFrame(
                {
                    "sand_content__0_20__value_mean": sand,
                    "nitrogen_total__0_20__value_mean": [3.0, 2.0, 1.0],
                    "carbon_organic__0_20__value_mean": [3.0, 2.0, 1.0],
                }
            )
        ).tolist()
    ) == {0, 1, 2}


def test_scoring_is_fast() -> None:
    import time

    start = time.perf_counter()
    run(priorities=["fewer_pests"])
    assert time.perf_counter() - start < 0.2  # PRD: under 200 ms


def test_bundle_scores_like_the_pipeline(tmp_path: Path) -> None:
    """The deployed app reads the CSV bundle; it must score exactly as the pipeline outputs do."""
    bundled = load_bundle(dest=export_bundle(dest=tmp_path))
    for lga in sorted(DATA.zone_of):
        req = Request(lga=lga, past_seasons=[["maize"], ["maize"]], season_type="Late start")
        as_json = [json.dumps(score_request(req, d), sort_keys=True) for d in (bundled, DATA)]
        assert as_json[0] == as_json[1]


def test_committed_bundle_is_current() -> None:
    """api/engine_inputs must be re-exported whenever a pipeline output changes."""
    committed = load_bundle()
    req = Request(lga="Kachia", past_seasons=[["maize"]], season_type="Typical")
    as_json = [json.dumps(score_request(req, d), sort_keys=True) for d in (committed, DATA)]
    assert as_json[0] == as_json[1], "run: python -m model.engine_data"


def test_season_fit_table_counts_years_long_enough() -> None:
    import pandas as pd

    types = pd.DataFrame(
        {
            "lga": ["A"] * 4,
            "season_type": ["Typical", "Typical", "Typical", "Late start"],
            "a2_length_days_value": [150.0, 120.0, None, 100.0],
        }
    )
    fit = season_fit_table(types, {"yam": 130.0, "cowpea": 100.0}).set_index(
        ["crop", "season_type"]
    )
    assert fit.loc[("yam", "Typical"), "fits_season_share"] == 0.5  # missing length left out
    assert fit.loc[("yam", "Typical"), "years"] == 2
    assert fit.loc[("yam", "Late start"), "fits_season_share"] == 0.0
    assert fit.loc[("cowpea", "Late start"), "fits_season_share"] == 1.0


def test_crop_without_water_balance_still_meets_the_season_rule() -> None:
    """Regression: yam (8 months, no A9 balance) used to top Ikara's late-start ranking."""
    result = run(lga="Ikara", season_type="Late start")
    assert all("yam" not in c["key"] for c in result["cards"][1:])
    assert "rainy season" in result["dropped"]["yam>lablab"]


def test_season_types_come_from_a5_and_unknown_ones_are_refused() -> None:
    import pandas as pd

    a5 = pd.read_csv("data/processed/models/a5/a5_lga_type_share.csv")
    assert set(DATA.season_types) == set(a5.columns) - {"lga"}
    with pytest.raises(ValueError, match="season type"):
        run(season_type="Not a type")


def test_no_risk_line_ever_reports_zero_of_ten() -> None:
    for lga in sorted(DATA.zone_of):
        for card in run(lga=lga)["cards"]:
            assert not any(" 0 of 10" in line for line in card["risks"]), (lga, card["risks"])


def test_every_card_has_the_prd_section_9_fields() -> None:
    """R4, R8, R10, R11, R12, R19, R32 on every card, in every LGA and season type."""
    for lga in sorted(DATA.zone_of):
        for season_type in DATA.season_types:
            result = run(lga=lga, season_type=season_type, priorities=["add_nitrogen"])
            for card in result["cards"]:
                crops = {c for season in card["seasons"] for c in season}
                assert {f["crop"] for f in card["crops"]} == crops  # R4
                assert card["why"].endswith(".")  # R8
                assert card["benefits"] and card["gaps"] and card["risks"]  # R10, R11, R12
                assert all(r["check_with"] for r in card["risks"])  # R12: who to ask
                assert card["trend_line"]  # R32
            for card in result["cards"][1:]:
                assert all(o["reason"] for o in card["opportunities"])  # R19


def test_legume_rotations_carry_the_phosphorus_and_sulphur_line() -> None:
    cards = run(past_seasons=[["maize"], ["soybean"]])["cards"]
    current = cards[0]["risks"]
    assert any("phosphorus and sulphur" in r["text"] for r in current)
    assert all(
        r["check_with"] == "a soil test through your extension officer"
        for r in current
        if "phosphorus" in r["text"]
    )


def test_no_priority_shows_a_notice_and_a_priority_clears_it() -> None:
    assert run()["notice"]
    assert run(priorities=["fewer_pests"])["notice"] is None


def test_water_line_states_need_and_how_often_short() -> None:
    line = run()["cards"][0]["water_line"]
    assert line.startswith("Maize needs about ") and "of 10 typical seasons" in line
