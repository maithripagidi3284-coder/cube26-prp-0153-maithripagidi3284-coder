# Prep Manager — Evaluation Report

## Honest framing, up front

Spec section 39 asks for a held-out set of **at least 50 units with
independent human labels, ideally from two reviewers**. This repository
ships **3 synthetic fixture images** (`backend/tests/fixtures/`, generated
by `scripts/generate_demo_images.py`) with single-reviewer ground truth
written by the same person who wrote the rule engine — which is exactly
the labeling process spec section 39 warns against ("do not secretly
modify labels to match the model" — here there's no independent labeler at
all yet). **Do not read the numbers below as evidence of real-world
accuracy.** They exist to prove the evaluation *framework* — manifest
schema, runner, metrics, failure analysis — works correctly and reports
honestly, which is what spec section 53 asks sample data to be used for.

What would need to happen before these numbers meant anything: capture
50+ real warehouse photos across the scenario list in spec section 40,
get two independent human reviewers to label each check, record their
disagreements rather than resolving them algorithmically, and run
`evaluation/runner.py` against that manifest instead.

## Dataset composition (current, synthetic)

| unit_id | Scenario | Checks with ground truth |
|---|---|---|
| `good_unit` | Clean pass: sealed polybag, legible warning, flat FNSKU off any seam/edge | 4 |
| `fnsku_on_edge_unit` | Spec section 56's flagship demo case: FNSKU straddling the package edge | 4 |
| `missing_polybag_unit` | No polybag detected | 4 |

12 total check-evaluations across 3 units. This is far below evaluation
scale — see "Honest framing" above.

## Ground-truth methodology (current)

Single-reviewer (the implementer), against synthetic images constructed to
have an unambiguous correct answer (e.g. the FNSKU box in
`fnsku_on_edge_unit` was placed at pixel coordinates that deliberately
straddle the drawn package boundary). No train/evaluation separation is
meaningful yet since the rule engine has no learned parameters — it's
deterministic Python — but the *quality-gate and OCR/geometry thresholds*
in `quality/engine.py` and `geometry/engine.py` were tuned by looking at
these same 3 fixtures, which is a real methodological weakness: those
thresholds have not been validated against independent data and should be
re-tuned once a real dataset exists.

## Metrics (current run)

Run `python evaluation/runner.py` to reproduce (uses a simulated VLM
response by default since no `ANTHROPIC_API_KEY` is configured in this
environment — see `scripts/run_demo.py`'s module docstring for exactly
what's simulated and why; set the env var to use real Claude vision calls
instead). Latest run (`evaluation/latest_report.json`):

```json
{
  "total_check_evaluations": 12,
  "accuracy_across_all_checks": 1.0,
  "uncertain_rate_across_all_checks": 0.417,
  "total_false_passes": 0,
  "total_false_fails": 0
}
```

Per-check breakdown (see `evaluation/metrics.py::CheckMetrics` for the
full schema — precision/recall/F1 with FAIL as the positive class,
confusion matrix, uncertain rate, coverage) is in
`evaluation/latest_report.json` after running the runner.

**On the 41.7% uncertain rate**: this is not a bug to hide — one of the
four checks per unit (`PACKAGING_MATERIAL_THICKNESS`) is deliberately
`NOT_VISUALLY_VERIFIABLE` (spec section 50) and *always* returns
UNCERTAIN by design, which alone accounts for 3 of the 5 UNCERTAIN
outcomes. The other 2 are genuine evidence-insufficiency UNCERTAINs from
the `missing_polybag_unit` case (warning/FNSKU checks correctly declining
to guess when the primary detection they depend on is itself missing).

**On 0 false passes / 0 false fails**: with 3 hand-constructed synthetic
units this is expected, not impressive — it mainly confirms the pipeline
and scoring code are wired correctly, not that the system is accurate.

## False positives / false negatives

