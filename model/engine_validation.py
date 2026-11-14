"""Check the data-led engine against published Kaduna and savanna rotation studies.

The engine ranks every candidate rotation from data alone. A study-backed sequence is a check:
if the data is right, those sequences should rank high, in the zones the studies come from,
more often than chance. Chance is measured by drawing random sets of candidate sequences of
the same size (a permutation test).
"""

import numpy as np
import pandas as pd

from model.engine_data import EngineData
from model.rotation_engine import Request, current_rotation, rank_candidates, striga_risk, weights


def study_sequences(data: EngineData) -> dict[str, list[list[str]]]:
    """Studied sequences the engine can generate (two single crops, both with rules)."""
    crops = set(data.crops.rules())
    return {
        k: t.seasons
        for k, t in data.rotations.templates.items()
        if t.status != "current_only"
        and len(t.seasons) == 2
        and all(len(s) == 1 and s[0] in crops for s in t.seasons)
    }


def rank_table(
    data: EngineData, farms: dict[str, list[list[str]]], season_types: list[str]
) -> pd.DataFrame:
    """Percentile of every candidate, per LGA, season type and farm (1 = ranked first)."""
    rows = []
    for lga in sorted(data.zone_of):
        for season_type in season_types:
            for farm, past in farms.items():
                req = Request(lga=lga, past_seasons=past, season_type=season_type)
                w = weights(req, data, striga_risk(req, data)[0])
                ranked, _ = rank_candidates(req, data, current_rotation(req, data), w)
                n = len(ranked)
                rows += [
                    {
                        "lga": lga,
                        "zone": data.zone_of[lga],
                        "season_type": season_type,
                        "farm": farm,
                        "sequence": rot.key,
                        "rank": i + 1,
                        "candidates": n,
                        "percentile": 1 - i / max(n - 1, 1),
                    }
                    for i, (rot, _) in enumerate(ranked)
                ]
    return pd.DataFrame(rows)


def study_check(ranks: pd.DataFrame, data: EngineData) -> pd.DataFrame:
    """For each studied sequence: mean percentile everywhere and in its own zones, top-2 share."""
    out = []
    for key, seasons in study_sequences(data).items():
        seq = f"{seasons[0][0]}>{seasons[1][0]}"
        mine = ranks[ranks["sequence"] == seq]
        home = mine[mine["zone"].isin(data.rotations.templates[key].best_fit)]
        out.append(
            {
                "study": key,
                "sequence": seq,
                "status": data.rotations.templates[key].status,
                "mean_percentile": mine["percentile"].mean(),
                "mean_percentile_home_zones": home["percentile"].mean(),
                "top2_share": (mine["rank"] <= 2).mean(),
                "dropped_share": 1 - len(mine) / len(ranks.groupby(["lga", "season_type", "farm"])),
            }
        )
    return pd.DataFrame(out)


def permutation_p(ranks: pd.DataFrame, studied: list[str], runs: int, seed: int) -> dict:
    """Is the studied sequences' mean percentile higher than random sets of the same size?"""
    by_seq = ranks.groupby("sequence")["percentile"].mean()
    observed = by_seq[by_seq.index.isin(studied)].mean()
    rng = np.random.default_rng(seed)
    pool = by_seq.to_numpy()
    k = int(by_seq.index.isin(studied).sum())
    null = np.array([rng.choice(pool, k, replace=False).mean() for _ in range(runs)])
    return {
        "studied_mean_percentile": float(observed),
        "random_mean": float(null.mean()),
        "p_value": float((np.sum(null >= observed) + 1) / (runs + 1)),
        "sequences": k,
    }
