"""
Generates synthetic package photos for the demo + test fixtures, since we
don't have a real warehouse photo dataset in this environment. Spec section
53 explicitly says sample data is for workflow/schema/UI development only —
these images exist to prove the pipeline works end-to-end, not as a
substitute for the real held-out evaluation set (spec section 39).
"""
import os
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

OUT = Path(__file__).resolve().parent.parent / "backend" / "tests" / "fixtures"
OUT.mkdir(parents=True, exist_ok=True)


def _font(size=28):
    try:
        return ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", size)
    except Exception:
        return ImageFont.load_default()


def make_good_unit():
    """Polybag + warning + FNSKU flat on a clean face, off any seam/edge."""
    img = Image.new("RGB", (900, 700), (150, 150, 150))
    d = ImageDraw.Draw(img)
    # product box
    d.rectangle([120, 120, 780, 580], outline=(40, 40, 40), width=6, fill=(190, 190, 190))
    # polybag: translucent overlay with a visible seal line near top
    d.rectangle([100, 100, 800, 600], outline=(120, 170, 220), width=4)
    d.line([100, 140, 800, 140], fill=(80, 120, 180), width=6)  # seal
    # warning label block, centered, away from edges
    d.rectangle([320, 200, 620, 300], fill=(255, 255, 255), outline=(0, 0, 0), width=3)
    d.text((335, 210), "WARNING", font=_font(30), fill=(200, 0, 0))
    d.text((335, 250), "SUFFOCATION HAZARD - KEEP AWAY FROM CHILDREN", font=_font(14), fill=(0, 0, 0))
    # FNSKU label, flat, well inside the face
    d.rectangle([350, 400, 590, 470], fill=(255, 255, 255), outline=(0, 0, 0), width=3)
    d.text((365, 415), "FNSKU X0DEMO1234", font=_font(19), fill=(0, 0, 0))
    img.save(OUT / "good_unit.jpg", quality=95)


def make_fnsku_on_edge_unit():
    img = Image.new("RGB", (900, 700), (150, 150, 150))
    d = ImageDraw.Draw(img)
    d.rectangle([120, 120, 780, 580], outline=(40, 40, 40), width=6, fill=(190, 190, 190))
    d.rectangle([100, 100, 800, 600], outline=(120, 170, 220), width=4)
    d.line([100, 140, 800, 140], fill=(80, 120, 180), width=6)
    d.rectangle([320, 200, 620, 300], fill=(255, 255, 255), outline=(0, 0, 0), width=3)
    d.text((335, 210), "WARNING", font=_font(30), fill=(200, 0, 0))
    d.text((335, 250), "SUFFOCATION HAZARD - KEEP AWAY FROM CHILDREN", font=_font(14), fill=(0, 0, 0))
    # FNSKU straddling the right package edge (120..780 boundary at x=780)
    d.rectangle([700, 400, 860, 470], fill=(255, 255, 255), outline=(0, 0, 0), width=3)
    d.text((712, 415), "FNSKU X0DEMO1234", font=_font(17), fill=(0, 0, 0))
    img.save(OUT / "fnsku_on_edge_unit.jpg", quality=95)


def make_missing_polybag_unit():
    img = Image.new("RGB", (900, 700), (150, 150, 150))
    d = ImageDraw.Draw(img)
    d.rectangle([120, 120, 780, 580], outline=(40, 40, 40), width=6, fill=(190, 190, 190))
    d.rectangle([350, 400, 590, 470], fill=(255, 255, 255), outline=(0, 0, 0), width=3)
    d.text((365, 410), "FNSKU X0DEMO1234", font=_font(20), fill=(0, 0, 0))
    img.save(OUT / "missing_polybag_unit.jpg", quality=95)


def make_blurry_unit():
    from PIL import ImageFilter
    img = Image.new("RGB", (900, 700), (235, 235, 235))
    d = ImageDraw.Draw(img)
    d.rectangle([120, 120, 780, 580], outline=(40, 40, 40), width=6, fill=(250, 250, 250))
    d.rectangle([320, 200, 620, 300], fill=(255, 255, 255), outline=(0, 0, 0), width=3)
    d.text((335, 250), "SUFFOCATION HAZARD", font=_font(14), fill=(0, 0, 0))
    img = img.filter(ImageFilter.GaussianBlur(radius=14))
    img.save(OUT / "blurry_unit.jpg", quality=95)


if __name__ == "__main__":
    make_good_unit()
    make_fnsku_on_edge_unit()
    make_missing_polybag_unit()
    make_blurry_unit()
    print(f"Wrote demo fixtures to {OUT}")
