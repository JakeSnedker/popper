"""Render the Popper desktop icon (gradient 'P') to assets/popper.ico."""

import os

from PIL import Image, ImageDraw, ImageFont

SIZE = 256
img = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
draw = ImageDraw.Draw(img)

# rounded-square gradient background: accent blue -> violet
top = (91, 141, 239)   # #5b8def
bot = (139, 92, 246)   # violet-500
for y in range(SIZE):
    t = y / SIZE
    r = int(top[0] + (bot[0] - top[0]) * t)
    g = int(top[1] + (bot[1] - top[1]) * t)
    b = int(top[2] + (bot[2] - top[2]) * t)
    draw.line([(0, y), (SIZE, y)], fill=(r, g, b, 255))

# mask to rounded rect
mask = Image.new("L", (SIZE, SIZE), 0)
ImageDraw.Draw(mask).rounded_rectangle([0, 0, SIZE - 1, SIZE - 1], radius=56, fill=255)
img.putalpha(mask)

# draw "P"
draw = ImageDraw.Draw(img)
font = None
for path in (
    "C:/Windows/Fonts/segoeuib.ttf",
    "C:/Windows/Fonts/arialbd.ttf",
    "C:/Windows/Fonts/arial.ttf",
):
    if os.path.exists(path):
        font = ImageFont.truetype(path, 170)
        break
if font is None:
    font = ImageFont.load_default()

text = "P"
box = draw.textbbox((0, 0), text, font=font)
w, h = box[2] - box[0], box[3] - box[1]
draw.text(((SIZE - w) / 2 - box[0], (SIZE - h) / 2 - box[1]), text, fill=(255, 255, 255, 255), font=font)

out = os.path.join(os.path.dirname(os.path.dirname(__file__)), "assets")
os.makedirs(out, exist_ok=True)
ico = os.path.join(out, "popper.ico")
img.save(ico, sizes=[(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
print("wrote", ico)
