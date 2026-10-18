# -*- coding: utf-8 -*-
"""生成打包用的图标 `assets/app.ico`（构建时由 build.bat 调用，需要 Pillow）。

配色与 tray.py 里 QPainter 现画的托盘图标一致：暗红底 + 白色 SL。
"""

import os


def build_images():
    from PIL import Image, ImageDraw, ImageFont

    bg = "#8e1b1b"
    fg = "#ffffff"
    sizes = (16, 24, 32, 48, 64, 128, 256)
    imgs = []
    for s in sizes:
        img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        m = max(1, s // 16)
        d.rounded_rectangle((m // 2, m // 2, s - 1 - m // 2, s - 1 - m // 2),
                            radius=max(2, s // 16), fill=bg)
        text = "SL"
        size = max(6, int(s * 0.5))
        font = None
        for name in ("msyhbd.ttc", "msyh.ttc", "simhei.ttf", "arialbd.ttf", "DejaVuSans-Bold.ttf"):
            try:
                font = ImageFont.truetype(name, size)
                break
            except OSError:
                continue
        if font is None:
            font = ImageFont.load_default()
        box = d.textbbox((0, 0), text, font=font)
        d.text(((s - box[2] + box[0]) / 2, (s - box[3] + box[1]) / 2),
               text, fill=fg, font=font)
        imgs.append(img)
    return imgs


def main():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    out_dir = os.path.join(root, "assets")
    os.makedirs(out_dir, exist_ok=True)
    out = os.path.join(out_dir, "app.ico")
    try:
        imgs = build_images()
    except ImportError:
        print("需要 Pillow 才能生成图标：python -m pip install pillow")
        raise SystemExit(1)
    imgs[-1].save(out, format="ICO", sizes=[(i.width, i.height) for i in imgs])
    print("icon -> %s" % out)


if __name__ == "__main__":
    main()
