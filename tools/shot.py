# -*- coding: utf-8 -*-
"""开发用：离屏把两个覆盖窗画到灰底上导出 PNG，自己检查配色/位置/错开。

用法（Windows Python）：python tools\\shot.py [输出目录]
"""

import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
# offscreen 平台用的是"basic"字体库，要手动指个字体目录，否则全是豆腐块
os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtCore import QPoint                  # noqa: E402
from PySide6.QtGui import QColor, QImage, QPainter  # noqa: E402
from PySide6.QtWidgets import QApplication          # noqa: E402

import config                                      # noqa: E402
from overlay import Overlay                        # noqa: E402

COUNTER = "[1服] 41人  |  [3服] -人  |  [5服] 19人"
TOASTS = ["[3服] 重启", "[1服] 重启"]


def compose(app, widgets, path, bg, size=(620, 140)):
    img = QImage(size[0], size[1], QImage.Format.Format_ARGB32)
    img.fill(QColor(bg))
    p = QPainter(img)
    for w in widgets:
        w.render(p, QPoint(w.x(), w.y()))
    p.end()
    assert img.save(path), path
    print("%s  (%s)" % (path, bg))


def main():
    out_dir = sys.argv[1] if len(sys.argv) > 1 else config.path("build_shots")
    os.makedirs(out_dir, exist_ok=True)
    app = QApplication(sys.argv[:1])
    cfg = config.load()

    def build(counter_text, toast_text):
        c = Overlay(cfg["counter_pos"], cfg["counter_font"], None, "#FFFFFF", False, cfg["alpha"])
        t = Overlay(cfg["restart_pos"], cfg["restart_font"], cfg["restart_seconds"],
                    cfg["restart_color"], True, cfg["alpha"])
        c.set_text(counter_text)
        t.pop(toast_text)
        c.show()
        t.show()
        app.processEvents()
        return c, t

    for idx, bg in enumerate(("#2b2f36", "#9aa3ad", "#1f5d2f")):
        c, t = build(COUNTER, TOASTS[0])
        compose(app, (c, t), os.path.join(out_dir, "shot_%d_counter_toast.png" % idx), bg)
        for w in (c, t):
            w.hide()
            w.deleteLater()

    # 只有人数条（没重启提示）时的样子
    c = Overlay(cfg["counter_pos"], cfg["counter_font"], None, "#FFFFFF", False, cfg["alpha"])
    c.set_text(COUNTER)
    c.show()
    app.processEvents()
    compose(app, (c,), os.path.join(out_dir, "shot_counter_only.png"), "#2b2f36")

    # 几何检查：人数条底边 < 重启提示顶边
    c.set_text(COUNTER)
    c2, t2 = build(COUNTER, TOASTS[1])
    gap = t2.y() - (c2.y() + c2.height())
    print("人数条 %dx%d @%s ；重启提示 @y=%d 高=%d ；间距 %.1f px"
          % (c2.width(), c2.height(), cfg["counter_pos"], t2.y(), t2.height(), gap))
    assert gap > 0, "两个覆盖窗重叠了！"

    # 关于窗口单独出一张，方便肉眼检查排版/配色
    from about import AboutDialog                   # noqa: E402
    from tray import make_icon                      # noqa: E402
    dlg = AboutDialog(make_icon())
    dlg.show()
    app.processEvents()
    img = dlg.grab()
    out = os.path.join(out_dir, "shot_about.png")
    assert img.save(out), out
    print("%s  (%dx%d)" % (out, img.width(), img.height()))
    dlg.hide()

    # 多台服同时重启时的样子：服名并排（只是变宽，高度不变）
    c4 = Overlay(cfg["counter_pos"], cfg["counter_font"], None, "#FFFFFF", False, cfg["alpha"])
    t4 = Overlay(cfg["restart_pos"], cfg["restart_font"], cfg["restart_seconds"],
                 cfg["restart_color"], True, cfg["alpha"])
    c4.set_text(COUNTER)
    t4.pop("[1服] [3服] [5服] 重启")
    c4.show()
    t4.show()
    app.processEvents()
    compose(app, (c4, t4), os.path.join(out_dir, "shot_multi_toast.png"), "#1b2027", size=(620, 140))
    single = Overlay(cfg["restart_pos"], cfg["restart_font"], cfg["restart_seconds"],
                     cfg["restart_color"], True, cfg["alpha"])
    single.pop("[1服] 重启")
    single.show()
    app.processEvents()
    print("重启提示宽度：单服 %d px -> 三服并排 %d px（高度都是 %d px）"
          % (single.width(), t4.width(), t4.height()))

    # README 里用的预览图：深色底 + 刚好装下两个窗的画布（这是示意图，不是真机截图）
    img_dir = config.path("images")
    os.makedirs(img_dir, exist_ok=True)
    c3, t3 = build(COUNTER, TOASTS[0])
    compose(app, (c3, t3), os.path.join(img_dir, "preview.png"), "#1b2027", size=(420, 104))
    return 0


if __name__ == "__main__":
    sys.exit(main())
