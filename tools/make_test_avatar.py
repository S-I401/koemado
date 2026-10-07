r"""テスト用の素材を作る（図形だけの顔。リポジトリのサンプルではない）。

    app\.venv\Scripts\python.exe tools\make_test_avatar.py OUT_DIR [--size 512]

OUT_DIR に、顔画像（image.png と neutral / joy / angry の表情、eyes_closed）と、
口の差分（mouth/closed|half|open|e|u.png。顔画像と同じキャンバスサイズの透過 PNG）を書き出す。
キャラクターのフォルダ（characters/<id>/）に置くときは、image_joy.png のように image_ を付けた名前にする（--character）。
"""
import argparse
from pathlib import Path

from PIL import Image, ImageDraw

SKIN = (255, 224, 196, 255)
EDGE = (120, 80, 60, 255)
DARK = (60, 20, 30, 255)


def face(size: int, expr: str) -> Image.Image:
    im = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    m = size * 0.1
    d.ellipse([m, m, size - m, size - m], fill=SKIN, outline=EDGE, width=max(2, size // 100))
    ex, ey, r = size * 0.28, size * 0.40, size * 0.055
    for cx in (size / 2 - ex / 1.6 * 1.0, size / 2 + ex / 1.6 * 1.0):
        if expr == "eyes_closed":
            d.line([cx - r * 1.4, ey, cx + r * 1.4, ey], fill=EDGE, width=max(3, size // 80))
        elif expr == "joy":
            d.arc([cx - r * 1.5, ey - r * 1.2, cx + r * 1.5, ey + r * 1.6], 200, 340, fill=EDGE, width=max(3, size // 80))
        elif expr == "angry":
            sgn = -1 if cx < size / 2 else 1
            d.line([cx - r * 1.5, ey - sgn * r * 1.4, cx + r * 1.5, ey + sgn * r * 1.4], fill=EDGE, width=max(3, size // 80))
            d.ellipse([cx - r, ey - r * 0.5, cx + r, ey + r * 1.5], fill=DARK)
        else:
            d.ellipse([cx - r, ey - r * 1.3, cx + r, ey + r * 1.3], fill=DARK)
    if expr == "angry":
        d.ellipse([size * 0.2, size * 0.55, size * 0.3, size * 0.62], fill=(255, 140, 140, 255))
        d.ellipse([size * 0.7, size * 0.55, size * 0.8, size * 0.62], fill=(255, 140, 140, 255))
    return im


def mouth(size: int, shape: str) -> Image.Image:
    im = Image.new("RGBA", (size, size), (0, 0, 0, 0))     # 透過。口の部分だけを描く
    d = ImageDraw.Draw(im)
    cx, cy = size / 2, size * 0.66
    if shape == "closed":
        d.line([cx - size * 0.09, cy, cx + size * 0.09, cy], fill=DARK, width=max(4, size // 70))
    elif shape == "half":
        d.ellipse([cx - size * 0.07, cy - size * 0.025, cx + size * 0.07, cy + size * 0.045], fill=DARK)
    elif shape == "open":
        d.ellipse([cx - size * 0.09, cy - size * 0.05, cx + size * 0.09, cy + size * 0.10], fill=DARK)
        d.ellipse([cx - size * 0.05, cy + size * 0.045, cx + size * 0.05, cy + size * 0.095], fill=(230, 100, 110, 255))
    elif shape == "e":
        d.rounded_rectangle([cx - size * 0.13, cy - size * 0.02, cx + size * 0.13, cy + size * 0.04], radius=size * 0.02, fill=DARK)
    elif shape == "u":
        d.ellipse([cx - size * 0.035, cy - size * 0.035, cx + size * 0.035, cy + size * 0.035], fill=DARK)
    return im


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("out")
    ap.add_argument("--size", type=int, default=512)
    ap.add_argument("--character", action="store_true", help="characters/<id>/ に置く名前（image_joy.png）で書き出す")
    ap.add_argument("--mouth-size", type=int, default=0, help="口の差分だけ別のサイズにする（サイズ違いの警告のテスト用）")
    a = ap.parse_args()
    out = Path(a.out)
    (out / "mouth").mkdir(parents=True, exist_ok=True)
    prefix = "image_" if a.character else ""
    face(a.size, "neutral").save(out / "image.png")
    for e in ("neutral", "joy", "angry", "eyes_closed"):
        face(a.size, e).save(out / f"{prefix}{e}.png")
    for s in ("closed", "half", "open", "e", "u"):
        mouth(a.mouth_size or a.size, s).save(out / "mouth" / f"{s}.png")
    print("wrote", out)


if __name__ == "__main__":
    main()
