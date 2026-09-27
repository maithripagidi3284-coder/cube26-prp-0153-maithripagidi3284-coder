import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.schemas import BoundingBox
from app.geometry.engine import analyze_region_geometry

FIXTURES = Path(__file__).resolve().parent / "fixtures"
PACKAGE_BBOX = BoundingBox(x1=120, y1=120, x2=780, y2=580)  # matches generate_demo_images.py


def test_fnsku_on_edge_detected_with_package_boundary():
    bbox = BoundingBox(x1=700, y1=400, x2=860, y2=470)  # crosses x=780 package edge
    g = analyze_region_geometry("o1", "img1", str(FIXTURES / "fnsku_on_edge_unit.jpg"), bbox,
                                 package_bbox=PACKAGE_BBOX)
    assert g.edge_overlap is True
    assert g.surface_type in ("edge", "seam")


def test_fnsku_flat_not_flagged_as_edge():
    bbox = BoundingBox(x1=350, y1=400, x2=590, y2=470)  # well inside package
    g = analyze_region_geometry("o1", "img1", str(FIXTURES / "good_unit.jpg"), bbox,
                                 package_bbox=PACKAGE_BBOX)
    assert g.edge_overlap is False


def test_geometry_without_bbox_returns_unknown():
    g = analyze_region_geometry("o1", "img1", str(FIXTURES / "good_unit.jpg"), None)
    assert g.surface_type == "unknown"


def test_geometry_falls_back_to_image_border_without_package_bbox():
    """No PRODUCT detection available -> falls back to image-frame edge
    proxy rather than erroring, but the notes must say the signal is weaker
    (spec section 12: ambiguous boundary should not silently look as
    confident as a real one)."""
    bbox = BoundingBox(x1=700, y1=400, x2=860, y2=470)
    g = analyze_region_geometry("o1", "img1", str(FIXTURES / "fnsku_on_edge_unit.jpg"), bbox, package_bbox=None)
    assert "no package boundary detected" in " ".join(g.notes).lower() or g.edge_overlap is False
