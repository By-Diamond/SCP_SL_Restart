# -*- coding: utf-8 -*-
"""开机自启：只写 HKCU\\...\\Run 这一处，且**只有用户点菜单才会写**。

程序自身从不主动调用 set_enabled(True)。
"""

import os
import sys

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
APP_NAME = "SL Restart"


def _target():
    """打包后 = exe 路径；源码运行 = pythonw + 脚本路径。"""
    if getattr(sys, "frozen", False):
        return '"%s"' % os.path.abspath(sys.executable)
    script = os.path.abspath(os.path.join(os.path.dirname(__file__), "sl_restart.py"))
    exe = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
    if not os.path.exists(exe):
        exe = sys.executable
    return '"%s" "%s"' % (exe, script)


def _winreg():
    if sys.platform != "win32":
        return None
    try:
        import winreg
        return winreg
    except ImportError:
        return None


def is_enabled():
    wr = _winreg()
    if not wr:
        return False
    try:
        with wr.OpenKey(wr.HKEY_CURRENT_USER, RUN_KEY) as k:
            wr.QueryValueEx(k, APP_NAME)
        return True
    except OSError:
        return False


def set_enabled(flag):
    """返回 (是否成功, 提示语)。"""
    wr = _winreg()
    if not wr:
        return False, "开机自启仅支持 Windows"
    try:
        with wr.CreateKey(wr.HKEY_CURRENT_USER, RUN_KEY) as k:
            if flag:
                wr.SetValueEx(k, APP_NAME, 0, wr.REG_SZ, _target())
            else:
                try:
                    wr.DeleteValue(k, APP_NAME)
                except OSError:
                    pass
        return True, ("已开启开机自启" if flag else "已关闭开机自启")
    except OSError as e:
        return False, "注册表写入失败：%s" % e
