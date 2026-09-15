"""桌面壳包（v0.2.0 桌面版）。

技术路线：pywebview（窗口）+ pystray（托盘）+ 自研轻量胶水层，
单进程内嵌 uvicorn 托管现有后端（68 个端点零改动）。

本包只与后端通过两个接口耦合：
  1. server.py  —— 起停 uvicorn 线程（K2）
  2. /api/v1/desktop/* 控制面路由（B11 落地，见 DESKTOP-UPGRADE.md §4.3）

换壳时只需重写窗口/托盘部分（DESKTOP-UPGRADE.md §3.5 壳可替换边界）。

开发模式入口：python -m desktop
"""
from __future__ import annotations

__all__ = ["__version__"]

__version__ = "0.2.1"
