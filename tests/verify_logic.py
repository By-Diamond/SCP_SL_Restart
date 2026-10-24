# -*- coding: utf-8 -*-
"""纯逻辑自检（不需要 Qt，也不联网）：`python tests/verify_logic.py`

覆盖：A2S 回包解析 / 地址校验 / 配置读写 / 人数条文案 / 重启判定（归零 + 掉线）/
回合时长（累计 / 冻结 / 清零 / 变黄）/ 一轮采集（打桩）对 CSV、latest.md、events.log 的写入。
"""

import os
import shutil
import struct
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import a2s                     # noqa: E402
import config                  # noqa: E402
import monitor                 # noqa: E402
from monitor import Monitor    # noqa: E402


def info(players, maxplayers=45, name="Srv"):
    return {"name": name, "map": "Facility", "game": "SCP: Secret Laboratory",
            "players": players, "maxplayers": maxplayers}


def packet(players=10, maxplayers=45, name="Custom", mapname="Facility",
           folder="scp secret laboratory", game="SCP: Secret Laboratory",
           appid=44970, protocol=17):
    out = b"\xff\xff\xff\xffI" + bytes([protocol])
    for s in (name, mapname, folder, game):
        out += s.encode("utf-8") + b"\x00"
    return out + struct.pack("<H", appid) + bytes([players, maxplayers])


class FakeA2S:
    """按地址排队返回脚本值；队列用完后重复最后一个值。"""

    def __init__(self, script):
        self.script = {k: list(v) for k, v in script.items()}
        self.calls = []

    def __call__(self, host, port, timeout=0.5, retries=1):
        key = "%s:%d" % (host, port)
        self.calls.append(key)
        seq = self.script.get(key)
        if not seq:
            return None
        return seq.pop(0) if len(seq) > 1 else seq[0]


class Clock:
    """可控时钟，替掉 Monitor 里的 time.monotonic，让"每秒加一秒"可复现。"""

    def __init__(self, t=1000.0):
        self.t = t

    def __call__(self):
        return self.t

    def tick(self, dt=1.0):
        self.t += dt


def read(name):
    """读数据文件；还没写出来（没有事件）就当空串。"""
    try:
        return open(config.path(name), encoding="utf-8").read()
    except FileNotFoundError:
        return ""


def test_a2s_parse():
    info_ = a2s.parse(packet(players=41, name="HZ-1", maxplayers=45))
    assert info_["players"] == 41, info_
    assert info_["maxplayers"] == 45, info_
    assert info_["name"] == "HZ-1", info_
    assert info_["map"] == "Facility", info_
    assert a2s.parse(b"\xff\xff\xff\xffA" + b"\x01\x02\x03\x04") is None   # 挑战包
    assert a2s.parse(b"garbage") is None
    assert a2s.A2S_INFO.endswith(b"TSource Engine Query\x00")


def test_addr():
    assert config.parse_addr("180.188.21.179:7777") == ("180.188.21.179", 7777)
    assert config.parse_addr(" 1.2.3.4：7779 ") == ("1.2.3.4", 7779)      # 全角冒号
    assert config.parse_addr("example.com:27015") == ("example.com", 27015)
    for bad in ("", "1.2.3.4", "1.2.3.4:", ":7777", "1.2.3.4:0",
                "1.2.3.4:70000", "1.2.3.4:abc", "1.2.3.4:7777:1", "a b:7777"):
        assert config.parse_addr(bad) is None, bad


def test_normalize():
    rows = [{"addr": "1.1.1.1:7777", "name": "1服"},
            {"addr": "bad", "name": "x"},
            {"addr": "2.2.2.2:7781", "name": "  "},
            "not-a-dict",
            {"addr": "3.3.3.3:7779", "name": "3服", "timer": False},
            {"addr": "1.1.1.1:7777", "name": "1服"}]
    got = config.normalize_servers(rows)
    assert got == [{"addr": "1.1.1.1:7777", "name": "1服", "timer": True},
                   {"addr": "2.2.2.2:7781", "name": "7781服", "timer": True},
                   {"addr": "3.3.3.3:7779", "name": "3服", "timer": False}], got


def test_counts_and_restart():
    text = monitor.format_counts([("1服", 41), ("3服", None), ("5服", 19)])
    assert text == "[1服] 41人  |  [3服] -人  |  [5服] 19人", text

    # 人数归零 = 重启
    assert monitor.is_restart(41, 0) is True
    assert monitor.is_restart(1, 0) is True
    assert monitor.is_restart(0, 0) is False       # 已经归零，不重复报
    assert monitor.is_restart(None, 0) is False    # 离线回来的 0 不算重启
    assert monitor.is_restart(41, 42) is False
    # 掉线 = 重启（人数 >0 变成查不到）
    assert monitor.is_restart(41, None) is True
    assert monitor.is_restart(0, None) is False
    assert monitor.is_restart(None, None) is False
    assert monitor.restart_kind(41, 0) == "zero"
    assert monitor.restart_kind(41, None) == "offline"
    assert monitor.restart_kind(41, 41) is None


