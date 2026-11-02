"""Draw dualchat.ico: a chat bubble with a play button (YouTube red) and two bars (Twitch purple).
Build-time only, needs Pillow: python make_icon.py"""
from pathlib import Path

from PIL import Image, ImageDraw

S = 1024  # drawn large, then downscaled so small sizes stay smooth
RED, PURPLE, WHITE = (230, 33, 23, 255), (145, 70, 255, 255), (255, 255, 255, 255)
HERE = Path(__file__).resolve().parent


def p(*xy):
    return [round(v * S) for v in xy]


bg = Image.new("RGBA", (S, S), RED)
ImageDraw.Draw(bg).polygon(p(1, 0, 1, 1, 0, 1), fill=PURPLE)  # diagonal split: red top-left, purple bottom-right
mask = Image.new("L", (S, S), 0)
ImageDraw.Draw(mask).rounded_rectangle(p(0, 0, 1, 1), radius=round(0.22 * S), fill=255)
icon = Image.new("RGBA", (S, S), (0, 0, 0, 0))
icon.paste(bg, mask=mask)

d = ImageDraw.Draw(icon)
d.rounded_rectangle(p(0.15, 0.2, 0.85, 0.7), radius=round(0.11 * S), fill=WHITE)  # chat bubble
d.polygon(p(0.28, 0.66, 0.28, 0.85, 0.47, 0.66), fill=WHITE)  # bubble tail
d.polygon(p(0.29, 0.31, 0.29, 0.59, 0.51, 0.45), fill=RED)  # play button
for x in (0.6, 0.71):  # Twitch-style "eyes"
    d.rounded_rectangle(p(x, 0.32, x + 0.065, 0.53), radius=round(0.02 * S), fill=PURPLE)

icon.resize((256, 256), Image.Resampling.LANCZOS).save(
    HERE / "dualchat.ico", sizes=[(n, n) for n in (16, 20, 24, 32, 40, 48, 64, 128, 256)])
(HERE / "assets").mkdir(exist_ok=True)
icon.resize((512, 512), Image.Resampling.LANCZOS).save(HERE / "assets" / "icon.png")  # README + social preview
print("wrote dualchat.ico and assets/icon.png")
