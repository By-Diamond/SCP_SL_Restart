# ANCHOR — SL Restart

> 给接手的大模型 / 协作者看的项目锚点。读完这份就能改代码，不必翻全部历史。
> 最后更新：2026-10-31（v0.4.2）

## 1. 这是什么

SCP:SL 服务器监控小工具（Windows 托盘常驻）：

1. 左上角**人数条**：每台服 `[1服] 41人 0:01`，鼠标穿透，不挡操作。
2. 服务器重启时弹**红色提示** `[1服] [3服] 重启`，10 秒淡出；**点击直连**晚重启的那台（`steam://connect`）。
3. 可选：重启提示音、Windows 通知（带「进服」按钮）、自动挤服、第三方只读数据端口。
4. 回合时长是**本地估算**（A2S 查不到真实回合状态）。

运行：`python sl_restart.py` 或 `run.bat`；打包后双击 `dist\SL Restart.exe`。

## 2. 数据流

```
monitor.py (后台线程, 1s 一轮)
  ├─ a2s.query → 人数 / 上限
  ├─ 重启判定 → _pending（UI 取走弹提示）
  ├─ 回合估算状态机 → snapshot["segments"]
  └─ 写 history.csv / latest.md / events.log
        ↓ (只读 snapshot，全程加锁)
sl_restart.py (UI 线程)
  ├─ counter（人数条） / toast（重启提示）
  ├─ tray.py（托盘菜单）
  ├─ alerts.py（声音 + Windows 通知）
  ├─ api_server.py（可选 127.0.0.1:17777/snapshot）
  └─ 各 QDialog：server_dialog / grab_dialog / alert_dialog / about
```

**硬规则**：UI 线程不碰 socket、不碰文件；监控线程不碰 Qt。

## 3. 文件职责

| 文件 | 作用 |
|---|---|
| `sl_restart.py` | 入口 + `App`（托盘、快照同步 `_sync`、弹提示 `_push_restart`、退出兜底） |
| `monitor.py` | 采集、重启判定、回合估算状态机、写数据文件 |
| `config.py` | `DEFAULTS` / `load()` 归一 / `save()` / 路径 / 版本号 / 日志 |
| `overlay.py` | `Overlay`（人数条 + 重启提示同一个类，靠 duration/movable 区分） |
| `alerts.py` | 提示音、Windows 通知（PowerShell Toast + 自定义协议进服按钮） |
| `api_server.py` | 只读 HTTP（`GET /snapshot`、`GET /join?addr=`、`GET /`），只绑回环 |
| `tray.py` / `autostart.py` / `joiner.py` | 托盘图标菜单、开机自启（HKCU）、steam 直连 |
| `a2s.py` | 极简 A2S_INFO 查询（不依赖第三方库） |
| `server_dialog.py` / `grab_dialog.py` / `alert_dialog.py` / `about.py` | 四个对话框 |
| `tools/gui_check.py` | 离屏 GUI 自检（托盘回调 / 弹窗 / 对话框） |
| `tests/verify_*.py` | 逻辑、提醒、拖动、回合估算 |

## 4. 关键配置键（settings.json，全部有默认值）

| 键 | 默认 | 说明 |
|---|---|---|
| `servers` | 3 台 | `[{"addr","name","timer"}]`，`timer`=该服显不显示时长 |
| `show_counter` | `True` | 人数条显隐（托盘/左键可切，**会记住**） |
| `show_timer` | `True` | 回合时长总开关（托盘菜单） |
| `show_toast` | `True` | **重启提示框开关**（「提醒设置…」里改） |
| `round_min_players` | `2` | 开局估算门槛人数（1–100） |
| `round_start_delay` | `45` | 开局倒计时秒数（0–300） |
| `timer_yellow_after` | `1200` | 时长超过多少秒变黄 |
| `offline_fails` | `2` | 连续查询失败几次才判掉线 |
| `restart_seconds` | `10` | 红字提示停留秒数 |
| `sound_enabled` / `windows_notifications` | `False` | 两种提醒开关 |
| `grab_servers` | `[]` | 自动挤服清单 `[{"addr","name","enabled","threshold"}]` |
| `third_party_api` | `False` | 本地数据端口开关（状态持久化） |
| `api_port` | `17777` | 只绑 `127.0.0.1` |

`load()` 会做：未知键丢弃、布尔值坏值回落默认、数值夹紧、清单清洗去重。

## 5. 回合估算状态机（`monitor._round_phase`）

```
waiting ──人数≥门槛──> countdown ──等够 delay──> running
   │                        │  └──满员持续1秒──────> running（timer 从 0:00 起算）
   └──人数<门槛 / 查询失败 / 暂停 / 采样间隔>5s────────┘（running 不受影响）
```

1. `waiting`：人数不够门槛，**时间栏留空**（不显示任何文字）。
2. `countdown`：显示 ` 等待 0:45`（`等待 ` + `fmt_duration(ceil(剩余秒数))`，**不带 `≈`**）。
3. `running`：显示 ` 0:00` 起累加；超过阈值那一段标 `yellow`。
4. 转 `running` 时 `timer = 已观察秒数 - delay`（多出来的部分不丢）。
5. **满员加速**：`countdown` 中人数达到服务器上限并持续 `FULL_START_GRACE_S=1s` → 直接 `running`、`timer = 0`。
   起始时刻记在 `_full_since`，人数掉下满员就取消；剩余倒计时先到的按普通规则走。
