"""
Requirement registry (spec sections 19, 47, 51, 53).

IMPORTANT — per spec section 53: these are DEMO/sample rule definitions for
schema, workflow, and UI development. They are NOT sourced from a verified
authoritative document and MUST NOT be treated as real compliance rules in
production. Each entry's `source_name` says so explicitly. Wiring in a real
authoritative source (e.g. a specific, dated policy document) means adding a
new RequirementVersion row with a real source_name/source_url/source_version
— the resolver and engine code do not change.

Every requirement carries `visual_verifiability` (spec section 50) so the
engine knows up front which checks can ever produce PASS/FAIL versus which
must always resolve to UNCERTAIN with a "not visually verifiable" reason.
"""

RULE_VERSION = "RULES_V1"
_DEMO_SOURCE = {
    "source_name": "DEMO_SAMPLE_RULESET (not an authoritative compliance source)",
    "source_url": None,
    "source_version": "demo-1",
}

REQUIREMENTS = [
    {
        "rule_id": "POLYBAG_PRESENCE",
        "version": RULE_VERSION,
        "name": "Polybag present and sealed",
        "description": "Product must be enclosed in a sealed polybag when polybag prep is required.",
        "applicability": {"prep_type_in": ["polybag", "polybag+label"]},
        "visual_verifiability": "VISUAL",
        "requirement_logic": {"check": "polybag"},
        **_DEMO_SOURCE,
    },
    {
        "rule_id": "SUFFOCATION_WARNING",
        "version": RULE_VERSION,
        "name": "Suffocation warning present and legible",
        "description": "A suffocation warning label must be present, visible, and legible when the polybag's opening dimension requires one.",
        "applicability": {"prep_type_in": ["polybag", "polybag+label"]},
        "visual_verifiability": "VISUAL",
        "requirement_logic": {"check": "warning"},
        **_DEMO_SOURCE,
    },
    {
        "rule_id": "FNSKU_PLACEMENT",
        "version": RULE_VERSION,
        "name": "FNSKU label placed correctly",
        "description": "FNSKU label must be present, flat, fully readable, and not placed across a seam or package edge.",
        "applicability": {"fnsku_required": True},
        "visual_verifiability": "VISUAL",
        "requirement_logic": {"check": "fnsku"},
        **_DEMO_SOURCE,
    },
    {
        "rule_id": "ORIGINAL_BARCODE_COVERAGE",
        "version": RULE_VERSION,
        "name": "Original manufacturer barcode covered",
        "description": "When required, the original manufacturer barcode must be covered/obscured by the FNSKU label or otherwise not scannable.",
        "applicability": {"barcode_coverage_required": True},
        "visual_verifiability": "VISUAL",
        "requirement_logic": {"check": "barcode_coverage"},
        **_DEMO_SOURCE,
    },
    {
        "rule_id": "EXPIRY_DATE_VISIBILITY",
        "version": RULE_VERSION,
        "name": "Expiry date visible and legible",
        "description": "Where an expiry date is required, it must remain visible and legible after prep.",
        "applicability": {"expiry_required": True},
        "visual_verifiability": "VISUAL",
        "requirement_logic": {"check": "expiry"},
        **_DEMO_SOURCE,
    },
    {
        "rule_id": "HANDLING_MARKS",
        "version": RULE_VERSION,
        "name": "Required handling marks present",
        "description": "Configured handling marks (e.g. FRAGILE) must be present and legible.",
        "applicability": {"handling_marks_not_empty": True},
        "visual_verifiability": "VISUAL",
        "requirement_logic": {"check": "handling_marks"},
        **_DEMO_SOURCE,
    },
    {
        "rule_id": "PACKAGING_MATERIAL_THICKNESS",
        "version": RULE_VERSION,
        "name": "Polybag material thickness (NOT visually verifiable)",
        "description": "Included to demonstrate section 50 handling: material thickness cannot be reliably established from a photograph.",
        "applicability": {"prep_type_in": ["polybag", "polybag+label"]},
        "visual_verifiability": "NOT_VISUALLY_VERIFIABLE",
        "requirement_logic": {"check": "not_visually_verifiable"},
        **_DEMO_SOURCE,
    },
]

REQUIREMENTS_BY_ID = {r["rule_id"]: r for r in REQUIREMENTS}
