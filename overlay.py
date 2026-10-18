# -*- coding: utf-8 -*-
"""左上角覆盖窗：人数条（常驻）+ 重启提示（N 秒后消失）。

两种用途共用一个类，靠参数区分：
* 人数条：duration=None + 白色 16px
* 重启提示：duration=10 + 红色 28px 粗体
窗口全程鼠标穿透，不会吃掉游戏里的点击。
"""

from PySide6.QtCore import QEasingCurve, QPropertyAnimation, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QPainter
from PySide6.QtWidgets import QWidget

FONT_FAMILIES = ["Microsoft YaHei UI", "Microsoft YaHei", "SimHei", "sans-serif"]


class Overlay(QWidget):
    def __init__(self, pos, font_px, duration=None, color="#FFFFFF",
                 bold=False, alpha=150):
        super().__init__()
        self.duration = duration
        self.color = QColor(color)
        self.alpha = int(alpha)
        self._pos = (int(pos[0]), int(pos[1]))
        self.pad_x = max(4, font_px // 2)
        self.pad_y = max(2, font_px // 4)
        self.lines = [""]

        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool                     # 不占任务栏
            | Qt.WindowType.WindowDoesNotAcceptFocus
            | Qt.WindowType.WindowTransparentForInput)   # 鼠标穿透

        f = QFont()
        f.setFamilies(FONT_FAMILIES)
        f.setPixelSize(int(font_px))
        f.setBold(bool(bold))
        self.setFont(f)

        self._fade = None
        self._timer = QTimer(self)          # 单次计时，重复 start() 会重置
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._fade_out)

    # ------------------------------ 对外 ------------------------------

    def set_text(self, text, ensure_shown=True):
        """更新内容并按需重排尺寸；人数条每秒走这里。"""
        lines = str(text).split("\n")
        if lines == self.lines:
            return
        self.lines = lines
        self._fit()
        self.update()          # 必须自己叫重绘！自定义 paintEvent 的控件靠这个刷新画面，
        #                        只改 self.lines 又不动尺寸的话，画面会一直停在旧数字上。
        if ensure_shown and not self.isVisible():
            self.show()

    def set_visible(self, flag):
        if flag:
            self.move(*self._pos)
            self.show()
        else:
            self.hide()

    def pop(self, text, seconds=None):
        """重新出现并重新计时（重启提示用）。"""
        self.lines = []                     # 强制重排，内容相同也要刷新
        self.set_text(text)
        self.move(*self._pos)
        self.setWindowOpacity(1.0)
        self.show()
        secs = self.duration if seconds is None else seconds
        if secs:
            self._timer.start(int(float(secs) * 1000))

    @property
    def text(self):
        return "\n".join(self.lines)

    # ------------------------------ 内部 ------------------------------

    def _fit(self):
        fm = self.fontMetrics()
        w = max(fm.horizontalAdvance(line) for line in self.lines) if self.lines else 0
        h = fm.lineSpacing() * max(1, len(self.lines))
        self.setFixedSize(max(1, w + self.pad_x * 2), max(1, h + self.pad_y * 2))

    def _fade_out(self):
        self._fade = QPropertyAnimation(self, b"windowOpacity", self)
        self._fade.setDuration(300)
        self._fade.setStartValue(1.0)
        self._fade.setEndValue(0.0)
        self._fade.setEasingCurve(QEasingCurve.Type.InCubic)
        self._fade.finished.connect(self.hide)
        self._fade.start()

    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(0, 0, 0, self.alpha))
        r = self.rect().adjusted(0, 0, -1, -1)
        p.drawRoundedRect(r, 4, 4)
        p.setPen(self.color)
        p.setFont(self.font())
        p.drawText(self.rect().adjusted(self.pad_x, self.pad_y, -self.pad_x, -self.pad_y),
                   int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
                   "\n".join(self.lines))