None observed in the current run (see above caveat). The metrics framework
tracks these per-check (`false_fail_count`, `false_pass_count` in
`CheckMetrics.to_dict()`) so that once a real dataset exists, the
**false-pass rate is the number to watch first** — a check that says PASS
when the real answer is FAIL is the costliest failure mode for a
compliance system (a real defect ships), which is why `rules/engine.py`'s
`check_*` functions are written to prefer UNCERTAIN over a confident PASS
whenever identifier/geometry evidence is anything less than strong.

## Failure modes observed / anticipated

From `evaluation/runner.py::_guess_cause` and hands-on debugging while
building this repo (documented here rather than hidden):

| Failure mode | What happened | Fix applied |
|---|---|---|
| Geometry false-positive: label's own printed border read as a "seam" | The FNSKU label's own rectangle outline sits exactly on its own bbox edge, and Hough line detection flagged it as a seam crossing the label | Restricted seam-overlap testing to a 16px-inset *interior* of the bbox, excluding the label's own border |
| Geometry false-positive: OCR text characters bridged into a fake "line" | `HoughLinesP`'s `maxLineGap` was originally 10px, long enough to bridge gaps between letters in printed text into a spurious long line | Reduced `maxLineGap` to 3 and raised `minLineLength` to `max(200, image_min_dim/3)` |
| OCR misses small label text entirely | Tesseract's default settings don't reliably read small text in a full-frame photo | Upscale 2x before OCR; use `--psm 3` (not `--psm 11`, which was empirically worse on this content) |
| OCR contrast enhancement hurting already-good images | Unconditional `equalizeHist` over-stretched contrast on well-exposed synthetic images and distorted thin character strokes | Applied conditionally, only when measured contrast (`np.std`) is below a threshold |
| Edge detection using image border instead of package boundary | A photo is rarely cropped exactly to the package, so image-border proximity is a weak/wrong proxy for "near the package edge" | `geometry/engine.py` now accepts an optional `package_bbox` (the VLM's `PRODUCT` detection) and measures edge distance against that when available, falling back to the image border with an explicit lower-confidence note otherwise |

Each of these was caught by actually running the pipeline against
generated fixtures during development, not assumed — consistent with spec
section 42's ask for a real failure-analysis process rather than an
unsupported accuracy claim.

## Latency

`GET /metrics` reports live average/p50/p95 latency once units have been
analyzed against a real API key; not populated in this report since this
environment has no `ANTHROPIC_API_KEY` configured. Expect the VLM call to
dominate end-to-end latency (a single multi-image multimodal call), with
local OCR/barcode/geometry adding low-hundreds-of-milliseconds on CPU for
images at typical warehouse-photo resolution.

## Cost estimate

One VLM call per unit (spec sections 29, 43) is the only paid step; OCR
(Tesseract), barcode decode (zbar), and geometry (OpenCV) are free local
compute. Cost per unit ≈ one multimodal API call sized by
`(number of images × image tokens) + prompt tokens`; no per-check
multiplier, which is the entire point of the one-call-per-unit
requirement.

## Known limitations

- Evaluation dataset is 3 synthetic units, not the 50+ real-photo held-out
  set spec section 39 requires — see "Honest framing" above.
- Ground truth has one labeler, not two independent reviewers with
  recorded disagreements.
- Quality-gate and geometry thresholds were tuned against the same
  fixtures used to "pass" them — needs re-validation on independent data.
- OCR backend is Tesseract, not the PaddleOCR the spec suggests (available
  in this environment; swapping backends only touches `ocr/engine.py`).
- No adversarial test images (reflections, glare, crumpled bags, rotated
  labels — spec section 41) exist yet.
- Vision calls in this development environment were exercised via a
  clearly-labeled simulated response (see `scripts/run_demo.py`) because
  no `ANTHROPIC_API_KEY` is configured here; the real call path
  (`vision/client.py`) is implemented and was verified independently via
  its fail-open behavior (a real, unmocked API call attempt that
  correctly failed closed to `PENDING_REVIEW`), but has not yet been
  exercised end-to-end with a live successful response.
