# -*- coding: utf-8 -*-
"""配置与路径。

* 所有文件都放 **exe 同目录**（portable）：`settings.json` / `history.csv` /
  `latest.md` / `events.log` / `SL_Restart.log`。
* 服务器清单由托盘菜单编辑，写回 `settings.json`。
"""

import json
import os
import sys
from datetime import datetime

VERSION = "0.1.0"               # 单一来源：关于窗口用它；tools/version_info.txt 需手工同步

DEFAULTS = {
    "servers": [
        {"addr": "180.188.21.179:7777", "name": "1服"},
        {"addr": "180.188.21.179:7779", "name": "3服"},
        {"addr": "180.188.21.179:7781", "name": "5服"},
    ],
    "interval": 1.0,            # 轮询间隔（秒）
    "timeout": 0.5,             # 单次等待（秒）
    "retries": 1,               # 抗丢包重试次数
    "counter_pos": [10, 10],    # 人数条位置
    "restart_pos": [10, 48],    # 重启提示位置（让开人数条）
    "counter_font": 16,         # 人数条字号
    "restart_font": 28,         # 重启提示字号
    "restart_seconds": 10,      # 重启提示显示秒数
    "restart_color": "#FF3B3B",  # 重启提示字色（红）
    "alpha": 150,               # 半透明黑底不透明度 0-255
    "sound_enabled": False,     # 重启提示音（默认关闭）
    "sound_file": "",           # 自定义音频绝对路径；留空使用系统提示音
    "sound_volume": 70,         # 提示音音量 0-100
    "windows_notifications": False,  # Windows 通知中心提醒（默认关闭）
}

_BASE = None                    # 测试时用 set_base_dir 改写到临时目录


def base_dir():
    """打包后 = exe 所在目录；源码运行 = 本文件所在目录。"""
    if _BASE:
        return _BASE
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


def set_base_dir(p):
    global _BASE
    _BASE = p


def path(*parts):
    return os.path.join(base_dir(), *parts)


def parse_addr(s):
    """'1.2.3.4:7777' -> ('1.2.3.4', 7777)；非法返回 None。"""
    s = str(s or "").strip().replace("：", ":")
    host, _, port = s.rpartition(":")
    if not host or not port.isdigit():
        return None
    port = int(port)
    if not 1 <= port <= 65535:
        return None
    if any(c in host for c in " \t/:;"):
        return None
    return host, port


def normalize_servers(rows):
    """只留地址合法的项；同地址只保留第一条；服名为空则回落成「端口服」。"""
    out, seen = [], set()
    for r in rows or []:
        if not isinstance(r, dict):
            continue
        parsed = parse_addr(r.get("addr"))
        if not parsed:
            continue
        host, port = parsed
        addr = "%s:%d" % (host, port)
        if addr in seen:
            continue
        seen.add(addr)
        name = str(r.get("name") or "").strip() or "%d服" % port
        out.append({"addr": addr, "name": name})
    return out


def load():
    cfg = {k: (list(v) if isinstance(v, list) else v) for k, v in DEFAULTS.items()}
    cfg["servers"] = [dict(s) for s in DEFAULTS["servers"]]
    try:
        with open(path("settings.json"), encoding="utf-8") as f:
            data = json.load(f)
        for k, v in data.items():          # 只认已知键，坏键忽略
            if k in DEFAULTS:
                cfg[k] = v
    except Exception:
        pass
    good = normalize_servers(cfg.get("servers"))
    cfg["servers"] = good or [dict(s) for s in DEFAULTS["servers"]]
    for key in ("sound_enabled", "windows_notifications"):
        if not isinstance(cfg[key], bool):
            cfg[key] = DEFAULTS[key]
    if not isinstance(cfg["sound_file"], str):
        cfg["sound_file"] = ""
    try:
        cfg["sound_volume"] = max(0, min(100, int(cfg["sound_volume"])))
    except (TypeError, ValueError, OverflowError):
        cfg["sound_volume"] = DEFAULTS["sound_volume"]
    return cfg


def save(cfg):
    keep = {k: cfg[k] for k in DEFAULTS if k in cfg}
    with open(path("settings.json"), "w", encoding="utf-8") as f:
        json.dump(keep, f, ensure_ascii=False, indent=2)


def log(msg):
    """无控制台窗口，所以一律写文件；日志失败绝不能把程序带崩。"""
    try:
        with open(path("SL_Restart.log"), "a", encoding="utf-8") as f:
            f.write("%s %s\n" % (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), msg))
    except Exception:
        pass
