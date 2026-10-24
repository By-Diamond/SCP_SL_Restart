# -*- coding: utf-8 -*-
"""开发用：离屏把界面逻辑跑一遍（托盘菜单回调 / 人数条显隐 / 编辑对话框 / 关于窗口 / 重启弹窗）。

不联网也能跑；不会写注册表（只读查询开机自启状态）。
用法（Windows Python）：python tools\\gui_check.py
"""

import os
import sys
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtWidgets import QApplication, QMessageBox     # noqa: E402

import autostart                                          # noqa: E402
import config                                             # noqa: E402
import overlay as overlay_mod                             # noqa: E402
import sl_restart                                         # noqa: E402
from about import LINKS, AboutDialog                      # noqa: E402
from server_dialog import ServersDialog                   # noqa: E402

FAILS = []


def check(name, cond, extra=""):
    print(("PASS  " if cond else "FAIL  ") + name + ("" if cond else "   -> %s" % (extra,)))
    if not cond:
        FAILS.append(name)


def pump(app, seconds=0.0):
    end = time.time() + seconds
    while True:
        app.processEvents()
        if time.time() >= end:
            return
        time.sleep(0.02)


def main():
    app = QApplication(sys.argv[:1])
    app.setQuitOnLastWindowClosed(False)
    tmp = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       "build_shots", "gui_check_data")     # 别把测试数据写进项目根目录
    os.makedirs(tmp, exist_ok=True)
    config.set_base_dir(tmp)
    cfg = config.load()
    ui = sl_restart.App(cfg, app)
    pump(app, 0.5)
    t = ui.tray

    check("托盘菜单九项齐全",
          set(t.acts) == {"counter", "move_counter", "pause", "refresh", "servers", "alerts", "autostart", "about", "quit"},
          sorted(t.acts))
    check("菜单文案：显示人数条 / 暂停监控 / 立即刷新 / 编辑服务器清单… / 开机自启 / 关于 / 退出程序",
          [t.acts[k].text() for k in ("counter", "pause", "refresh", "servers", "autostart", "about", "quit")]
          == ["显示人数条", "暂停监控", "立即刷新", "编辑服务器清单…", "开机自启", "关于", "退出程序"],
          [t.acts[k].text() for k in ("counter", "pause", "refresh", "servers", "autostart", "about", "quit")])
    check("人数条默认显示、重启提示默认不显示",
          ui.counter.isVisible() and not ui.toast.isVisible())

    ui.toast.pop("[3服] 重启")
    pump(app, 0.2)
    check("人数条在 (10,10)、重启提示在 (10,48)",
          (ui.counter.x(), ui.counter.y()) == (10, 10) and (ui.toast.x(), ui.toast.y()) == (10, 48),
          "人数条 %s 提示 %s" % ((ui.counter.x(), ui.counter.y()), (ui.toast.x(), ui.toast.y())))
    gap = ui.toast.y() - (ui.counter.y() + ui.counter.height())
    check("重启提示不会盖住人数条（间距 > 0）", gap > 0, "间距 %d px" % gap)
    check("重启提示是红色粗体",
          ui.toast.font().bold() and ui.toast.color.name().lower() == "#ff3b3b",
          "%s / bold=%s" % (ui.toast.color.name(), ui.toast.font().bold()))
    ui.toast.pop("[3服] 重启", seconds=0.4)
    pump(app, 1.3)
    check("提示到点自动消失（这里用 0.4 秒加速验证淡出+隐藏）", not ui.toast.isVisible())

    enabled = autostart.is_enabled()
    print("   （HKCU 开机自启项当前：%s；若勾选会写：%s）"
          % ("存在" if enabled else "不存在", autostart._target()))
    check("开机自启勾选状态 = 注册表真实状态（自检没有写过注册表）",
          t.acts["autostart"].isChecked() == enabled)

    t.acts["refresh"].trigger()
    pump(app, 0.4)
    check("‘立即刷新’能触发一轮采集（快照有更新时间）",
          bool(ui.monitor.snapshot["stamp"]), ui.monitor.snapshot["stamp"])

    t.acts["pause"].trigger()
    pump(app, 0.2)
    check("暂停：菜单变‘继续监控’且监控线程挂起",
          ui.monitor.paused and t.acts["pause"].text() == "继续监控", t.acts["pause"].text())
    t.acts["pause"].trigger()
    pump(app, 0.2)
    check("继续：菜单变回‘暂停监控’且恢复采集",
          (not ui.monitor.paused) and t.acts["pause"].text() == "暂停监控")

    t.acts["counter"].trigger()               # trigger() 自己会翻转勾选
    pump(app, 0.2)
    check("取消勾选 -> 人数条隐藏", not ui.counter.isVisible())
    for _ in range(5):
        ui._sync()
        pump(app, 0.05)
    check("连跑 5 次心跳也不会把隐藏的人数条弹回来", not ui.counter.isVisible())
    t.acts["counter"].trigger()
    pump(app, 0.2)
    check("重新勾选 -> 人数条回来", ui.counter.isVisible())

    # 回归：数字变了、但像素宽度没变时，也必须请求重绘
    #（否则画面会一直停在旧数字上——就是"提示完重启后人数条不再刷新"那个 bug）
    ui.timer.stop()                                   # 停掉 200ms 心跳，避免干扰这段判定
    real_update = overlay_mod.Overlay.update
    calls = {"n": 0}

    def spy(self, *a, **k):
        calls["n"] += 1
        return real_update(self, *a, **k)

    overlay_mod.Overlay.update = spy
    try:
        ui.counter.set_text("[1服] 41人  |  [3服] 25人  |  [5服] 36人")
        w = ui.counter.width()
        n0 = calls["n"]
        ui.counter.set_text("[1服] 42人  |  [3服] 25人  |  [5服] 36人")   # 只换数字，宽度不变
        w2 = ui.counter.width()
        n1 = calls["n"]
    finally:
        overlay_mod.Overlay.update = real_update
        ui.timer.start(sl_restart.SYNC_MS)
    check("宽度不变、只换数字时也会重绘（旧版这里 0 次重绘）",
          w == w2 and n1 > n0, "宽度 %s->%s，update 次数 %s->%s" % (w, w2, n0, n1))

    with ui.monitor._lock:
        ui.monitor._pending.append("3服")
    ui._sync()
    pump(app, 0.2)
    check("重启事件 -> 弹窗显示「[3服] 重启」",
          ui.toast.isVisible() and ui.toast.text == "[3服] 重启",
          "%s / visible=%s" % (ui.toast.text, ui.toast.isVisible()))

    # ---- 多台服同时重启：服名并排、不丢信息 ----
    # 旧逻辑（一台一条 pop）会被后一条盖掉，这里顺手复现一下
    ui.toast.set_visible(False)
    ui.toast.pop("[1服] 重启")
    ui.toast.pop("[3服] 重启")
    check("（旧逻辑对照）俩服同时重启只剩后一个：[3服] 重启",
          ui.toast.text == "[3服] 重启", ui.toast.text)

    ui.toast.set_visible(False)
    ui.toast._timer.stop()
    with ui.monitor._lock:
        ui.monitor._pending.extend(["1服", "3服"])
    ui._sync()
    pump(app, 0.2)
    check("同一轮两台服重启 -> 并排显示「[1服] [3服] 重启」",
          ui.toast.isVisible() and ui.toast.text == "[1服] [3服] 重启", ui.toast.text)

    pump(app, 1.5)                                    # 让倒计时先走掉一部分
    left_before = ui.toast._timer.remainingTime()
    with ui.monitor._lock:
        ui.monitor._pending.append("5服")
    ui._sync()
    pump(app, 0.2)
    left_after = ui.toast._timer.remainingTime()
    check("提示期间又来一台 -> 三个服名并排",
          ui.toast.text == "[1服] [3服] [5服] 重启", ui.toast.text)
    check("新重启到达时倒计时重置为 10 秒",
          left_after > left_before + 500,
          "重置前剩 %dms -> 重置后剩 %dms" % (left_before, left_after))

    # 提示放完之后，旧服名不许残留
    ui.toast.duration = 0.4
    with ui.monitor._lock:
        ui.monitor._pending.append("3服")
    ui._sync()
    pump(app, 1.4)
    check("提示自动消失后旧服名被清掉", not ui.toast.isVisible())
    with ui.monitor._lock:
        ui.monitor._pending.append("1服")
    ui._sync()
    pump(app, 0.2)
    check("下一次重启只显示本次的服名「[1服] 重启」",
          ui.toast.text == "[1服] 重启", ui.toast.text)
    ui.toast.duration = cfg["restart_seconds"]
    ui.toast.set_visible(False)
    ui.toast._timer.stop()

    ui.monitor.set_servers([{"addr": "127.0.0.1:1", "name": "测试服"}])
    pump(app, 0.5)
    check("换清单后人数条立刻换成新清单（打不通显示 -人）",
          ui.counter.text == "[测试服] -人", ui.counter.text)

    # ---- 关于窗口 ----
    real_exec = AboutDialog.exec
    AboutDialog.exec = lambda self: 0                  # 别真弹模态框把自检卡住
    try:
        t.acts["about"].trigger()
        pump(app, 0.2)
        opened = True
    except Exception as e:                             # noqa: BLE001
        opened = False
        print("   打开关于窗口时异常：%r" % e)
    finally:
        AboutDialog.exec = real_exec
    check("点‘关于’能打开窗口（自检里换成不阻塞的 exec）", opened)

    dlg = AboutDialog(ui.tray.icon.icon())
    check("关于窗口：标题 / 名称 / 版本",
          dlg.windowTitle() == "关于 SL Restart"
          and dlg.name_label.text() == "SL Restart"
          and dlg.version_label.text() == "版本 %s" % config.VERSION,
          "%s | %s | %s" % (dlg.windowTitle(), dlg.name_label.text(), dlg.version_label.text()))
    urls = [l.toolTip() for l in dlg.link_labels]
    check("关于窗口：三行链接、URL 正确、点击走系统浏览器",
          urls == [u for _, u in LINKS]
          and all(l.openExternalLinks() for l in dlg.link_labels)
          and all('href="%s"' % u in l.text() for (_, u), l in zip(LINKS, dlg.link_labels)),
          urls)
    check("关于窗口：带程序图标", not dlg.windowIcon().isNull())
    check("关于窗口：精简（尺寸不过分大）",
          dlg.sizeHint().width() < 460 and dlg.sizeHint().height() < 320,
          (dlg.sizeHint().width(), dlg.sizeHint().height()))
    dlg.reject()
    check("关于窗口能关掉", not dlg.isVisible())

    real_warning = QMessageBox.warning
    QMessageBox.warning = staticmethod(lambda *a, **k: None)
    try:
        dlg = ServersDialog(cfg["servers"])
        check("编辑对话框读出 3 台服", dlg.table.rowCount() == 3)
        dlg._add_row("banana", "坏服")
        dlg.accept()
        check("填错地址不放行（对话框不关闭）", dlg.result() == 0, dlg.result())
        dlg.table.item(3, 0).setText("180.188.21.179:7783")
        dlg.table.item(3, 1).setText("7服")
        dlg.accept()
        got = dlg.result_servers()
        check("改对后放行，解析出 4 台（含新加的 7服）",
              dlg.result() == 1 and got[-1] == {"addr": "180.188.21.179:7783", "name": "7服"}, got)

        dup = ServersDialog([{"addr": "1.1.1.1:7777", "name": "a"},
                             {"addr": "1.1.1.1:7777", "name": "b"}])
        dup.accept()
        check("重复地址不放行", dup.result() == 0, dup.result())
    finally:
        QMessageBox.warning = real_warning

    ui.monitor.stop()
    pump(app, 0.2)
    print("\n%s" % ("全部通过" if not FAILS else "失败 %d 项：%s" % (len(FAILS), FAILS)))
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
