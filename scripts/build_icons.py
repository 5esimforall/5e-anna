#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = ["Pillow==12.0.0"]
# ///
"""Export Anna's approved portrait: uv run scripts/build_icons.py."""
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
SIZES = (16, 32, 48, 64, 128, 256)


def main():
    source = Image.open(ROOT / 'assets/branding/anna.png').convert('RGBA')
    if source.width != source.height:
        raise ValueError('The approved portrait must be square')
    icons = {size: source.resize((size, size), Image.Resampling.LANCZOS) for size in SIZES}
    for size in (16, 32, 48):
        icons[size].save(ROOT / f'web/favicon-{size}.png', optimize=True)
    icons[128].save(ROOT / 'web/anna-logo.png', optimize=True)
    icons[256].save(ROOT / 'web/favicon.ico', sizes=[(size, size) for size in SIZES],
                    append_images=[icons[size] for size in SIZES if size != 256])

    # Native-size samples plus nearest-neighbor magnification: no hidden upscale smoothing.
    preview = Image.new('RGB', (760, 400), '#f4f3ef')
    draw = ImageDraw.Draw(preview)
    for row, (background, foreground) in enumerate((('#f4f3ef', '#302e2b'), ('#202124', '#e8eaed'))):
        top = row * 200
        draw.rectangle((0, top, 759, top + 199), fill=background)
        draw.text((20, top + 14), 'LIGHT' if row == 0 else 'DARK', fill=foreground)
        for column, size in enumerate((16, 32, 48)):
            x = 20 + column * 245
            draw.text((x, top + 40), f'{size}px native', fill=foreground)
            preview.paste(icons[size], (x, top + 74), icons[size])
            enlarged = icons[size].resize((96, 96), Image.Resampling.NEAREST)
            preview.paste(enlarged, (x + 120, top + 66), enlarged)
            draw.text((x + 120, top + 172), f'{96 // size}x pixels', fill=foreground)
    output = ROOT / 'output/imagegen'
    output.mkdir(parents=True, exist_ok=True)
    preview.save(output / 'anna-favicon-preview.png')


if __name__ == '__main__':
    main()
