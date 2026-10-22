"""Draw the add-on icon (128x128) and logo (250x100) into myha/, and the guest app's
apple-touch-icon (180x180) into myha/web/public/.

Run from the repository root: uv run --with pillow==13.0.0 python scripts/make-icons.py
Deterministic for a given Pillow version: fixed geometry and Pillow's bundled font, no system
fonts. The committed PNGs come from Pillow 13.0.0.
"""

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

GOLD, WHITE, INK, PAPER = "#C8A24A", "#FFFFFF", "#1F1D19", "#F7F5F0"
SS = 4  # ImageDraw does not anti-alias: draw 4x larger, then downsample
OUT = Path(__file__).resolve().parent.parent / "myha"


def draw_mark(draw: ImageDraw.ImageDraw, x: int, y: int, size: int) -> None:
    """Rounded gold square with a white key; the key is laid out on a 32-unit grid."""

    def box(*units: float) -> list[int]:
        return [round(c + u * size / 32) for c, u in zip((x, y, x, y), units, strict=True)]

    draw.rounded_rectangle((x, y, x + size - 1, y + size - 1), radius=size * 22 // 100, fill=GOLD)
    draw.ellipse(box(5, 10, 17, 22), fill=WHITE)  # bow
    draw.ellipse(box(8.5, 13.5, 13.5, 18.5), fill=GOLD)  # hole in the bow
    draw.rectangle(box(16, 14.5, 27, 17.5), fill=WHITE)  # shaft
    draw.rectangle(box(20.5, 17.5, 22.5, 21), fill=WHITE)  # teeth
    draw.rectangle(box(24.5, 17.5, 27, 22.5), fill=WHITE)


icon = Image.new("RGBA", (128 * SS, 128 * SS), (0, 0, 0, 0))
draw_mark(ImageDraw.Draw(icon), 0, 0, 128 * SS)
icon = icon.resize((128, 128), Image.Resampling.LANCZOS)

logo = Image.new("RGB", (250 * SS, 100 * SS), PAPER)
draw = ImageDraw.Draw(logo)
draw_mark(draw, 12 * SS, 12 * SS, 76 * SS)
font = ImageFont.load_default(size=46 * SS)  # Pillow's bundled Aileron, identical everywhere
draw.text(
    (104 * SS, 50 * SS), "myHA", fill=INK, font=font, anchor="lm", stroke_width=SS, stroke_fill=INK
)
logo = logo.resize((250, 100), Image.Resampling.LANCZOS)

# iOS rounds the corners itself and paints transparency black: a full gold square.
touch = Image.new("RGB", (180 * SS, 180 * SS), GOLD)
draw_mark(ImageDraw.Draw(touch), 0, 0, 180 * SS)
touch = touch.resize((180, 180), Image.Resampling.LANCZOS)

assert icon.size == (128, 128) and logo.size == (250, 100) and touch.size == (180, 180)
icon.save(OUT / "icon.png", optimize=True)
logo.save(OUT / "logo.png", optimize=True)
touch.save(OUT / "web" / "public" / "apple-touch-icon.png", optimize=True)
