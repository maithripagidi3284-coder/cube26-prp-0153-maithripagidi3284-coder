"""
Geometry engine (spec sections 10, 11, 12, 13). Provides GEOMETRIC evidence
(edge lines, seam lines, overlap ratios) about regions the VLM identified.
It never decides compliance — it only measures. The rule engine turns
measurements into PASS/FAIL/UNCERTAIN.

Thresholds here are documented CV heuristics, not compliance rules.
"""
import cv2
import numpy as np
from app.schemas import BoundingBox, GeometryEvidence

# Heuristic thresholds (engineering, not compliance) — see docs/ARCHITECTURE.md
EDGE_PROXIMITY_PX = 12       # bbox within this many px of a detected package edge => "on edge"
SEAM_OVERLAP_MIN_RATIO = 0.08  # fraction of bbox area intersecting a seam line's band
MIN_LINE_VOTES = 60          # Hough line detector vote threshold


def _detect_lines(gray: np.ndarray) -> list:
    """Looks for long, near-continuous straight edges (package seams/edges),
    deliberately tuned to NOT bridge across small gaps — a small maxLineGap
    keeps this from stitching printed text characters into a fake long
    'line', which is a common false-positive source for this heuristic."""
    edges = cv2.Canny(gray, 50, 150, apertureSize=3)
    min_len = max(200, min(gray.shape) // 3)
    lines = cv2.HoughLinesP(edges, 1, np.pi / 180, threshold=MIN_LINE_VOTES,
                             minLineLength=min_len, maxLineGap=3)
    return [] if lines is None else [l[0] for l in lines]


def _line_band_mask(shape, line, thickness=10) -> np.ndarray:
    mask = np.zeros(shape[:2], dtype=np.uint8)
    x1, y1, x2, y2 = line
    cv2.line(mask, (x1, y1), (x2, y2), 255, thickness=thickness)
    return mask


def _bbox_mask(shape, bbox: BoundingBox) -> np.ndarray:
    mask = np.zeros(shape[:2], dtype=np.uint8)
    cv2.rectangle(mask, (int(bbox.x1), int(bbox.y1)), (int(bbox.x2), int(bbox.y2)), 255, -1)
    return mask


def _interior_mask(shape, bbox: BoundingBox, inset: int = 16) -> np.ndarray:
    """Eroded interior of the bbox — deliberately excludes the label's own
    printed border/outline so a label's own edge is never mistaken for a
    package seam running underneath it (a real seam must cross deep into
    the label's interior, not just trace its own border)."""
    mask = np.zeros(shape[:2], dtype=np.uint8)
    x1, y1 = int(bbox.x1) + inset, int(bbox.y1) + inset
    x2, y2 = int(bbox.x2) - inset, int(bbox.y2) - inset
    if x2 <= x1 or y2 <= y1:
        return mask  # bbox too small to have a meaningful interior
    cv2.rectangle(mask, (x1, y1), (x2, y2), 255, -1)
    return mask


def analyze_region_geometry(object_id: str, image_id: str, inference_path: str,
                             bbox: BoundingBox | None,
                             package_bbox: BoundingBox | None = None) -> GeometryEvidence:
    """
    package_bbox: the detected PRODUCT/package region for this image, when
    available from the VLM evidence. When given, edge proximity is measured
    against the actual package boundary instead of the raw image border —
    a photo is almost never cropped exactly to the package, so the image
    border alone is a weak edge proxy. Falls back to the image border when
    no package region was detected (spec section 12: if the boundary can't
    be established, that should widen UNCERTAIN, not silently degrade
    accuracy without saying so — see the note added below).
    """
    if bbox is None:
        return GeometryEvidence(
            object_id=object_id, image_id=image_id, surface_type="unknown",
            notes=["No bounding box available — geometry cannot be computed."],
        )

    img = cv2.imread(inference_path)
    if img is None:
        return GeometryEvidence(
            object_id=object_id, image_id=image_id, surface_type="unknown",
            notes=["Image unreadable for geometry analysis."],
        )

    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    h, w = gray.shape[:2]
    lines = _detect_lines(gray)
    bbox_area = max(1.0, (bbox.x2 - bbox.x1) * (bbox.y2 - bbox.y1))
    bmask = _interior_mask(gray.shape, bbox)
    interior_area = max(1.0, float(np.count_nonzero(bmask)))

    # --- package edge proximity ---
    boundary_is_package = package_bbox is not None
    if package_bbox is not None:
        edge_distance = min(
            bbox.x1 - package_bbox.x1, bbox.y1 - package_bbox.y1,
            package_bbox.x2 - bbox.x2, package_bbox.y2 - bbox.y2,
        )
    else:
        # Coarse fallback: image border. Weaker signal — noted below.
        edge_distance = min(bbox.x1, bbox.y1, w - bbox.x2, h - bbox.y2)
    edge_overlap = edge_distance <= EDGE_PROXIMITY_PX

    # --- seam overlap: does the label bbox cross a strong straight line
    # that runs through (not just borders) the region? ---
    best_seam_ratio = 0.0
    notes = []
    for line in lines:
        x1, y1, x2, y2 = line
        # only count lines that actually pass near/through the bbox interior
        lmask = _line_band_mask(gray.shape, line, thickness=14)
        overlap = cv2.bitwise_and(bmask, lmask)
        ratio = float(np.count_nonzero(overlap)) / interior_area
        if ratio > best_seam_ratio:
            best_seam_ratio = ratio

    seam_overlap = best_seam_ratio >= SEAM_OVERLAP_MIN_RATIO

    # --- orientation via minAreaRect of the bbox region's strongest contour ---
    orientation = None
    crop = gray[max(0, int(bbox.y1)):int(bbox.y2), max(0, int(bbox.x1)):int(bbox.x2)]
    if crop.size > 0:
        _, thresh = cv2.threshold(crop, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        contours, _ = cv2.findContours(thresh, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        if contours:
            largest = max(contours, key=cv2.contourArea)
            if cv2.contourArea(largest) > 20:
                rect = cv2.minAreaRect(largest)
                orientation = float(rect[-1])

    boundary_label = "package boundary" if boundary_is_package else "image frame edge (no package boundary detected — weaker signal)"
    if edge_overlap and seam_overlap:
        surface_type = "seam"  # seam classification takes precedence for reporting
        notes.append(f"Region intersects both a detected straight line and the {boundary_label}.")
    elif edge_overlap:
        surface_type = "edge"
        notes.append(f"Region is within {edge_distance:.0f}px of the {boundary_label}.")
    elif seam_overlap:
        surface_type = "seam"
        notes.append(f"Region overlaps a detected seam-like line (overlap ratio {best_seam_ratio:.2f}).")
    elif len(lines) == 0:
        surface_type = "unknown"
        notes.append("No reliable lines detected — insufficient evidence for surface classification.")
    else:
        surface_type = "flat"
        notes.append("No significant edge or seam overlap detected near this region.")

    return GeometryEvidence(
        object_id=object_id, image_id=image_id, surface_type=surface_type,
        seam_overlap_ratio=round(best_seam_ratio, 3),
        edge_overlap=edge_overlap,
        edge_distance_px=round(float(edge_distance), 1),
        orientation_deg=orientation,
        notes=notes,
    )
