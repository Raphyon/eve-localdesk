"""Render the app icon (packaging/icon.png, icon.ico, icon.icns). Run once; the outputs are committed."""
from pathlib import Path

from PIL import Image, ImageDraw

HERE = Path(__file__).parent


def draw(size):
    s = size * 4  # supersample for smooth edges
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    pad = s * 0.04
    d.ellipse([pad, pad, s - pad, s - pad], fill=(20, 24, 30, 255))                    # dark disc
    ring = s * 0.10
    d.ellipse([ring, ring, s - ring, s - ring], fill=(61, 143, 209, 255))             # blue ring
    inner = s * 0.27
    d.ellipse([inner, inner, s - inner, s - inner], fill=(20, 24, 30, 255))           # dark core
    core = s * 0.40
    d.ellipse([core, core, s - core, s - core], fill=(156, 203, 240, 255))            # bright centre
    # a small "jump" arc across the ring
    d.arc([s * 0.18, s * 0.18, s * 0.82, s * 0.82], start=200, end=320, fill=(255, 255, 255, 230),
          width=int(s * 0.035))
    return img.resize((size, size), Image.LANCZOS)


if __name__ == "__main__":
    big = draw(1024)
    big.resize((256, 256), Image.LANCZOS).save(HERE / "icon.png")
    big.save(HERE / "icon.ico", sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    big.save(HERE / "icon.icns")
    print("icons written")
