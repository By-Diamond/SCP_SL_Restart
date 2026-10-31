# -*- coding: utf-8 -*-
"""采集与事件：1 秒一轮。

两种「服务器重启」（用户口径）：

* 人数 **>0 掉到 0** —— 人全掉光；
* 人数 **>0 变成查不到**（掉线）—— 服务器突然下线。

掉线要**连续失败 N 次**（默认 2）才判，免得网络抖动误报成重启。

顺带估算**回合时长**：人数达到门槛后先显示「等待 m:ss」倒计时，倒计时结束才开始计时；
人数 0 / 确认离线时重置，暂停和查询失败时不补算空档。

不依赖 Qt：采集跑在后台线程，负责写 `history.csv` / `latest.md` / `events.log`。
界面只读 `snapshot`，并用 `take_restarts()` 取走"要弹提示"的事件——
这样 UI 线程永远不碰 socket、也不碰文件。
"""

import csv
import math
import os
import threading
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor

import a2s
import config

SEP = "  |  "
YELLOW = "yellow"               # 时长超阈值的那一段，UI 负责换成黄色

FULL_START_GRACE_S = 1.0        # 开局倒计时中满员：持续满 1 秒就直接起算回合时长
QUIET_WINDOW_S = 10.0           # 重启抑制：回看最近 10 秒的人数
QUIET_MAX_PLAYERS = 2           # 重启抑制：这 10 秒一直 ≤2 人且没开局，就不算重启


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
        self._round_phase = {}     # waiting / countdown / running；本地估算，不是服务端事件
        self._countdown = {}       # addr -> 已观察到的连续开局等待秒数
        self._full_since = {}      # addr -> 倒计时中人数达到上限的时刻（满员加速开局）
        self._started_at = {}      # addr -> 最近一次开始计时回合时长的时刻
        self._recent = {}          # addr -> deque[(时刻, 人数)]，用于「过去 10 秒」重启抑制
        self._t_prev = None        # 上一轮的时刻，用来算这一轮该加多少秒
        self._clock = time.monotonic
        self._show_timer = bool(cfg.get("show_timer", True))
        self._pending = []         # 待提示的重启事件（(服名, 地址)）
        self._grab_servers = []    # 自动挤服：已勾选的 {"addr","name","threshold"}，按优先顺序
        self._grab_enabled = False
        self._grab_pending = []    # 待处理的挤服命中（dict：addr/name/players/max/threshold）
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
            self._round_phase.clear()
            self._countdown.clear()
            self._full_since.clear()
            self._started_at.clear()
            self._recent.clear()
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
        if flag:
            self._cancel_countdowns()     # 暂停：倒计时作废，恢复后从「开局」重来
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

    def set_grab(self, servers, enabled):
        """自动挤服开关。servers 里只保留勾选的项，顺序就是进服优先级。"""
        with self._lock:
            self._grab_servers = [dict(s) for s in servers or [] if s.get("enabled")]
            self._grab_enabled = bool(enabled) and bool(self._grab_servers)
            if not self._grab_enabled:
                self._grab_servers = []
                self._grab_pending.clear()
        if self._grab_enabled:
            self.refresh_now()               # 开启立刻查一轮，不等下一个周期

    def take_grab_hits(self):
        """UI 线程取走待处理的挤服命中（命中后监控端已自动关闭挤服）。"""
        with self._lock:
            out, self._grab_pending = self._grab_pending, []
        return out

    # ------------------------------ 内部 ------------------------------

    def reset_round_timers(self):
        """改了估算参数后重新观察；不动人数与重启判定历史。"""
        with self._lock:
            self._timer.clear()
            self._round_phase.clear()
            self._countdown.clear()
            self._full_since.clear()
            self._started_at.clear()
            self.snapshot = self._make_snapshot()

    def _cancel_countdowns(self):
        """把还在倒计时的服退回「等待」；已经在计时的回合不受影响。"""
        for addr, phase in list(self._round_phase.items()):
            if phase == "countdown":
                self._round_phase[addr] = "waiting"
                self._countdown[addr] = 0.0
                self._full_since.pop(addr, None)

    def _advance_round(self, addr, players, maxplayers, dt, now_t):
        """本地估算：等够门槛 -> 倒计时 -> 从 0:00 计时。

        倒计时中人数达到服务器上限（满员）并持续 1 秒，就直接开局——
        满员说明回合大概率已经开了，不用再等满 45 秒。
        """
        minimum = self.cfg.get("round_min_players", 2)
        delay = self.cfg.get("round_start_delay", 45)
        phase = self._round_phase.get(addr, "waiting")
        if phase == "running":
            self._timer[addr] = self._timer.get(addr, 0.0) + dt
            return
        if players < minimum:
            self._round_phase[addr] = "waiting"
            self._countdown[addr] = 0.0
            self._full_since.pop(addr, None)
            return
        if delay == 0:
            self._round_phase[addr] = "running"
            self._timer[addr] = self._timer.get(addr, 0.0) + dt
            self._started_at[addr] = now_t
            self._full_since.pop(addr, None)
            return
        if phase != "countdown":
            self._round_phase[addr] = "countdown"
            self._countdown[addr] = 0.0
            elapsed = 0.0
        else:
            elapsed = self._countdown.get(addr, 0.0) + dt
        if maxplayers and players >= maxplayers:            # 满员：再等 1 秒就开局
            since = self._full_since.get(addr)
            if since is None:
                self._full_since[addr] = now_t
            elif now_t - since >= FULL_START_GRACE_S:
                self._round_phase[addr] = "running"
                self._timer[addr] = 0.0
                self._started_at[addr] = now_t
                self._full_since.pop(addr, None)
                return
        else:
            self._full_since.pop(addr, None)
        self._countdown[addr] = elapsed
        if elapsed >= delay:
            self._round_phase[addr] = "running"
            self._timer[addr] = elapsed - delay     # 倒计时多出来的部分算进回合时长
            self._started_at[addr] = now_t
            self._full_since.pop(addr, None)

    def _track_players(self, addr, now_t, players):
        """记一条人数采样，只保留最近 10 秒（重启抑制用）。"""
        history = self._recent.setdefault(addr, deque())
        history.append((now_t, players))
        cutoff = now_t - QUIET_WINDOW_S
        while history and history[0][0] < cutoff:
            history.popleft()

    def _is_quiet_restart(self, addr, now_t):
        """重启抑制：过去 10 秒人数一直 ≤2 且没开过局，就不算重启。

        空服掉线 / 归零本来就没有回合可丢，弹提示只会打扰。
        """
        if self._round_phase.get(addr) == "running":
            return False
        started = self._started_at.get(addr)
        if started is not None and now_t - started <= QUIET_WINDOW_S:
            return False
        history = self._recent.get(addr)
        if not history:
            return False
        return all(p is not None and p <= QUIET_MAX_PLAYERS for _t, p in history)

    def _timer_on(self, s):
        """这台服要不要显示时长：总开关开着 且 这台服自己勾了。"""
        return self._show_timer and bool(s.get("timer", True))

    def _make_snapshot(self, players=None, timers=None, rows=(), stamp="", online=0):
        players = self._players if players is None else players
        timers = self._timer if timers is None else timers
        segs = []
        for i, server in enumerate(self._servers):
            if i:
                segs.append((SEP, None))
            addr = server["addr"]
            count = players.get(addr)
            segs.extend(format_segments([(server["name"], count, 0, False)]))
            if not self._timer_on(server) or not count:
                continue
            phase = self._round_phase.get(addr, "waiting")
            if phase == "running":
                secs = timers.get(addr) or 0.0
                segs.append((" " + fmt_duration(secs),
                             YELLOW if secs >= self.cfg.get("timer_yellow_after", 1200) else None))
            elif phase == "countdown":
                remaining = max(0, self.cfg.get("round_start_delay", 45)
                                - self._countdown.get(addr, 0))
                segs.append((" 等待 " + fmt_duration(math.ceil(remaining)), None))
            # waiting：人数还不够门槛，时间栏留空（什么都不显示）
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
        # 自动挤服：勾选的服也一并查询（与清单重叠的复用主查询结果，不重复发包）
        with self._lock:
            grab_cfg = list(self._grab_servers) if self._grab_enabled else []
        covered = {s["addr"] for s, _h, _p in targets}
        grab_targets = []
        for g in grab_cfg:
            parsed = config.parse_addr(g["addr"])
            if not parsed:
                continue
            addr = "%s:%d" % parsed
            if addr in covered:
                continue
            covered.add(addr)
            grab_targets.append((g, parsed[0], parsed[1]))
        all_targets = targets + grab_targets
        if not all_targets:
            return
        with ThreadPoolExecutor(max_workers=min(8, len(all_targets))) as ex:
            results = list(ex.map(
                lambda t: a2s.query(t[1], t[2], self.cfg["timeout"], self.cfg["retries"]),
                all_targets))
        by_addr = {}
        for (s, _h, _p), r in zip(targets, results[:len(targets)]):
            by_addr[s["addr"]] = r
        for (g, _h, _p), r in zip(grab_targets, results[len(targets):]):
            by_addr[g["addr"]] = r

        # 这一轮该给"在线且有人"的服加多少秒（暂停/休眠回来后不会把空档算进去）
        t_now = self._clock()
        dt = 0.0
        if self._t_prev is not None:
            dt = t_now - self._t_prev
            if dt < 0 or dt > max(5.0, float(self.cfg["interval"]) * 3):
                dt = 0.0
                self._cancel_countdowns()       # 长时间没采样：倒计时作废，不补算
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
                self._fails[addr] = 0                        # 查询成功：清零，别让「失败-成功-失败」误判
                live += 1
                now = r["players"]
                self._track_players(addr, t_now, now)
                kind = restart_kind(prev, now)
                quiet = bool(kind) and self._is_quiet_restart(addr, t_now)
                if prev is None and addr in self._prev:      # 上轮查不到、这轮通了
                    events.append("%s(%s) 上线 %d/%d" % (name, addr, now, r["maxplayers"]))
                if kind == "zero":
                    if quiet:
                        events.append("%s(%s) 人数归零 %d -> 0（过去 %d 秒 ≤%d 人且未开局，忽略重启）"
                                      % (name, addr, prev, int(QUIET_WINDOW_S), QUIET_MAX_PLAYERS))
                    else:
                        events.append("%s(%s) 人数归零 %d -> 0（疑似服务器重启）" % (name, addr, prev))
                if kind:
                    timers[addr] = 0.0                       # 重启 -> 回合时长清零
                    self._round_phase[addr] = "waiting"
                    self._countdown[addr] = 0.0
                    self._full_since.pop(addr, None)
                    if not quiet:
                        restarts.append((name, addr))
                elif now > 0 and not self._paused:
                    self._advance_round(addr, now, r["maxplayers"], dt, t_now)   # 在线且有人 -> 按状态估算
                    timers[addr] = self._timer.get(addr, 0.0)
                self._prev[addr] = now
            else:
                if self._round_phase.get(addr) == "countdown":
                    self._round_phase[addr] = "waiting"
                    self._countdown[addr] = 0.0
                    self._full_since.pop(addr, None)
                n = self._fails.get(addr, 0) + 1
                self._fails[addr] = n
                if n < max_fails:                # 第 1 次失败：显示与计时都保持上一次的样子
                    stale = True
                    now = prev
                else:
                    now = None
                    if prev is not None:
                        if restart_kind(prev, None):             # >0 掉线：也算一次重启
                            quiet = self._is_quiet_restart(addr, t_now)
                            if quiet:
                                events.append("%s(%s) 掉线 %d -> 查不到（过去 %d 秒 ≤%d 人且未开局，忽略重启）"
                                              % (name, addr, prev, int(QUIET_WINDOW_S), QUIET_MAX_PLAYERS))
                            else:
                                events.append("%s(%s) 掉线 %d -> 查不到（疑似服务器重启）"
                                              % (name, addr, prev))
                                restarts.append((name, addr))
                            timers[addr] = 0.0
                            self._round_phase[addr] = "waiting"
                            self._countdown[addr] = 0.0
                            self._full_since.pop(addr, None)
                        else:
                            events.append("%s(%s) 离线" % (name, addr))
                    self._prev[addr] = None      # 记下"见过、当前离线"，之后上线才记得住
            # 阶段也要给出去：数字端口 / 灵动岛插件只拿 timer 是分不清
            # 「还没开局（时间栏留空）」和「倒计时（等待 0:32）」的，都会画成 0:00。
            phase = self._round_phase.get(addr, "waiting")
            left = 0
            if phase == "countdown":
                left = int(math.ceil(max(0, self.cfg.get("round_start_delay", 45)
                                         - self._countdown.get(addr, 0.0))))
            players[addr] = now
            rows.append({"name": name, "addr": addr, "players": now,
                         "max": r["maxplayers"] if r else None,
                         "online": bool(r), "stale": stale,
                         "timer": int(timers.get(addr) or 0),
                         "timer_on": self._timer_on(s),
                         "phase": phase, "countdown": left})

        self._players = players
        self._timer = timers
        if grab_cfg:
            self._check_grab(grab_cfg, by_addr)
        with self._lock:
            self._pending.extend(restarts)
            self.snapshot = self._make_snapshot(players, timers, rows, stamp, live)
        self._write(stamp, rows, events)

    def _check_grab(self, grab_cfg, by_addr):
        """任意勾选的服人数低于阈值（留空 = 该服人数上限）→ 记命中并自动关闭挤服。

        按勾选顺序检测，进第一个低于阈值的服；查不到（离线）的跳过。
        """
        for g in grab_cfg:
            r = by_addr.get(g["addr"])
            if not r:
                continue
            threshold = g.get("threshold") or r["maxplayers"]   # 不填 = 人数上限
            if not threshold:
                continue                                        # 上限也未知，等下一轮
            if r["players"] is not None and r["players"] < threshold:
                with self._lock:
                    self._grab_pending.append({"addr": g["addr"],
                                               "name": g.get("name") or g["addr"],
                                               "players": r["players"],
                                               "max": r["maxplayers"],
                                               "threshold": threshold})
                    self._grab_enabled = False                  # 每次开启只进一次
                    self._grab_servers = []
                return

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