def test_duration_text():
    assert monitor.fmt_duration(0) == "0:00"
    assert monitor.fmt_duration(1) == "0:01"
    assert monitor.fmt_duration(61) == "1:01"
    assert monitor.fmt_duration(380) == "6:20"
    assert monitor.fmt_duration(1200) == "20:00"
    assert monitor.fmt_duration(3930) == "65:30"       # 一直用分钟，不带小时位
    assert monitor.fmt_duration(-5) == "0:00"

    # 用户给的样式：有的服显示时长、有的不显示
    segs = monitor.format_segments([("1服", 40, 1, True),
                                    ("2服", 2, 9999, False),
                                    ("7服", 3, 380, True)])
    assert monitor.segments_text(segs) == "[1服] 40人 0:01  |  [2服] 2人  |  [7服] 3人 6:20"
    kinds = [k for _, k in segs]
    assert kinds.count(monitor.YELLOW) == 0

    # 0 人 / 离线不显示时长
    assert monitor.segments_text(monitor.format_segments([("1服", 0, 300, True)])) == "[1服] 0人"
    assert monitor.segments_text(monitor.format_segments([("1服", None, 300, True)])) == "[1服] -人"

    # 超过 20 分钟（1200 秒）那一段变黄
    yellow = monitor.format_segments([("7服", 3, 1200, True)])
    assert yellow[-1] == (" 20:00", monitor.YELLOW), yellow
    assert monitor.format_segments([("7服", 3, 1199, True)])[-1] == (" 19:59", None)


def test_config_roundtrip(tmp):
    config.set_base_dir(tmp)
    cfg = config.load()                            # 没有 settings.json -> 内置默认
    assert [s["name"] for s in cfg["servers"]] == ["1服", "3服", "5服"], cfg["servers"]
    assert cfg["counter_font"] == 16 and cfg["restart_font"] == 28
    assert tuple(cfg["counter_pos"]) == (10, 10) and tuple(cfg["restart_pos"]) == (10, 48)
    assert cfg["show_timer"] is True                       # 计时器默认开
    assert cfg["timer_yellow_after"] == 1200               # 20 分钟变黄
    assert all(s["timer"] for s in cfg["servers"])         # 每台服默认都显示

    cfg["servers"] = [{"addr": "9.9.9.9:7777", "name": "9服", "timer": False}]
    cfg["interval"] = 2.0
    cfg["show_timer"] = False
    config.save(cfg)
    again = config.load()
    assert again["servers"] == [{"addr": "9.9.9.9:7777", "name": "9服", "timer": False}], again
    assert again["interval"] == 2.0
    assert again["show_timer"] is False

    with open(config.path("settings.json"), "w", encoding="utf-8") as f:
        f.write('{"interval": 3, "不认识的键": 1, "servers": "坏了"}')
    fixed = config.load()
    assert fixed["interval"] == 3
    assert [s["name"] for s in fixed["servers"]] == ["1服", "3服", "5服"]   # 坏清单回落默认
    assert [s["timer"] for s in fixed["servers"]] == [True, True, True]
    config.log("自检写入一行日志")
    assert "自检写入一行日志" in open(config.path("SL_Restart.log"), encoding="utf-8").read()


