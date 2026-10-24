"""Asymmetric synthetic canvases with uneven thumbnail rounding; no user art."""
from pathlib import Path
import json
import shutil
import sys
from PIL import Image, ImageDraw, ImageOps

folder = Path(sys.argv[1])
folder.mkdir(parents=True, exist_ok=True)
items = []
for name, size in (("wide", (3001, 997)), ("portrait", (701, 1903))):
    width, height = size
    shapes = [(0, int(height*.53), int(width*.15), int(height*.22), "#2167c3"),
              (int(width*.32), int(height*.12), int(width*.27), int(height*.2), "#df4728"),
              (int(width*.67), int(height*.55), int(width*.18), int(height*.29), "#23a565")]
    image = Image.new("RGB", size, "#fffef4")
    draw = ImageDraw.Draw(image)
    for x, y, w, h, color in shapes:
        draw.rectangle((x, y, x+w-1, y+h-1), fill=color)
    image.save(folder / (name + ".png"))
    image.thumbnail((1600, 1600), Image.Resampling.LANCZOS)
    image.save(folder / (name + "-preview.png"))
    paths = [f'<path d="M0 0H{width}V{height}H0Z" fill="#fffef4"/>']
    for x, y, w, h, color in shapes:
        paths.append(f'<path d="M{x} {y}H{x+w}V{y+h}H{x}Z" fill="{color}"/>')
    svg = f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">' + "".join(paths) + "</svg>"
    (folder / (name + ".svg")).write_text(svg, encoding="utf-8")
    items.append({"id": name, "name": name + ".png", "info": {"width": width, "height": height}})
if len(sys.argv) == 4:
    source, vector = Path(sys.argv[2]), Path(sys.argv[3])
    with Image.open(source) as opened:
        image = ImageOps.exif_transpose(opened).convert("RGBA")
        width, height = image.size
        image.thumbnail((1600, 1600), Image.Resampling.LANCZOS)
        image.save(folder / "user-source-preview.png")
    shutil.copyfile(vector, folder / "user-source.svg")
    items.append({"id": "user-source", "name": source.name, "source_path": str(source),
                  "info": {"width": width, "height": height}})
(folder / "fixtures.json").write_text(json.dumps(items), encoding="utf-8")
