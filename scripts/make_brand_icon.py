"""Generate a brand icon for home-assistant/brands custom_integrations.

Renders a simple climate-themed icon: rounded blue square with a
stylised AC unit (louvre lines) and a snowflake. No third-party
trademark elements. Produces icon.png (256x256) and icon@2x.png
(512x512) in branding/.

Run:
    python scripts/make_brand_icon.py
"""

from __future__ import annotations

import math
from pathlib import Path

from PIL import Image, ImageDraw

BG = (3, 169, 244, 255)         # HA brand light blue
FG = (255, 255, 255, 255)       # white
SHADE = (255, 255, 255, 60)     # soft accent

OUT_DIR = Path(__file__).resolve().parent.parent / "branding"


def _rounded_square(size: int, radius_frac: float = 0.22) -> Image.Image:
    """Solid colour square with rounded corners on transparent canvas."""
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    r = int(size * radius_frac)
    draw.rounded_rectangle((0, 0, size - 1, size - 1), radius=r, fill=BG)
    return img


def _ac_unit(draw: ImageDraw.ImageDraw, size: int) -> None:
    """Draw a stylised AC indoor unit (rounded rectangle with louvre slits)."""
    # Outer body
    pad_x = int(size * 0.18)
    top_y = int(size * 0.30)
    bot_y = int(size * 0.62)
    r = int(size * 0.06)
    draw.rounded_rectangle(
        (pad_x, top_y, size - pad_x, bot_y),
        radius=r,
        fill=FG,
    )
    # Louvre lines (horizontal)
    line_y_start = top_y + int(size * 0.10)
    line_spacing = int(size * 0.05)
    for i in range(3):
        y = line_y_start + i * line_spacing
        draw.line(
            (pad_x + int(size * 0.06), y, size - pad_x - int(size * 0.06), y),
            fill=BG,
            width=max(2, int(size * 0.012)),
        )


def _snowflake(draw: ImageDraw.ImageDraw, size: int) -> None:
    """Draw a 6-arm snowflake below the AC unit."""
    cx = size // 2
    cy = int(size * 0.78)
    radius = int(size * 0.10)
    width = max(2, int(size * 0.014))
    for arm in range(6):
        angle = math.radians(arm * 60)
        x = cx + int(math.cos(angle) * radius)
        y = cy + int(math.sin(angle) * radius)
        draw.line((cx, cy, x, y), fill=FG, width=width)
        # Small terminators on each arm
        for branch in (-30, 30):
            bx = cx + int(math.cos(angle) * radius * 0.70)
            by = cy + int(math.sin(angle) * radius * 0.70)
            ang_branch = angle + math.radians(branch)
            ex = bx + int(math.cos(ang_branch) * radius * 0.35)
            ey = by + int(math.sin(ang_branch) * radius * 0.35)
            draw.line((bx, by, ex, ey), fill=FG, width=max(1, width - 1))


def render(size: int) -> Image.Image:
    img = _rounded_square(size)
    draw = ImageDraw.Draw(img)
    _ac_unit(draw, size)
    _snowflake(draw, size)
    return img


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    icon = render(256)
    icon.save(OUT_DIR / "icon.png", "PNG")
    icon2x = render(512)
    icon2x.save(OUT_DIR / "icon@2x.png", "PNG")
    print(f"wrote {OUT_DIR/'icon.png'}  (256x256)")
    print(f"wrote {OUT_DIR/'icon@2x.png'}  (512x512)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