def test_monitor_round(tmp):
    config.set_base_dir(tmp)
    cfg = config.load()
    cfg["servers"] = [{"addr": "1.1.1.1:7777", "name": "1服", "timer": True},
                      {"addr": "1.1.1.1:7779", "name": "3服", "timer": True}]
    clock = Clock()
    fake = FakeA2S({"1.1.1.1:7777": [info(41), info(41), info(41), info(0)],
                    "1.1.1.1:7779": [None, None, info(5)]})
    old, a2s.query = a2s.query, fake
    try:
        m = Monitor(cfg)
        m._clock = clock
        m._round()                       # 7777 通(41)；7779 第 1 次失败 -> 先不动
        assert m.snapshot["text"] == "[1服] 41人 0:00  |  [3服] -人", m.snapshot["text"]
        assert m.snapshot["online"] == 1
        assert m.take_restarts() == []

        clock.tick()
        m._round()                       # 7779 第 2 次失败 -> 判离线（从没上线过，不记事件）
        assert m.snapshot["text"] == "[1服] 41人 0:01  |  [3服] -人", m.snapshot["text"]
        events = read("events.log")
        assert "离线" not in events, events

        clock.tick()
        m._round()                       # 7779 上线 5 人
        assert m.snapshot["text"] == "[1服] 41人 0:02  |  [3服] 5人 0:01", m.snapshot["text"]
        events = read("events.log")
        assert "上线 5/45" in events, events

        clock.tick()
        m._round()                       # 7777 归零 -> 重启，时长清零
        assert m.take_restarts() == [("1服", "1.1.1.1:7777")], m.snapshot
        assert m.snapshot["text"] == "[1服] 0人  |  [3服] 5人 0:02", m.snapshot["text"]

        clock.tick()
        m._round()                       # 持续 0：不重复报，1服 也不计时
        assert m.take_restarts() == []
        assert m.snapshot["text"] == "[1服] 0人  |  [3服] 5人 0:03", m.snapshot["text"]

        # 时长那一段是独立分段，UI 才好单独上色
        assert (" 0:03", None) in m.snapshot["segments"], m.snapshot["segments"]

        csv_text = open(config.path("history.csv"), encoding="utf-8-sig").read().strip().splitlines()
        assert csv_text[0] == "time,address,name,players,max", csv_text[0]
        # 5 轮：7777 记 5 条；7779 只有上线那 3 轮记（掉线/没数据不记）
        assert len(csv_text) == 1 + 8, csv_text
        assert sum(1 for line in csv_text if "1.1.1.1:7779" in line) == 3, csv_text

        md = read("latest.md")
        assert "| 1服 | 1.1.1.1:7777 | 0 | 45 | 在线 |" in md, md
        assert "| 3服 | 1.1.1.1:7779 | 5 | 45 | 在线 |" in md, md

        events = read("events.log")
        assert "人数归零 41 -> 0" in events, events
        assert events.count("人数归零") == 1, events
    finally:
        a2s.query = old

    # ---- 掉线 = 重启（>0 变成查不到，连续 2 次失败才判）----
    off_dir = os.path.join(tmp, "offline")
    os.makedirs(off_dir, exist_ok=True)
    config.set_base_dir(off_dir)
    cfg2 = config.load()
    cfg2["servers"] = [{"addr": "9.9.9.9:7777", "name": "9服", "timer": True}]
    clock2 = Clock()
    fake_off = FakeA2S({"9.9.9.9:7777": [info(7), None, None]})
    old, a2s.query = a2s.query, fake_off
    try:
        m2 = Monitor(cfg2)
        m2._clock = clock2
        m2._round()
        assert m2.snapshot["text"] == "[9服] 7人 0:00", m2.snapshot["text"]

        clock2.tick()
        m2._round()                      # 第 1 次失败：显示不动、不误报、不计时
        assert m2.snapshot["text"] == "[9服] 7人 0:00", m2.snapshot["text"]
        assert m2.take_restarts() == []

        clock2.tick()
        m2._round()                      # 第 2 次失败 -> 掉线也算重启
        assert m2.take_restarts() == [("9服", "9.9.9.9:7777")], m2.take_restarts()
        assert m2.snapshot["text"] == "[9服] -人", m2.snapshot["text"]
        assert m2._timer["9.9.9.9:7777"] == 0.0, m2._timer        # 时长清零
        events = read("events.log")
        assert "掉线 7 -> 查不到（疑似服务器重启）" in events, events
        assert "离线" not in events, events
    finally:
        a2s.query = old


def test_timer_switch(tmp):
    """每台服的勾选 + 托盘总开关，都能立刻影响人数条。"""
    dir_ = os.path.join(tmp, "timer")
    os.makedirs(dir_, exist_ok=True)
    config.set_base_dir(dir_)
    cfg = config.load()
    cfg["servers"] = [{"addr": "1.1.1.1:7777", "name": "1服", "timer": True},
                      {"addr": "1.1.1.1:7779", "name": "3服", "timer": False}]
    old, a2s.query = a2s.query, FakeA2S({"1.1.1.1:7777": [info(10)],
                                         "1.1.1.1:7779": [info(20)]})
    try:
        m = Monitor(cfg)
        m._clock = Clock()
        m._round()
        assert m.snapshot["text"] == "[1服] 10人 0:00  |  [3服] 20人", m.snapshot["text"]

        m.set_show_timer(False)          # 总开关关掉：两台都不带时长了
        assert m.snapshot["text"] == "[1服] 10人  |  [3服] 20人", m.snapshot["text"]
        m.set_show_timer(True)           # 打开：按每台服自己的勾选
        assert m.snapshot["text"] == "[1服] 10人 0:00  |  [3服] 20人", m.snapshot["text"]
    finally:
        a2s.query = old


def main():
    tmp = tempfile.mkdtemp(prefix="sl_restart_test_")
    tests = [("A2S 回包解析", test_a2s_parse),
             ("地址校验", test_addr),
             ("清单清洗", test_normalize),
             ("人数条文案/重启判定", test_counts_and_restart),
             ("回合时长文案/变黄", test_duration_text),
             ("配置读写", lambda: test_config_roundtrip(tmp)),
             ("一轮采集（打桩）", lambda: test_monitor_round(tmp)),
             ("时长开关", lambda: test_timer_switch(tmp))]
    failed = 0
    try:
        for name, fn in tests:
            try:
                fn()
                print("PASS  %s" % name)
            except Exception as e:
                failed += 1
                print("FAIL  %s -> %r" % (name, e))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print("\n%d/%d 通过" % (len(tests) - failed, len(tests)))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
