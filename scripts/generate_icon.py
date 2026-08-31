"""Generate the reproducible JARVIS Windows icon from the HUD color system."""

from __future__ import annotations

import math
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "desktop" / "assets"
SIZE = 256


def point(cx: float, cy: float, radius: float, degrees: float) -> tuple[float, float]:
    angle = math.radians(degrees)
    return cx + radius * math.cos(angle), cy + radius * math.sin(angle)


def main() -> None:
    ASSETS.mkdir(parents=True, exist_ok=True)
    image = Image.new("RGBA", (SIZE, SIZE), (3, 12, 17, 255))
    draw = ImageDraw.Draw(image)
    cyan = (80, 229, 255, 255)
    soft = (32, 151, 179, 210)
    faint = (22, 75, 91, 220)
    draw.rounded_rectangle((3, 3, 252, 252), radius=48, fill=(4, 17, 24, 255), outline=faint, width=5)
    draw.ellipse((29, 29, 227, 227), outline=faint, width=4)
    draw.arc((42, 42, 214, 214), 205, 335, fill=cyan, width=7)
    draw.arc((42, 42, 214, 214), 25, 155, fill=soft, width=7)
    draw.arc((60, 60, 196, 196), 105, 255, fill=soft, width=4)
    draw.arc((60, 60, 196, 196), 285, 75, fill=cyan, width=4)
    for degrees in (0, 120, 240):
        cx, cy = point(128, 128, 87, degrees)
        triangle = [point(cx, cy, 11, degrees), point(cx, cy, 7, degrees + 135), point(cx, cy, 7, degrees - 135)]
        draw.polygon(triangle, fill=cyan)
    draw.ellipse((82, 82, 174, 174), fill=(5, 25, 34, 255), outline=cyan, width=3)
    font_path = Path("C:/Windows/Fonts/segoeuib.ttf")
    font = ImageFont.truetype(str(font_path), 78) if font_path.is_file() else ImageFont.load_default()
    bounds = draw.textbbox((0, 0), "J", font=font)
    width = bounds[2] - bounds[0]
    height = bounds[3] - bounds[1]
    draw.text((128 - width / 2, 126 - height / 2 - bounds[1]), "J", font=font, fill=cyan)
    image.save(ASSETS / "jarvis.png")
    image.save(
        ASSETS / "jarvis.ico",
        format="ICO",
        sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)],
    )


if __name__ == "__main__":
    main()
