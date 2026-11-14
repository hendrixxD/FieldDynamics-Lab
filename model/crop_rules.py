"""PRD A11: read-only helpers over the crop rules in config/crops.yaml.

The rules are data, not a model: every value carries a source id, and a value with no source
is absent and listed under `gaps`. These helpers turn the stored values into what the
rotation engine scores, without inventing anything the file does not hold.
"""

from model.settings import CropConfig, CropRules

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


def missing_fields(crop: str, cfg: CropConfig) -> set[str]:
    """Fields with no value, named as `gaps` names them. A price counts when the crop maps to a
    KDBS commodity sold by the mudu."""
    rules = cfg.rules()[crop]
    missing = {name for name in ("forage", "labour") if getattr(rules, name) is None}
    if not rules.pest_groups:
        missing.add("pest_groups")
    if rules.market.role is None:
        missing.add("market.role")
    if crop not in cfg.kdbs_commodities:
        missing.add("market.price")
    return missing


def fixes_nitrogen(crop: str, cfg: CropConfig) -> bool:
    """True for N-fixing legumes."""
    return cfg.rules()[crop].nitrogen.fixes