6. `running` 后降到 1 人**继续计时**；人数归零 / 确认掉线 → 重置为 `waiting`。
7. **暂停**：`set_paused(True)` 取消倒计时；暂停期间手动刷新不推进（`not self._paused` 才 `_advance_round`）。
8. 改估算参数 → `reset_round_timers()`：清时长/阶段/倒计时，不动人数与重启历史。

相关方法：`reset_round_timers()` / `_cancel_countdowns()` / `_advance_round()` / `_make_snapshot()`。

**外部可见字段**（`GET /snapshot` 的每台服）：`timer`（秒）、`timer_on`、`phase`
（`waiting` / `countdown` / `running`）、`countdown`（倒计时剩余秒）。凡是要显示时间栏的
消费方（如灵动岛插件）都必须读 `phase`：只拿 `timer` 会把「没到门槛」和「倒计时」都画成 `0:00`。

### 5.1 灵动岛「进服」按钮走本地接口

`GET /join?addr=IP:端口` → 后台线程 `joiner.join(addr)`（`steam://connect`），立刻返回 200；
地址非法返回 400。岛上的按钮用 `http://127.0.0.1:<api_port>/join?addr=…`，别再走
`slrestart://` 协议——那会新起一个打包 exe、先解包 59 MB（实测 1.6 s 起），慢到用户以为没反应。
协议注册仍然保留：**Windows 原生通知**的按钮只能走协议激活。

## 6. 重启判定（`restart_kind`）

1. 人数 **>0 → 0**：`zero`，立即判重启。
2. 人数 **>0 → 查不到**：要**连续 `offline_fails` 次**失败才判 `offline`。
3. **查询成功即 `_fails[addr]=0`**——否则「失败→成功→失败」会被误判成掉线，还会把时长清空。
4. 本来就 0 人 / 本来就离线，不重复报。
5. **空服抑制**：判定为重启时，若过去 `QUIET_WINDOW_S=10s` 内所有采样都 ≤ `QUIET_MAX_PLAYERS=2`、且这 10 秒没开过局
   （`_started_at` 不在窗口内，当前也不是 `running`）→ 只写 `events.log`「忽略重启」，**不弹提示、不进 `_pending`**；
   时长 / 阶段照常重置。采样历史存在 `_recent[addr]`（`deque[(monotonic, players)]`，只留 10 秒）。

## 7. 运行产物（exe 同目录）

`settings.json`、`history.csv`（每轮一行）、`latest.md`（当前快照表）、`events.log`（事件流水）、`SL_Restart.log`（程序日志，反馈问题时发这个）。

## 8. 开发 / 自检

```bat
python sl_restart.py                  :: 直接跑（debug 只 run 不 build）
python tests\verify_logic.py          :: 逻辑 / 配置 / 监控 / 挤服 / 端口
python tests\verify_round_timer.py    :: 回合估算状态机（可控时钟）
python tests\verify_alerts.py         :: 声音 / 通知 / 对话框
python tests\verify_drag.py           :: 拖动 / 排序
python tools\gui_check.py             :: 离屏 GUI 自检（看输出「全部通过」）
```

不自检时可用：`--smoke N`（跑 N 秒退出）、`--instance NAME`（并行测试不抢单实例锁）。

**测试纪律**：只测新增/改动点，旧用例通过后不重跑；全量冒烟只在发布前做。

## 9. 打包

```bat
build.bat            :: PyInstaller 单文件 → dist\SL Restart.exe
```

版本号两处要同步：`config.py:VERSION` 和 `tools/version_info.txt`（filevers/prodvers/StringStruct）。
**未经用户明确要求不要 build、不要改版本号。**

## 10. 已知坑 / 别踩

1. `tray.acts[...].trigger()` 会**自己翻转勾选**，测试里别先 `setChecked()`。
2. `gui_check.py` 结尾用 `os._exit()`：离屏窗口太多，Qt 正常收尾会乱退出码。
3. 退出路径有坑：先停心跳 → 收托盘 → 停监控 / 端口 / 通知，全程限时 + 5s 硬兜底；改这里务必跑 `gui_check` 的「退出 <1.5s」。
4. `third_party_api` 的长连接客户端（灵动岛）会拖住端口关闭——`_Server.handle_error` 已静默，`stop()` 不 join。
5. 时间显示**不要加 `≈`**（用户明确要求）；README 若写了 `≈` 属于待改文档。
6. PR #3 的 **WinRT 原生通知**实现已被用户**拒绝**，别再把 `alerts.py` 换成 WinRT。
7. 人数条显隐由 `_counter_on` / `show_counter` 决定，任何地方不要「顺手 `set_visible(True)` 弹回来」。

## 11. 当前状态

1. 版本 `0.4.2`；`dist\SL Restart.exe` 已按最新代码打包（59,211,455 字节，`FileVersion = 0.4.2`）。
2. 本轮改动：满员 1 秒加速开局（`FULL_START_GRACE_S` / `_full_since`）、空服重启抑制（`QUIET_WINDOW_S` / `_recent`）、
   时间栏改「等待 0:45」且不够门槛留空；第三方适配新增 `GET /join`、快照新增 `phase` / `countdown`，
   灵动岛按钮改走本地接口（不再起新进程解包）。
3. `README.md` 与本文件已同步新规则。
4. git：v0.4.1 已推送（`3dc2219`）；v0.4.2 改动见本次提交。
5. `dist\旧版_4849_可删.exe` 是上一版残留，旧实例退出后可直接删。
