# Restart Alerts Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 为服务器重启增加可选提示音、自定义音频、音量和 Windows 通知。

**Architecture:** 现有 App 汇总重启事件后调用独立提醒控制器。设置对话框维护编辑副本，确认后保存；Qt 音频和 Windows 通知异步执行。

**Tech Stack:** Python、PySide6 QtMultimedia、QProcess、Windows PowerShell / WinRT。

---

### Task 1: 提醒控制器及配置

Files: 新增 alerts.py、tests/verify_alerts.py；修改 config.py。

1. 编写默认关闭、配置读写、音频路径、音量、XML 转义及独立触发测试。
2. 运行 `python tests/verify_alerts.py`，确认新模块缺失导致失败。
3. 使用 QMediaPlayer / QAudioOutput 播放音频；使用 QProcess 异步调用编码后的 PowerShell 脚本。
4. 运行 `python tests/verify_alerts.py`，确认相关测试通过。

### Task 2: 设置界面与重启事件集成

Files: 新增 alert_dialog.py；修改 sl_restart.py、tools/gui_check.py、tests/verify_alerts.py。

1. 添加设置确认、取消、试听、测试通知及事件合并测试。
2. 实现独立开关、文件选择、音量和测试按钮。
3. 加入托盘菜单和重启事件回调。
4. 运行 `python tests/verify_alerts.py`、`python tests/verify_drag.py`、`python tests/verify_logic.py` 和 `python tools/gui_check.py`。

### Task 3: 文档、打包与交付

Files: 修改 README.md；生成 dist/SL Restart.exe。

1. 记录设置方法、系统通知条件和自定义文件路径行为。
2. 使用隔离 PATH 的 PyInstaller 打包，并检查 QtMultimedia 和音频解码依赖。
3. 验证打包版界面导出和启动，执行 `git diff --check`。
4. 提交并推送现有功能分支，更新 PR #1 的标题、描述和验证结果。
