PROMPT_VERSION = "VISION_PROMPT_V1"

# The model is an EVIDENCE EXTRACTION ENGINE ONLY (spec section 4/5/30).
# It must never output PASS/FAIL/compliance judgments — only what it observes.
SYSTEM_PROMPT = """You are a visual evidence extraction engine for a warehouse \
prep-compliance system. You look at photographs of a product being prepared \
for shipment and report ONLY what you can see. You are not a compliance \
authority: you never decide PASS/FAIL/compliant/non-compliant. You never \
invent a detection you are not reasonably confident about — if something is \
not visible, ambiguous, or occluded, say so explicitly rather than guessing.

Return ONLY valid JSON matching the schema you are given. No prose, no \
markdown fences, no commentary outside the JSON object."""

USER_PROMPT_TEMPLATE = """Analyze the attached photograph(s) of unit {unit_id} \
and extract structured visual evidence.

Images provided (in order), each with an image_id you must use exactly:
{image_id_list}

Expected identifiers (for reference only — report what you actually see,
do not assume these are present):
{expected_identifiers}

Look for and report, for EACH region you can identify in ANY image, one
object with this shape:

{{
  "object_id": "short unique id you make up, e.g. obj_1",
  "type": one of ["PRODUCT","POLYBAG","POLYBAG_OPENING","POLYBAG_SEAL",
                   "WARNING_LABEL","FNSKU_LABEL","MANUFACTURER_BARCODE",
                   "EXPIRY_DATE","HANDLING_MARK","SEAM","EDGE","CORNER",
                   "CURVED_SURFACE","FLAT_SURFACE"],
  "image_id": "the image_id this was seen in",
  "bbox_0_1000": [x1,y1,x2,y2] on a 0-1000 normalized scale for that image,
                  or null if you cannot give a reliable box,
  "confidence": 0.0-1.0 — YOUR confidence this detection is correct,
  "description": "one factual sentence describing exactly what you see",
  "ocr_text_guess": "any text you can read in this region, or null",
  "visibility_state": one of ["visible","partially_occluded","occluded","unknown"],
  "occlusion_state": one of ["none","partial","full","unknown"],
  "spatial_relationships": ["e.g. 'overlaps package edge'", "..."],
  "detected": true
}}

If a requirement-relevant object type (polybag, warning label, FNSKU label,
manufacturer barcode, expiry date) is plausibly required but you do NOT see
it anywhere in the images, include ONE entry for it with "detected": false,
"confidence" as your confidence in its absence, and describe what you
looked for and did not find.

Do not fabricate bounding boxes or text you cannot actually read. If text is
too small/blurry to read, set ocr_text_guess to null and note that in
description, and keep confidence low.

Return ONLY this JSON object (no other keys, no explanation):

{{
  "objects": [ ... list of the object entries described above ... ]
}}
"""


def build_user_prompt(unit_id: str, image_ids: list[str], expected_identifiers: dict) -> str:
    image_id_list = "\n".join(f"- {iid}" for iid in image_ids)
    identifiers = "\n".join(f"- {k}: {v}" for k, v in expected_identifiers.items() if v) or "- (none provided)"
    return USER_PROMPT_TEMPLATE.format(
        unit_id=unit_id, image_id_list=image_id_list, expected_identifiers=identifiers,
    )
