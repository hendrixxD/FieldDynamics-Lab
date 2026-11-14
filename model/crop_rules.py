"""PRD A11: read-only helpers over the crop rules in config/crops.yaml.

The rules are data, not a model: every value carries a source id, and a value with no source
is absent and listed under `gaps`. These helpers turn the stored values into what the
rotation engine scores, without inventing anything the file does not hold.
"""

import re
from typing import Literal

from model.settings import CropConfig, CropRules, Range

Band = Literal["high", "medium", "low"]
SOURCE_FIELDS = ("source", "role_source")


def source_ids(rules: CropRules) -> set[str]:
    """Every source id a crop's rules cite (a field may cite several, separated by ';')."""
    found: set[str] = set()

    def walk(value: object) -> None:
        if isinstance(value, dict):
            for key, item in value.items():
                if key in SOURCE_FIELDS and isinstance(item, str):
                    found.update(s.strip() for s in item.split(";"))
                else:
                    walk(item)

    walk(rules.model_dump())
    return found


def missing_fields(rules: CropRules) -> set[str]:
    """Fields with no value, named as `gaps` names them."""
    missing = {name for name in ("forage", "labour") if getattr(rules, name) is None}
    if not rules.pest_groups:
        missing.add("pest_groups")
    if rules.market.role is None:
        missing.add("market.role")
    if rules.market.price is None:
        missing.add("market.price")
    return missing


def price_band(crop: str, cfg: CropConfig, reference: str = "maize") -> Band | None:
    """Price relative to maize at the same market and date; None if either price is missing.

    Bands come from cfg.price_bands. Uses the midpoint of each price range.
    """
    rules, ref = cfg.rules()[crop], cfg.rules()[reference]
    price, ref_price = rules.market.price, ref.market.price
    if price is None or ref_price is None or price.date != ref_price.date:
        return None
    if _market_name(price.market) != _market_name(ref_price.market):
        return None
    ratio = _mid(price.ngn_per_100kg) / _mid(ref_price.ngn_per_100kg)
    if ratio >= cfg.price_bands["high"]:
        return "high"
    return "low" if ratio < cfg.price_bands["low"] else "medium"


def fixes_nitrogen(crop: str, cfg: CropConfig) -> bool:
    """True for N-fixing legumes."""
    return cfg.rules()[crop].nitrogen.fixes


def _mid(r: Range) -> float:
    if r.low is not None and r.high is not None:
        return (r.low + r.high) / 2
    if r.mean is None:
        raise ValueError("price range has neither low/high nor mean")
    return r.mean


def _market_name(label: str) -> str:
    """Market name without the bracketed variety note, e.g. 'Anchau, Kubau LGA'."""
    return re.sub(r"\s*\(.*\)$", "", label)
