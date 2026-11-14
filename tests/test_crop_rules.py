from model import crop_rules as cr
from model.settings import load_crop_config

CFG = load_crop_config()


def test_every_cited_source_is_listed() -> None:
    for crop, rules in CFG.rules().items():
        assert cr.source_ids(rules) <= set(CFG.sources), crop


def test_every_pest_group_is_defined() -> None:
    for crop, rules in CFG.rules().items():
        assert set(rules.pest_groups) <= set(CFG.pest_groups), crop


def test_gaps_match_the_fields_that_have_no_value() -> None:
    for crop, rules in CFG.rules().items():
        assert cr.missing_fields(rules) == set(rules.gaps), crop


def test_only_legumes_fix_nitrogen() -> None:
    legumes = {k for k, r in CFG.rules().items() if r.family == "Fabaceae"}
    fixers = {k for k in CFG.rules() if cr.fixes_nitrogen(k, CFG)}
    assert fixers == legumes == {"soybean", "cowpea", "groundnut"}


def test_price_band_needs_the_same_market_and_date() -> None:
    assert cr.price_band("cowpea", CFG) == "high"  # 175,000 against 85,000
    assert cr.price_band("soybean", CFG) == "medium"
    assert cr.price_band("groundnut", CFG) is None  # no Kaduna price yet
