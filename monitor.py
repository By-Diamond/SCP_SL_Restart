# -*- coding: utf-8 -*-
"""采集与事件：1 秒一轮。

两种「服务器重启」（用户口径）：

* 人数 **>0 掉到 0** —— 人全掉光；
* 人数 **>0 变成查不到**（掉线）—— 服务器突然下线。

掉线要**连续失败 N 次**（默认 2）才判，免得网络抖动误报成重启。

顺带算**回合时长**：只有「在线且有人」的那一秒才累加；人数 0 / 离线 / 暂停监控
时冻结，检测到重启清零。

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
YELLOW = "yellow"               # 时长超阈值的那一段，UI 负责换成黄色


def restart_kind(prev_players, now_players):
    """重启类型：'zero'（人数归零）/ 'offline'（掉线）/ None（没重启）。

    只有上一轮「有人在」才算：本来就 0 人 / 本来就离线的，不重复报。
    """
    if not prev_players:
        return None
    if now_players == 0:
        return "zero"
    if now_players is None:
        return "offline"
    return None


def is_restart(prev_players, now_players):
    """同上，只要 True / False。"""
    return restart_kind(prev_players, now_players) is not None


def fmt_duration(secs):
    """秒 -> M:SS（一直用分钟：65 分钟就是 '65:30'）。"""
    s = max(0, int(secs))
    return "%d:%02d" % (s // 60, s % 60)


def format_segments(rows, yellow_after=1200):
    """[(服名, 人数 or None, 时长秒, 是否显示时长)] -> 单行分段

    返回 [(文本, kind)]：kind=None 走默认色（白），'yellow' 表示该段时长超阈值。
    人数为 0 或离线时不显示时长（用户口径）。
    """
    segs = []
    for i, (name, players, secs, show) in enumerate(rows):
        if i:
            segs.append((SEP, None))
        segs.append(("[%s] %s" % (name, "-人" if players is None else "%d人" % players), None))
        if show and players:
            text = " %s" % fmt_duration(secs)
            segs.append((text, YELLOW if int(secs or 0) >= yellow_after else None))
    return segs


def segments_text(segs):
    return "".join(t for t, _ in segs)


def format_counts(items):
    """[(名字, 人数 or None)] -> '[1服] 41人  |  [3服] -人'（不带时长）。"""
    return segments_text(format_segments([(n, p, 0, False) for n, p in items]))


class Monitor:
    def __init__(self, cfg):
        self.cfg = cfg
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._servers = config.normalize_servers(cfg.get("servers"))
        self._prev = {}            # addr -> 上次人数（None = 离线；键不存在 = 还没见过）
        self._players = {}         # addr -> 最近人数（None = 离线）
        self._fails = {}           # addr -> 连续查询失败次数
        self._timer = {}           # addr -> 回合时长（只累加"在线且有人"的秒数）
        self._t_prev = None        # 上一轮的时刻，用来算这一轮该加多少秒
        self._clock = time.monotonic
        self._show_timer = bool(cfg.get("show_timer", True))
        self._pending = []         # 待提示的重启事件（(服名, 地址)）
        self._paused = False
        self._force = False
        self._thread = None
        self.snapshot = self._make_snapshot()

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
            self._fails.clear()
            self._timer.clear()
            self._t_prev = None
            self._players = {s["addr"]: None for s in self._servers}
            self.snapshot = self._make_snapshot()
        self.refresh_now()

    def set_show_timer(self, flag):
        """托盘菜单切「显示回合时长」：立刻重画，不用等下一轮。"""
        self._show_timer = bool(flag)
        with self._lock:
            self.snapshot = self._make_snapshot()

    def set_paused(self, flag):
        flag = bool(flag)
        if not flag:
            self._t_prev = None          # 恢复监控：暂停期间那一段不算进回合时长
        self._paused = flag

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

    def _timer_on(self, s):
        """这台服要不要显示时长：总开关开着 且 这台服自己勾了。"""
        return self._show_timer and bool(s.get("timer", True))

    def _make_snapshot(self, players=None, timers=None, rows=(), stamp="", online=0):
        players = self._players if players is None else players
        timers = self._timer if timers is None else timers
        segs = format_segments(
            [(s["name"], players.get(s["addr"]), (timers.get(s["addr"]) or 0), self._timer_on(s))
             for s in self._servers],
            self.cfg.get("timer_yellow_after", 1200))
        return {"text": segments_text(segs), "segments": segs, "rows": list(rows),
                "stamp": stamp, "online": online}

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

        # 这一轮该给"在线且有人"的服加多少秒（暂停/休眠回来后不会把空档算进去）
        t_now = self._clock()
        dt = 0.0
        if self._t_prev is not None:
            dt = t_now - self._t_prev
            if dt < 0 or dt > max(5.0, float(self.cfg["interval"]) * 3):
                dt = 0.0
        self._t_prev = t_now

        stamp = time.strftime("%Y-%m-%d %H:%M:%S")
        max_fails = max(1, int(self.cfg.get("offline_fails", 2)))
        players, timers = {}, dict(self._timer)
        rows, events, restarts, live = [], [], [], 0
        for (s, _host, _port), r in zip(targets, results):
            addr, name = s["addr"], s["name"]
            prev = self._prev.get(addr)
            stale = False                       # 这一轮没拿到新数据（还没到判离线那一步）
            if r:
                live += 1
                now = r["players"]
                kind = restart_kind(prev, now)
                if prev is None and addr in self._prev:      # 上轮查不到、这轮通了
                    events.append("%s(%s) 上线 %d/%d" % (name, addr, now, r["maxplayers"]))
                if kind == "zero":
                    events.append("%s(%s) 人数归零 %d -> 0（疑似服务器重启）" % (name, addr, prev))
                if kind:
                    timers[addr] = 0.0                       # 重启 -> 回合时长清零
                    restarts.append((name, addr))
                elif now > 0:
                    timers[addr] = timers.get(addr, 0.0) + dt   # 在线且有人 -> 攒时长
                self._prev[addr] = now
            else:
                n = self._fails.get(addr, 0) + 1
                self._fails[addr] = n
                if n < max_fails:                # 第 1 次失败：显示与计时都保持上一次的样子
                    stale = True
                    now = prev
                else:
                    now = None
                    if prev is not None:
                        if restart_kind(prev, None):             # >0 掉线：也算一次重启
                            events.append("%s(%s) 掉线 %d -> 查不到（疑似服务器重启）"
                                          % (name, addr, prev))
                            restarts.append((name, addr))
                            timers[addr] = 0.0
                        else:
                            events.append("%s(%s) 离线" % (name, addr))
                    self._prev[addr] = None      # 记下"见过、当前离线"，之后上线才记得住
            players[addr] = now
            rows.append({"name": name, "addr": addr, "players": now,
                         "max": r["maxplayers"] if r else None,
                         "online": bool(r), "stale": stale})

        self._players = players
        self._timer = timers
        with self._lock:
            self._pending.extend(restarts)
            self.snapshot = self._make_snapshot(players, timers, rows, stamp, live)
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
                    if row["online"] and not row["stale"]:   # 离线/没拿到数据的跳过不记
                        w.writerow([stamp, row["addr"], row["name"], row["players"], row["max"]])

            with open(config.path("latest.md"), "w", encoding="utf-8") as f:
                f.write("# SCP:SL 人数快照\n\n更新时间：%s\n\n" % stamp)
                f.write("| 服务器 | 地址 | 人数 | 上限 | 状态 |\n|---|---|---|---|---|\n")
                for row in rows:
                    if row["stale"]:
                        continue
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
