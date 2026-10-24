"""Compare displayed raster/vector coordinates, independent of tracing quality."""
from pathlib import Path
import json
import sys
from PIL import Image, ImageChops

folder = Path(sys.argv[1])
results = []
for case in json.loads((folder / "shot-cases.json").read_text(encoding="utf-8")):
    original = Image.open(folder / (case["name"] + "-original.png")).convert("RGB")
    vector = Image.open(folder / (case["name"] + "-vector.png")).convert("RGB")
    compare = Image.open(folder / (case["name"] + "-compare.png")).convert("RGB")
    width, height = original.size
    differences = []
    for target in (vector, compare):
        channels = ImageChops.difference(original, target).split()
        maximum = ImageChops.lighter(ImageChops.lighter(channels[0], channels[1]), channels[2])
        pixels = list(maximum.getdata())
        significant = sum(value > 35 for value in pixels)
        ratio = significant / (width * height)
        if case.get("fidelityIndependent", True):
            assert ratio < .012, (case["name"], ratio)
        differences.append(round(ratio, 6))
    # A vector may never paint beyond the shared source-canvas bounds.
    frame = case["frame"]
    diff = ImageChops.difference(original, vector)
    outside = []
    for y in range(height):
        for x in range(width):
            if not (frame["x"]-2 <= x <= frame["x"]+frame["width"]+2
                    and frame["y"]-2 <= y <= frame["y"]+frame["height"]+2):
                if max(diff.getpixel((x, y))) > 10:
                    outside.append((x, y))
    assert not outside, (case["name"], "paint outside source canvas", outside[:5])
    results.append({"case": case["name"], "significant_difference_fraction": differences,
                    "outside_canvas_paint": len(outside)})
clip_checks = []
for case in json.loads((folder / "clip-cases.json").read_text(encoding="utf-8")):
    image = Image.open(folder / ("clip-" + str(case["split"]) + ".png")).convert("RGB")
    width, height = image.size
    frame = case["frame"]
    split_x = width * case["split"] / 100
    checked = 0
    bad = 0
    for y in range(height):
        for x in range(width):
            # Ignore only antialiased boundary pixels, never whole regions.
            if min(abs(x-frame["x"]), abs(x-frame["x"]-frame["width"]),
                   abs(y-frame["y"]), abs(y-frame["y"]-frame["height"]), abs(x-split_x)) < 2:
                continue
            inside = frame["x"] <= x+.5 <= frame["x"]+frame["width"] and frame["y"] <= y+.5 <= frame["y"]+frame["height"]
            expected_purple = inside and x+.5 < split_x
            pixel = image.getpixel((x, y))
            actual_purple = max(abs(value-color) for value, color in zip(pixel, (160, 32, 240))) < 8
            bad += actual_purple != expected_purple
            checked += 1
    assert bad == 0, (case["split"], "incorrect transformed compare clip", bad, checked)
    clip_checks.append({"split": case["split"], "checked_pixels": checked, "incorrect_pixels": bad})
print(json.dumps({"alignment": results, "transformed_compare_clip": clip_checks}))
