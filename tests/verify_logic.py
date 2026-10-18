# -*- coding: utf-8 -*-
"""纯逻辑自检（不需要 Qt，也不联网）：`python tests/verify_logic.py`

覆盖：A2S 回包解析 / 地址校验 / 配置读写 / 人数条文案 / 重启判定 /
一轮采集（打桩）对 CSV、latest.md、events.log 的写入。
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
            {"addr": "1.1.1.1:7777", "name": "1服"}]
    got = config.normalize_servers(rows)
    assert got == [{"addr": "1.1.1.1:7777", "name": "1服"},
                   {"addr": "2.2.2.2:7781", "name": "7781服"}], got


def test_counts_and_restart():
    text = monitor.format_counts([("1服", 41), ("3服", None), ("5服", 19)])
    assert text == "[1服] 41人  |  [3服] -人  |  [5服] 19人", text
    assert monitor.is_restart(41, 0) is True
    assert monitor.is_restart(1, 0) is True
    assert monitor.is_restart(0, 0) is False       # 已经归零，不重复报
    assert monitor.is_restart(None, 0) is False    # 离线回来的 0 不算重启
    assert monitor.is_restart(41, 42) is False


def test_config_roundtrip(tmp):
    config.set_base_dir(tmp)
    cfg = config.load()                            # 没有 settings.json -> 内置默认
    assert [s["name"] for s in cfg["servers"]] == ["1服", "3服", "5服"], cfg["servers"]
    assert cfg["counter_font"] == 16 and cfg["restart_font"] == 28
    assert tuple(cfg["counter_pos"]) == (10, 10) and tuple(cfg["restart_pos"]) == (10, 48)

    cfg["servers"] = [{"addr": "9.9.9.9:7777", "name": "9服"}]
    cfg["interval"] = 2.0
    config.save(cfg)
    again = config.load()
    assert again["servers"] == [{"addr": "9.9.9.9:7777", "name": "9服"}], again
    assert again["interval"] == 2.0

    with open(config.path("settings.json"), "w", encoding="utf-8") as f:
        f.write('{"interval": 3, "不认识的键": 1, "servers": "坏了"}')
    fixed = config.load()
    assert fixed["interval"] == 3
    assert [s["name"] for s in fixed["servers"]] == ["1服", "3服", "5服"]   # 坏清单回落默认
    config.log("自检写入一行日志")
    assert "自检写入一行日志" in open(config.path("SL_Restart.log"), encoding="utf-8").read()


def test_monitor_round(tmp):
    config.set_base_dir(tmp)
    cfg = config.load()
    cfg["servers"] = [{"addr": "1.1.1.1:7777", "name": "1服"},
                      {"addr": "1.1.1.1:7779", "name": "3服"}]
    fake = FakeA2S({"1.1.1.1:7777": [info(41), info(41), info(0)],
                    "1.1.1.1:7779": [None, info(5)]})
    old, a2s.query = a2s.query, fake
    try:
        m = Monitor(cfg)
        m._round()
        assert m.snapshot["text"] == "[1服] 41人  |  [3服] -人", m.snapshot
        assert m.snapshot["online"] == 1
        assert m.take_restarts() == []

        m._round()                                  # 7779 上线，7777 不变
        assert m.take_restarts() == []
        assert m.snapshot["text"] == "[1服] 41人  |  [3服] 5人", m.snapshot

        m._round()                                  # 7777 归零 -> 重启
        assert m.take_restarts() == ["1服"], m.snapshot
        assert m.snapshot["text"] == "[1服] 0人  |  [3服] 5人", m.snapshot

        m._round()                                  # 持续 0，不重复报
        assert m.take_restarts() == []

        csv_text = open(config.path("history.csv"), encoding="utf-8-sig").read().strip().splitlines()
        assert csv_text[0] == "time,address,name,players,max", csv_text[0]
        assert len(csv_text) == 1 + 7, csv_text        # 1 表头 + 7 条在线记录（离线那一轮跳过）
        assert sum(1 for line in csv_text if "1.1.1.1:7779" in line) == 3, csv_text
        assert sum(1 for line in csv_text if "1.1.1.1:7777" in line) == 4, csv_text

        md = open(config.path("latest.md"), encoding="utf-8").read()
        assert "| 1服 | 1.1.1.1:7777 | 0 | 45 | 在线 |" in md, md
        assert "| 3服 | 1.1.1.1:7779 | 5 | 45 | 在线 |" in md, md

        events = open(config.path("events.log"), encoding="utf-8").read()
        assert "人数归零 41 -> 0" in events, events
        assert "上线 5/45" in events, events
        assert events.count("人数归零") == 1, events
    finally:
        a2s.query = old

    cfg2 = config.load()
    cfg2["servers"] = [{"addr": "1.1.1.1:7777", "name": "1服"},
                       {"addr": "1.1.1.1:7779", "name": "3服"}]
    fake_off = FakeA2S({"1.1.1.1:7777": [info(7), None], "1.1.1.1:7779": [None]})
    old, a2s.query = a2s.query, fake_off
    try:
        m2 = Monitor(cfg2)
        m2._round()
        m2._round()                                  # 7777 掉线；7779 一次都没通 -> 不算事件
        md = open(config.path("latest.md"), encoding="utf-8").read()
        assert "| 1服 | 1.1.1.1:7777 | - | - | 离线 |" in md, md
        assert "| 3服 | 1.1.1.1:7779 | - | - | 离线 |" in md, md
        events = open(config.path("events.log"), encoding="utf-8").read()
        assert events.count("离线") == 1 and "1服(1.1.1.1:7777) 离线" in events, events
        assert m2.take_restarts() == []              # 离线不等于重启
    finally:
        a2s.query = old


def main():
    tmp = tempfile.mkdtemp(prefix="sl_restart_test_")
    tests = [("A2S 回包解析", test_a2s_parse),
             ("地址校验", test_addr),
             ("清单清洗", test_normalize),
             ("人数条文案/重启判定", test_counts_and_restart),
             ("配置读写", lambda: test_config_roundtrip(tmp)),
             ("一轮采集（打桩）", lambda: test_monitor_round(tmp))]
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
