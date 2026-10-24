# -*- coding: utf-8 -*-
"""一键进服：把 `steam://connect/IP:端口` 交给系统，由 Steam 完成拉起/切换。

实测（SCP:SL 客户端，2026-10）：
* 游戏没开 -> Steam 自动启动游戏并直连该服务器；
* 已在游戏中 -> 请求先排队，日志见
  `[SteamServer] Waiting for scene changed to connect...`，场景切回后自动连过去；
* 游戏在主菜单等任何状态都能直接进。
所以这里只管把 URL 发出去，成败由 Steam / 游戏接管，程序不模拟任何按键。
"""

import os


def join(addr):
    """触发 steam://connect。返回 (ok, 提示文本)。"""
    url = "steam://connect/%s" % addr
    try:
        if os.name != "nt":
            return False, "一键进服只在 Windows 上可用"
        os.startfile(url)               # 走注册表协议处理 -> Steam
        return True, url
    except OSError as e:
        return False, "%s（%r）" % (url, e)
