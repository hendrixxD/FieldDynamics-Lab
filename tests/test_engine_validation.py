"""A13: the data's own ranking puts the published rotations above chance."""

from model import engine_validation as ev
from model.engine_data import load_engine_data

DATA = load_engine_data()


def test_studied_rotations_rank_above_chance() -> None:
    farms = {"continuous maize": [["maize"], ["maize"]]}
    ranks = ev.rank_table(DATA, farms, ["Typical"])
    seqs = [f"{s[0][0]}>{s[1][0]}" for s in ev.study_sequences(DATA).values()]
    assert ev.permutation_p(ranks, seqs, 500, 0)["studied_mean_percentile"] > 0.5
