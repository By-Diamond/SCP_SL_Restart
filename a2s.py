# -*- coding: utf-8 -*-
"""SCP:SL 服务器查询：Steam A2S_INFO。

实测：SCP:SL 服务端在**游戏端口**上就响应 A2S（appid 44970），无需任何 API key。
回包里能拿到 名称 / 地图 / 人数 / 上限；`map` 恒为 Facility，没有回合信息。
"""

import socket

A2S_INFO = b"\xff\xff\xff\xffTSource Engine Query\x00"


def _cstr(buf, i):
    j = buf.index(b"\x00", i)
    return buf[i:j].decode("utf-8", "replace"), j + 1


def parse(data):
    """解析 A2S_INFO 回包；挑战包(0x41)/其它异常返回 None。"""
    if data[:5] != b"\xff\xff\xff\xffI":
        return None
    i = 6                      # 4 字节头 + 'I' + 协议号
    name, i = _cstr(data, i)
    mapname, i = _cstr(data, i)
    i = _cstr(data, i)[1]      # folder
    game, i = _cstr(data, i)
    i += 2                     # appid
    return {"name": name, "map": mapname, "game": game,
            "players": data[i], "maxplayers": data[i + 1]}


def query(host, port, timeout=0.5, retries=1):
    """在线返回 dict，否则 None。

    A2S 会间歇性丢包（实测同一台 5 次里丢 1 次），所以默认重试 1 次。
    """
    for _ in range(int(retries) + 1):
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(timeout)
        try:
            s.sendto(A2S_INFO, (host, port))
            data, _ = s.recvfrom(65535)
            if data[:5] == b"\xff\xff\xff\xffA":        # 需要挑战码
                s.sendto(A2S_INFO + data[5:9], (host, port))
                data, _ = s.recvfrom(65535)
            info = parse(data)
            if info is not None:
                return info
        except (socket.timeout, OSError):
            pass
        finally:
            s.close()
    return None
