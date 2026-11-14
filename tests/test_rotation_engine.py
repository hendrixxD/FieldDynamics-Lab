import pytest

from model.engine_data import load_engine_data, striga_soil_base, tercile_score
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
        {"season_type": "Short and dry"},
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
