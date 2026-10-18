# -*- coding: utf-8 -*-
"""开发用：对比「修复前 / 修复后」的人数条重绘行为。

不联网：直接给覆盖窗喂一串"数字变了但像素宽度没变"的文本，
最后一步用明显变窄的文本作对照组。
脚本自己模拟"修复前"的 set_text（改内容不叫重绘），再跑一遍当前代码，一次给出对比。

用法（Windows Python，会在桌面上显示约 12 秒）：python tools\\repro_repaint.py
"""

import os
import sys
import time

sys.stdout.reconfigure(encoding="utf-8", errors="replace")   # 控制台是 GBK，先自保
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtCore import QTimer                          # noqa: E402
from PySide6.QtWidgets import QApplication                 # noqa: E402

import config                                              # noqa: E402
from overlay import Overlay                                # noqa: E402

# 前 5 条宽度完全一样（都是 2 位数），第 6 条明显变窄（1 位数）作对照
TEXTS = ["[1服] 41人  |  [3服] 25人  |  [5服] 36人",
         "[1服] 42人  |  [3服] 25人  |  [5服] 36人",
         "[1服] 43人  |  [3服] 25人  |  [5服] 36人",
         "[1服] 44人  |  [3服] 25人  |  [5服] 36人",
         "[1服] 45人  |  [3服] 25人  |  [5服] 36人",
         "[1服] 9人   |  [3服] 25人  |  [5服] 36人"]

PAINTS = {"n": 0}
REAL_SET_TEXT = Overlay.set_text
REAL_PAINT = Overlay.paintEvent


def counted_paint(self, event):
    """数一数 paintEvent 真的被调了几次（= 画面真的刷新了几次）。"""
    PAINTS["n"] += 1
    return REAL_PAINT(self, event)


Overlay.paintEvent = counted_paint


def old_set_text(self, text, ensure_shown=True):
    """修复前的行为：改内容、改尺寸，但不叫重绘（问题是它不叫重绘）。"""
    lines = str(text).split("\n")
    if lines == self.lines:
        return
    self.lines = lines
    self._fit()
    if ensure_shown and not self.isVisible():
        self.show()


def flush(app, seconds=0.35):
    end = time.time() + seconds
    while time.time() < end:
        app.processEvents()
        time.sleep(0.01)


def wait_paint(app, before, timeout=1.2):
    """等到真的重绘一次（或超时）。这样即使机器忙也不会把"有重绘"误判成 0。"""
    end = time.time() + timeout
    while time.time() < end and PAINTS["n"] == before:
        app.processEvents()
        time.sleep(0.01)


def run(app, cfg, label, use_old):
    print("\n=== %s ===" % label, flush=True)
    PAINTS["n"] = 0
    Overlay.set_text = old_set_text if use_old else REAL_SET_TEXT
    win = Overlay(cfg["counter_pos"], cfg["counter_font"], None, "#FFFFFF", False, cfg["alpha"])
    win.show()
    flush(app, 0.4)
    rows = []
    try:
        for i, text in enumerate(TEXTS):
            before = PAINTS["n"]
            win.set_text(text)
            wait_paint(app, before)          # 等这次刷新真的落下来（最多 1.2 秒）
            flush(app, 0.2)
            now = PAINTS["n"]
            rows.append((i, win.width(), now - before, text))
            print("  第 %d 步  宽 %3d  本步新增重绘 %d 次   %s" % (i, win.width(), rows[-1][2], text),
                  flush=True)
    finally:
        Overlay.set_text = REAL_SET_TEXT
        win.hide()
        win.deleteLater()
    return rows


def verdict(label, rows):
    widths = [r[1] for r in rows]
    same_width = len(set(widths[:5])) == 1
    same_steps = [r[2] for r in rows[1:5]]        # 第 2~5 步：宽度没变、只换数字
    control = rows[5][2]                          # 第 6 步：宽度变窄
    print("\n【%s】前 5 步宽度是否相同：%s%s" % (label, same_width, "  " + str(widths[:5]) if not same_width else ""))
    print("  同宽 4 步的重绘次数：%s（合计 %d）" % (same_steps, sum(same_steps)))
    print("  变窄对照步的重绘次数：%d" % control)
    if same_width and sum(same_steps) == 0 and control > 0:
        print("  判定：BUG 复现 -> 只有像素宽度变化时才重绘，数字变了也不刷新画面")
    elif same_width and all(s > 0 for s in same_steps):
        print("  判定：OK -> 每次数字变化都重绘，条子会正常刷新")
    else:
        print("  判定：? -> 再人工看看")
    return same_width, sum(same_steps), control


def main():
    app = QApplication(sys.argv[:1])
    cfg = config.load()
    QTimer.singleShot(30000, app.quit)            # 兜底：绝不挂死
    before = verdict("修复前", run(app, cfg, "修复前（旧代码：改内容不叫重绘）", use_old=True))
    after = verdict("修复后", run(app, cfg, "修复后（当前代码：内容变了就叫重绘）", use_old=False))
    print("\n总结：修复前 同宽4步重绘 %d 次 / 对照步 %d 次 ；修复后 同宽4步重绘 %d 次 / 对照步 %d 次"
          % (before[1], before[2], after[1], after[2]), flush=True)
    print("结论：%s" % ("旧代码确实会卡住画面，新代码修好了" if before[1] == 0 and after[1] > 0
                        else "与预期不符，需要人工复核"), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
