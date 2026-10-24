# -*- coding: utf-8 -*-
"""左上角覆盖窗：人数条（常驻）+ 重启提示（N 秒后消失）。

两种用途共用一个类，靠参数区分：
* 人数条：duration=None + 白色 16px（回合时长那一段可以单独给颜色）——鼠标穿透；
  托盘勾「移动人数条」后临时可拖动（蓝框提示），拖完把位置写回 settings.json
* 重启提示：duration=10 + 红色 28px 粗体 + clickable=True——**可点击**，
  点一下回调 on_click（一键进服用）；不穿透但也不抢焦点（WS_EX_NOACTIVATE），
  游戏照样保持前台。移动人数条期间拖动优先，on_click 不会被触发。

内容按"行 -> 段"存：每段是 (文本, 颜色 or None)，None 表示用默认色。
"""
from PySide6.QtCore import QEasingCurve, QPropertyAnimation, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QPainter
from PySide6.QtWidgets import QWidget

FONT_FAMILIES = ["Microsoft YaHei UI", "Microsoft YaHei", "SimHei", "sans-serif"]


class Overlay(QWidget):
    position_changed = Signal(int, int)
    position_moved = Signal(int, int)

    def __init__(self, pos, font_px, duration=None, color="#FFFFFF",
                 bold=False, alpha=150, clickable=False):
        super().__init__()
        self.duration = duration
        self.color = QColor(color)
        self.alpha = int(alpha)
        self.on_click = None                 # clickable=True 时，整块被点一下就回调
        self._pos = (int(pos[0]), int(pos[1]))
        self.pad_x = max(4, font_px // 2)
        self.pad_y = max(2, font_px // 4)
        self.lines = [[("", None)]]          # [[(文本, 颜色 or None)]]
        self._sig = []                       # 内容指纹，用来判断要不要重绘
        self._movable = False
        self._drag_offset = None

        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        flags = (Qt.WindowType.FramelessWindowHint
                 | Qt.WindowType.WindowStaysOnTopHint
                 | Qt.WindowType.Tool                     # 不占任务栏
                 | Qt.WindowType.WindowDoesNotAcceptFocus)  # 点击不抢游戏焦点
        if clickable:
            self.setCursor(Qt.CursorShape.PointingHandCursor)
        else:
            flags |= Qt.WindowType.WindowTransparentForInput   # 鼠标穿透
        self.setWindowFlags(flags)

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

    def set_position(self, pos):
        """同步当前位置与下次显示的位置，隐藏的提示也随人数条移动。"""
        self._pos = (int(pos[0]), int(pos[1]))
        self.move(*self._pos)

    def set_movable(self, flag):
        """仅在用户解锁时接收鼠标；切换窗口标志后恢复原来的显隐状态。"""
        self._movable = bool(flag)
        self._drag_offset = None
        visible = self.isVisible()
        self.setWindowFlag(Qt.WindowType.WindowTransparentForInput, not self._movable)
        self.setCursor(Qt.CursorShape.OpenHandCursor if self._movable
                       else (Qt.CursorShape.PointingHandCursor if self.on_click is not None
                             else Qt.CursorShape.ArrowCursor))
        self.setToolTip("按住左键拖动；在托盘菜单取消‘移动人数条’后恢复鼠标穿透。"
                        if self._movable else "")
        self.set_visible(visible)
        self.update()

    def set_text(self, text, ensure_shown=True):
        """更新内容并按需重排尺寸；支持 \\n 多行（整行一个颜色）。"""
        self._set([[(part, None)] for part in str(text).split("\n")], ensure_shown)

    def set_segments(self, segments, ensure_shown=True):
        """单行多色：[(文本, 颜色名/颜色 or None)]；None = 默认色。"""
        self._set([list(segments)], ensure_shown)

    def set_visible(self, flag):
        if flag:
            self.move(*self._pos)
            self.show()
        else:
            self.hide()

    def pop(self, text, seconds=None):
        """重新出现并重新计时（重启提示用）。"""
        self._sig = []                      # 强制重排，内容相同也要刷新
        self.set_text(text)
        self.move(*self._pos)
        self.setWindowOpacity(1.0)
        self.show()
        secs = self.duration if seconds is None else seconds
        if secs:
            self._timer.start(int(float(secs) * 1000))

    @property
    def text(self):
        return "\n".join("".join(t for t, _ in line) for line in self.lines)

    # ------------------------------ 内部 ------------------------------

    def _set(self, lines, ensure_shown):
        lines = [[(str(t), c if isinstance(c, QColor) else (QColor(c) if c else None))
                  for t, c in line] for line in lines]
        sig = [[(t, c.name() if c else "") for t, c in line] for line in lines]
        if sig == self._sig:
            return
        self._sig = sig
        self.lines = lines
        self._fit()
        self.update()          # 必须自己叫重绘！自定义 paintEvent 的控件靠这个刷新画面，
        #                        只改内容又不动尺寸的话，画面会一直停在旧数字上。
        if ensure_shown and not self.isVisible():
            self.show()

    def _fit(self):
        fm = self.fontMetrics()
        widths = [sum(fm.horizontalAdvance(t) for t, _ in line) for line in self.lines] or [0]
        h = fm.lineSpacing() * max(1, len(self.lines))
        self.setFixedSize(max(1, max(widths) + self.pad_x * 2), max(1, h + self.pad_y * 2))

    def mousePressEvent(self, event):
        if self._movable and event.button() == Qt.MouseButton.LeftButton:
            self._drag_offset = event.globalPosition().toPoint() - self.pos()
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            event.accept()                   # 移动模式：只拖不点，绝不触发 on_click
            return
        if self.on_click and event.button() == Qt.MouseButton.LeftButton:
            event.accept()
            self.on_click()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._drag_offset is not None and event.buttons() & Qt.MouseButton.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_offset)
            self._pos = (self.x(), self.y())
            self.position_moved.emit(*self._pos)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if self._drag_offset is not None and event.button() == Qt.MouseButton.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_offset)
            self._pos = (self.x(), self.y())
            self._drag_offset = None
            self.setCursor(Qt.CursorShape.OpenHandCursor)
            self.position_changed.emit(*self._pos)
            event.accept()
            return
        super().mouseReleaseEvent(event)

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
        if self._movable:                    # 解锁拖动时画一圈蓝框提示
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.setPen(QColor("#5CB8FF"))
            p.drawRoundedRect(r, 4, 4)
            p.setPen(Qt.PenStyle.NoPen)
        p.setFont(self.font())
        fm = self.fontMetrics()
        for i, line in enumerate(self.lines):
            x = self.pad_x
            y = self.pad_y + i * fm.lineSpacing() + fm.ascent()
            for txt, col in line:
                if txt:
                    p.setPen(col or self.color)
                    p.drawText(int(x), int(y), txt)
                x += fm.horizontalAdvance(txt)
