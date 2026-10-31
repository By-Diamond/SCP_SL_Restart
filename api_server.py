# -*- coding: utf-8 -*-
"""第三方适配：本地 HTTP 数据端口。

勾选托盘菜单「第三方适配」后，在本机回环地址开一个小 HTTP 服务，
把监控快照（服务器名 / 人数 / 上限 / 状态 / 回合时长）以 JSON 吐给
第三方程序（如灵动岛插件）——数据都是 monitor 已经算好的，这里只做搬运。

* 只绑 127.0.0.1，不监听外网；菜单不开就完全不起服务。
* 接口：GET /snapshot -> 快照 JSON；GET /join?addr=IP:端口 -> 一键进服；
  GET / -> 自述；其它 404。
* 不依赖 Qt，跑在后台线程；读的只是 monitor.snapshot（原子引用），
  不会碰 socket 查询、也不会碰 UI。
"""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import config

APP = "SL_Restart"


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"          # 带 Content-Length，支持 keep-alive

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path.rstrip("/") in ("/snapshot", ):
            self._send(200, self.server.api.snapshot())
        elif path.rstrip("/") == "/join":
            self._send(*self.server.api.join(self.path))
        elif path == "/":
            self._send(200, {"app": APP, "version": config.VERSION,
                             "endpoints": ["/snapshot", "/join?addr=IP:端口"]})
        else:
            self._send(404, {"error": "not found",
                             "endpoints": ["/snapshot", "/join?addr=IP:端口"]})

    def _send(self, code, obj):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        pass                                    # 无控制台窗口，静默


class _Server(ThreadingHTTPServer):
    """客户端硬断（keep-alive 被掐、进程退出）只记一行日志，不往控制台吐 traceback。"""

    def handle_error(self, request, client_address):
        config.log("[api] 客户端连接异常（忽略）：%s" % (client_address,))


class ApiServer:
    """开关封装：start/stop 可重复调用；端口被占用时 start 返回 False。"""

    def __init__(self, monitor, cfg_getter):
        self.monitor = monitor
        self._cfg = cfg_getter                  # () -> cfg dict（UI 线程在改，读没问题）
        self._lock = threading.Lock()
        self._srv = None

    def start(self, port):
        with self._lock:
            if self._srv is not None:
                return True
            try:
                srv = _Server(("127.0.0.1", int(port)), _Handler)
            except (OSError, ValueError) as e:
                config.log("[api] 数据端口 %s 开启失败：%r" % (port, e))
                return False
            srv.daemon_threads = True
            srv.block_on_close = False      # 退出时不等挂着 keep-alive 的客户端，免得关端口卡住
            srv.api = self
            self._srv = srv
            threading.Thread(target=srv.serve_forever, name="api",
                             daemon=True).start()
        config.log("[api] 第三方适配已开启：http://127.0.0.1:%d/snapshot" % port)
        return True

    def stop(self):
        with self._lock:
            srv, self._srv = self._srv, None
        if srv:
            srv.shutdown()
            srv.server_close()
            config.log("[api] 第三方适配已关闭")

    @property
    def running(self):
        return self._srv is not None

    def join(self, path):
        """GET /join?addr=IP:端口：灵动岛卡片的「进服」按钮走这里。

        按钮如果走 `slrestart://` 协议，会**新起一个打包 exe、重新解包**才轮到进服，
        实测 1.6 秒起步（冷启动更久）；直接打到已运行的进程就只剩 os.startfile 的时间。
        """
        from urllib.parse import parse_qs, urlparse
        addr = (parse_qs(urlparse(path).query).get("addr") or [""])[0].strip()
        if not config.parse_addr(addr):
            return 400, {"ok": False, "error": "invalid addr", "addr": addr}
        threading.Thread(target=self._join_async, args=(addr,),
                         name="join", daemon=True).start()
        return 200, {"ok": True, "addr": addr}

    @staticmethod
    def _join_async(addr):
        try:
            import joiner
            ok, msg = joiner.join(addr)
            config.log("[api] 灵动岛按钮进服 %s：ok=%s %s" % (addr, ok, msg))
        except Exception as e:                    # 后台线程炸了不能影响服务
            config.log("[api] 灵动岛按钮进服失败：%r" % e)

    def snapshot(self):
        """组装快照 JSON：每次请求现取，monitor.snapshot 是原子引用，无锁可读。"""
        cfg = self._cfg() or {}
        snap = self.monitor.snapshot
        servers = []
        for r in snap.get("rows") or ():
            players = r.get("players")
            servers.append({
                "addr": r.get("addr"),
                "name": r.get("name"),
                "players": None if players is None else int(players),
                "max": None if r.get("max") is None else int(r["max"]),
                "online": bool(r.get("online")),
                "stale": bool(r.get("stale")),
                "timer": int(r.get("timer") or 0),      # monitor 已算好的回合时长（秒）
                "timer_on": bool(r.get("timer_on")),
                # 阶段：waiting（人数不够门槛，时间栏留空）/ countdown（等待 m:ss）/ running
                "phase": r.get("phase") or "waiting",
                "countdown": int(r.get("countdown") or 0),   # 倒计时剩余秒（仅 countdown 阶段）
            })
        return {
            "app": APP,
            "version": config.VERSION,
            "time": snap.get("stamp", ""),
            "online": int(snap.get("online") or 0),
            "paused": bool(self.monitor.paused),
            "timer_yellow_after": int(cfg.get("timer_yellow_after", 1200)),
            "servers": servers,
        }
