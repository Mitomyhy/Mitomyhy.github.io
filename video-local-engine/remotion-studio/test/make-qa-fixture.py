"""Generate neutral QA assets. Not user artwork and not a model result."""
from pathlib import Path
import json
from PIL import Image, ImageDraw, ImageFont, ImageFilter
import random

here = Path(__file__).resolve().parents[1] / 'qa'
here.mkdir(exist_ok=True)
im = Image.new('RGBA', (700, 900), (0, 0, 0, 0))
d = ImageDraw.Draw(im)
d.polygon([(320, 0), (340, 0), (357, 250), (330, 260)], fill=(247, 246, 239, 255))
d.polygon([(340, 0), (362, 0), (348, 250), (330, 260)], fill=(222, 221, 213, 255))
d.ellipse((87, 235, 613, 761), fill=(237, 225, 197, 250), outline=(255, 250, 229, 255), width=10)
d.ellipse((101, 249, 599, 747), outline=(179, 151, 101, 255), width=3)
font = ImageFont.truetype('C:/Windows/Fonts/arial.ttf', 64)
small = ImageFont.truetype('C:/Windows/Fonts/arial.ttf', 35)
d.text((350, 335), 'MITOM', font=font, fill='#284b38', anchor='mm')
d.text((350, 630), 'LISTING 2026', font=small, fill='#284b38', anchor='mm')
for x, y, size in [(260, 500, 72), (350, 465, 90), (445, 510, 65)]:
    d.rectangle((x-6, y+size*.6, x+6, y+size*.85), fill='#8e6c39')
    d.polygon([(x,y-size*.65), (x-size*.5,y+size*.65), (x+size*.5,y+size*.65)], fill='#38683e')
im.save(here / 'product.png')
rng = random.Random(42)
bg = Image.new('RGB', (1600, 900), '#8d7e63')
layer = Image.new('RGBA', bg.size)
ld = ImageDraw.Draw(layer)
for _ in range(100):
    x, y = rng.randrange(1600), rng.randrange(900)
    s = rng.randrange(7, 40)
    ld.ellipse((x-s,y-s,x+s,y+s), fill=(255, 225, 158, rng.randrange(80,200)))
ld.polygon([(1300,70),(1080,900),(1570,900)], fill=(25,70,39,180))
ld.polygon([(100,100),(-150,900),(350,900)], fill=(25,70,39,120))
bg = Image.alpha_composite(bg.convert('RGBA'), layer.filter(ImageFilter.GaussianBlur(11))).convert('RGB')
bg.save(here / 'background.png')
for orientation in ('landscape', 'portrait'):
    value = {'schemaVersion': 1, 'aspectRatio': '16:9' if orientation == 'landscape' else '9:16', 'durationSeconds': 5, 'resolution': 720 if orientation == 'landscape' else 1080, 'fps': 24, 'seed': 42, 'product': {'src': 'product.png', 'anchor': 'hanging', 'material': 'acrylic'}, 'background': {'src': 'background.png', 'theme': 'christmas'}, 'motion': {'orbitDegrees': 45, 'swayDegrees': 2.5, 'cycles': 1}, 'lighting': {'glint': 0.3}}
    (here / f'{orientation}.json').write_text(json.dumps(value, indent=2) + '\n')
wide = Image.new('RGBA', (1600, 400), (0, 0, 0, 0))
wd = ImageDraw.Draw(wide)
wd.rounded_rectangle((10, 10, 1590, 390), radius=60, fill='#e9ddba', outline='#6f542b', width=12)
wd.text((800, 210), 'WIDE PRODUCT - MITOM', font=font, fill='#284b38', anchor='mm')
wide.save(here / 'wide-product.png')
value = json.loads((here / 'landscape.json').read_text())
value['product']['src'] = 'wide-product.png'
value['motion']['orbitDegrees'] = 0
value['motion']['swayDegrees'] = 0
(here / 'wide-product.json').write_text(json.dumps(value, indent=2) + '\n')
print(here)
