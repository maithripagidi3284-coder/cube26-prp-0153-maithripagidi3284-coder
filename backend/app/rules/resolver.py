"""
Requirement resolution (spec section 20). Turns product/work-order metadata
into the concrete set of rules that apply to THIS unit, each one traceable
back to its rule_id/version in the registry.
"""
from dataclasses import dataclass, field
from app.rules.registry import REQUIREMENTS


@dataclass
class ResolvedRequirement:
    rule_id: str
    version: str
    name: str
    visual_verifiability: str
    check: str
    source_name: str
    source_url: str | None
    source_version: str


@dataclass
class ResolvedRequirementSet:
    unit_id: str
    requirements: list[ResolvedRequirement] = field(default_factory=list)
    handling_marks: list[str] = field(default_factory=list)


def resolve_requirements(unit_id: str, product_meta: dict) -> ResolvedRequirementSet:
    """
    product_meta expected keys (all optional):
      prep_type: str
      fnsku: str | None
      manufacturer_barcode: str | None
      barcode_coverage_required: bool
      expiry_required: bool
      handling_marks: list[str]
    """
    prep_type = product_meta.get("prep_type", "")
    fnsku_required = bool(product_meta.get("fnsku"))
    barcode_coverage_required = bool(product_meta.get("barcode_coverage_required", False))
    expiry_required = bool(product_meta.get("expiry_required", False))
    handling_marks = product_meta.get("handling_marks") or []
    selected_rule_ids = product_meta.get("rule_ids")
    selected_rule_ids = set(selected_rule_ids or []) if selected_rule_ids else None

    resolved: list[ResolvedRequirement] = []
    for r in REQUIREMENTS:
        if selected_rule_ids is not None and r["rule_id"] not in selected_rule_ids:
            continue
        appl = r["applicability"]
        applies = False
        if "prep_type_in" in appl and prep_type in appl["prep_type_in"]:
            applies = True
        if appl.get("fnsku_required") and fnsku_required:
            applies = True
        if appl.get("barcode_coverage_required") and barcode_coverage_required:
            applies = True
        if appl.get("expiry_required") and expiry_required:
            applies = True
        if appl.get("handling_marks_not_empty") and len(handling_marks) > 0:
            applies = True
        if not applies:
            continue
        resolved.append(ResolvedRequirement(
            rule_id=r["rule_id"], version=r["version"], name=r["name"],
            visual_verifiability=r["visual_verifiability"],
            check=r["requirement_logic"]["check"],
            source_name=r["source_name"], source_url=r["source_url"],
            source_version=r["source_version"],
        ))

    return ResolvedRequirementSet(unit_id=unit_id, requirements=resolved, handling_marks=handling_marks)
