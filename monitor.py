# -*- coding: utf-8 -*-
"""采集与事件：1 秒一轮；**人数 >0 -> 0 判为服务器重启**（用户口径）。

不依赖 Qt：采集跑在后台线程，负责写 `history.csv` / `latest.md` / `events.log`。
界面只读 `snapshot`，并用 `take_restarts()` 取走"要弹提示"的事件——
这样 UI 线程永远不碰 socket、也不碰文件。
"""

import csv
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import a2s
import config

SEP = "  |  "


def is_restart(prev_players, now_players):
    """人数从 >0 掉到 0 = 服务器重启。"""
    return bool(prev_players) and now_players == 0


def format_counts(items):
    """[(名字, 人数 or None)] -> '[1服] 41人  |  [3服] -人'"""
    return SEP.join("[%s] %s" % (n, "-人" if p is None else "%d人" % p) for n, p in items)


class Monitor:
    def __init__(self, cfg):
        self.cfg = cfg
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._servers = config.normalize_servers(cfg.get("servers"))
        self._prev = {}            # addr -> 上次人数（None = 离线；键不存在 = 还没见过）
        self._players = {}         # addr -> 最近人数（None = 离线）
        self._pending = []         # 待提示的重启事件（服名）
        self._paused = False
        self._force = False
        self._thread = None
        self.snapshot = {"text": self._render(), "rows": [], "stamp": "", "online": 0}

    # ------------------------------ 对外 ------------------------------

    def start(self):
        self._thread = threading.Thread(target=self._loop, name="monitor", daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        self._wake.set()

    def set_servers(self, servers):
        with self._lock:
            self._servers = config.normalize_servers(servers)
            self._prev.clear()
            self._players = {s["addr"]: None for s in self._servers}
            self.snapshot = {"text": self._render(), "rows": [], "stamp": "", "online": 0}
        self.refresh_now()

    def set_paused(self, flag):
        self._paused = bool(flag)

    @property
    def paused(self):
        return self._paused

    def refresh_now(self):
        self._force = True
        self._wake.set()

    def take_restarts(self):
        """UI 线程取走待提示的重启事件。"""
        with self._lock:
            out, self._pending = self._pending, []
        return out

    # ------------------------------ 内部 ------------------------------

    def _render(self, players=None):
        players = self._players if players is None else players
        return format_counts([(s["name"], players.get(s["addr"])) for s in self._servers])

    def _loop(self):
        while not self._stop.is_set():
            t0 = time.time()
            force, self._force = self._force, False
            if force or not self._paused:
                try:
                    self._round()
                except Exception as e:                       # 单轮炸了不能停掉监控
                    config.log("[monitor] 轮询异常：%r" % e)
            self._wake.clear()
            self._wake.wait(max(0.05, float(self.cfg["interval"]) - (time.time() - t0)))

    def _round(self):
        targets = []
        for s in self._servers:
            parsed = config.parse_addr(s["addr"])
            if parsed:
                targets.append((s, parsed[0], parsed[1]))
        if not targets:
            return
        with ThreadPoolExecutor(max_workers=min(8, len(targets))) as ex:
            results = list(ex.map(
                lambda t: a2s.query(t[1], t[2], self.cfg["timeout"], self.cfg["retries"]),
                targets))

        stamp = time.strftime("%Y-%m-%d %H:%M:%S")
        players, rows, events, restarts, live = {}, [], [], [], 0
        for (s, _host, _port), r in zip(targets, results):
            addr, name = s["addr"], s["name"]
            prev = self._prev.get(addr)
            if r:
                live += 1
                players[addr] = r["players"]
                if prev is None and addr in self._prev:          # 上轮查不到、这轮通了
                    events.append("%s(%s) 上线 %d/%d" % (name, addr, r["players"], r["maxplayers"]))
                if is_restart(prev, r["players"]):
                    line = "%s(%s) 人数归零 %d -> 0（疑似服务器重启）" % (name, addr, prev)
                    events.append(line)
                    restarts.append(name)
                self._prev[addr] = r["players"]
            else:
                players[addr] = None
                if prev is not None:
                    events.append("%s(%s) 离线" % (name, addr))
                self._prev[addr] = None
            rows.append({"name": name, "addr": addr,
                         "players": r["players"] if r else None,
                         "max": r["maxplayers"] if r else None,
                         "online": bool(r)})

        self._players = players
        with self._lock:
            self._pending.extend(restarts)
            self.snapshot = {"text": self._render(players), "rows": rows,
                             "stamp": stamp, "online": live}
        self._write(stamp, rows, events)

    def _write(self, stamp, rows, events):
        try:
            csv_path = config.path("history.csv")
            new = not os.path.exists(csv_path)
            with open(csv_path, "a", newline="", encoding="utf-8-sig") as f:
                w = csv.writer(f)
                if new:
                    w.writerow(["time", "address", "name", "players", "max"])
                for row in rows:
                    if row["online"]:                        # 离线跳过不记
                        w.writerow([stamp, row["addr"], row["name"], row["players"], row["max"]])

            with open(config.path("latest.md"), "w", encoding="utf-8") as f:
                f.write("# SCP:SL 人数快照\n\n更新时间：%s\n\n" % stamp)
                f.write("| 服务器 | 地址 | 人数 | 上限 | 状态 |\n|---|---|---|---|---|\n")
                for row in rows:
                    if row["online"]:
                        f.write("| %s | %s | %d | %d | 在线 |\n"
                                % (row["name"], row["addr"], row["players"], row["max"]))
                    else:
                        f.write("| %s | %s | - | - | 离线 |\n" % (row["name"], row["addr"]))

            if events:
                with open(config.path("events.log"), "a", encoding="utf-8") as f:
                    for line in events:
                        f.write("%s %s\n" % (stamp, line))
        except Exception as e:
            config.log("[monitor] 写数据文件失败：%r" % e)
