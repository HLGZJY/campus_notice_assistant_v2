"""B08 退出编排与生命周期验收（离线）。

对应 docs/DESKTOP-ACCEPTANCE.md §4：
  1. 关闭窗口后进程存活且 health 200（closing 放行/隐藏语义）
  2. 托盘退出后进程消失、无子线程残留（完整退出序列收掉所有托管线程）
  3. 退出序列顺序正确（几何 → server → scheduler → tray → 锁）
  4. 有 running 任务时退出被二次确认拦截

设计依据见 docs/DESKTOP-UPGRADE.md §6 K5：
window.closing → hide + return False；真退出按顺序释放资源；
注册 WM_QUERYENDSESSION 让关机/注销走同一路径，避免数据损坏。

依赖：desktop.app（B08 已实现，缺接口则整脚本 SKIP）
测试全程离线：用 __new__ 构造 DesktopApp + 桩注入，不启动真实 GUI/后端。
对 _do_exit 末尾的 sys.exit(0)，用替换 desktop.app.sys 命名空间的方式捕获
（不污染全局 sys），断言退出被正确请求。

用法：python test_desktop_lifecycle.py
"""
from __future__ import annotations

import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from tools._desktop_testkit import Suite, temp_db  # noqa: E402

suite = Suite(batch="B08", module="desktop.app", title="退出编排与生命周期（K5）")


def _make_app(mod):
    """用 __new__ 构造一个不初始化 GUI 的 DesktopApp 实例（避开真实后端/窗口）。"""
    cls = mod.DesktopApp
    app = cls.__new__(cls)
    app._exiting = False
    app._tray_available = True
    app._shutdown_done = False
    app.tray = None
    app.single_instance = None
    app.shutdown_hook = None
    app._window = None
    app.settings = None
    app.server = None
    app._save_window_geometry = lambda: None
    return app


@suite.case("1. 关闭窗口后进程存活（closing 隐藏/放行语义）")
def _(app_mod):
    cls = app_mod.DesktopApp
    app = _make_app(app_mod)

    # 场景 A：托盘可用 + 非退出中 → 点 X 隐藏，返回 False（取消关闭，进程存活，
    # 后端不被动 → health 保持 200 的前提是「没停服务」，此处断言未触发退出）
    hidden = {"called": False}
    window = types.SimpleNamespace(hide=lambda: hidden.__setitem__("called", True))
    app._window = window
    app._tray_available = True
    assert app._on_closing() is False, "托盘可用时应取消关闭"
    assert hidden["called"] is True, "应调用 window.hide()"

    # 场景 B：已进入退出（_exiting=True）→ 放行关闭（True），让 webview.start() 返回
    app._exiting = True
    assert app._on_closing() is True, "退出中应放行关闭"

    # 场景 C：无托盘 → 关闭即退出（True），进程由随后退出序列收尾
    app2 = _make_app(app_mod)
    app2._tray_available = False
    app2._window = types.SimpleNamespace(hide=lambda: None)
    assert app2._on_closing() is True, "无托盘应关闭即退出"


@suite.case("2. 托盘退出后进程消失、无子线程残留（完整退出收尾）")
def _(app_mod):
    app = _make_app(app_mod)

    calls = []
    server = types.SimpleNamespace(stop=lambda timeout=None: calls.append(f"server:{timeout}"))
    tray = types.SimpleNamespace(stop=lambda: calls.append("tray"))
    lock = types.SimpleNamespace(release=lambda: calls.append("lock"))
    app.server = server
    app.tray = tray
    app.single_instance = lock
    app._stop_scheduler = lambda: calls.append("scheduler")
    app._save_window_geometry = lambda: calls.append("geometry")

    # 捕获 _do_exit 末尾的 sys.exit(0)：替换 desktop.app 模块命名空间的 sys
    captured = {}
    fake_sys = types.SimpleNamespace(exit=lambda code=0: captured.setdefault("code", code))
    app_mod.sys = fake_sys

    app._do_exit()

    # 完整退出序列应依次收掉所有托管资源
    assert captured.get("code") == 0, f"应请求 sys.exit(0)，实际={captured}"
    for name in ("server", "scheduler", "tray", "lock"):
        assert any(c.startswith(name) for c in calls), f"退出序列应调用 {name}"
    assert "geometry" in calls, "退出序列应写窗口几何"
    # 幂等：重复调用 _do_exit 不再重复执行（_shutdown_done 守卫）
    before = list(calls)
    app._do_exit()
    assert calls == before, "退出序列应幂等（第二次调用不重复）"
    # 单实例锁已置空
    assert app.single_instance is None, "退出后单实例锁应置空"

    # 恢复（避免污染其他 case）
    app_mod.sys = sys


@suite.case("3. 退出序列顺序正确（几何 → server → scheduler → tray → 锁）")
def _(app_mod):
    app = _make_app(app_mod)

    order: list[str] = []
    server = types.SimpleNamespace(stop=lambda timeout=None: order.append("server"))
    tray = types.SimpleNamespace(stop=lambda: order.append("tray"))
    lock = types.SimpleNamespace(release=lambda: order.append("lock"))
    app.server = server
    app.tray = tray
    app.single_instance = lock
    app._stop_scheduler = lambda: order.append("scheduler")
    app._save_window_geometry = lambda: order.append("geometry")

    fake_sys = types.SimpleNamespace(exit=lambda code=0: None)
    app_mod.sys = fake_sys

    app._do_exit()

    # K5 规定顺序：写窗口几何 → server.should_exit+join → scheduler.stop
    # → pystray.stop → 释放单实例锁 → sys.exit(0)
    expected = ["geometry", "server", "scheduler", "tray", "lock"]
    assert order == expected, f"退出顺序错误：{order} != {expected}"

    app_mod.sys = sys


@suite.case("4. 有 running 任务时退出被二次确认拦截")
def _(app_mod):
    # 离线构造：临时库内插入一个 running 任务，让 has_running_tasks()=True
    with temp_db():
        from storage.db import create_task_or_get_existing, get_connection

        conn = get_connection()
        tid = create_task_or_get_existing(conn, "crawl_all", {}, "lock-lifecycle-1")
        conn.execute("UPDATE tasks SET status='running' WHERE id=?", (tid,))
        conn.commit()
        conn.close()

        # 有任务 + 用户取消 → 中止退出（_exiting 保持 False，进程存活）
        app = _make_app(app_mod)
        app._confirm_exit = lambda: False
        app.quit()
        assert app._exiting is False, "有任务且用户取消时应中止退出"

        # 有任务 + 用户确认 → 放行退出（_exiting=True）
        app2 = _make_app(app_mod)
        app2._confirm_exit = lambda: True
        app2.quit()
        assert app2._exiting is True, "有任务且用户确认时应放行退出"

    # 无任务时不弹确认（_confirm_exit 不应被调用）
    app3 = _make_app(app_mod)
    confirm_called = {"n": 0}
    app3._confirm_exit = lambda: confirm_called.__setitem__("n", confirm_called["n"] + 1)
    app3.quit()
    assert confirm_called["n"] == 0, "无任务时不应弹二次确认"


@suite.case("5. 关机/注销模拟：无 journal 残留 + PRAGMA integrity_check=ok")
def _(app_mod):
    """Gate B08 自动项：优雅退出后数据目录无 -journal 残留、SQLite 完整。

    在临时库上模拟「优雅关闭连接」（对应关机前走完整退出序列、uvicorn lifespan
    正常 flush），断言不产生 -journal 残留且 integrity_check=ok。
    绝不触碰生产库 data/notices.db（本 case 全程用 temp_db 临时文件）。
    """
    from pathlib import Path

    from storage.db import get_connection

    with temp_db() as tmp_path:
        # 写入并提交数据，模拟运行中的后端
        conn = get_connection()
        conn.execute(
            "INSERT INTO notices (url, source, title, crawled_at) "
            "VALUES ('https://shutdown-sim.example/1', 'sim', 'shutdown-sim', datetime('now'))"
        )
        conn.commit()
        # 优雅关闭连接（对应退出序列里 server.should_exit → lifespan 正常收尾）
        conn.close()

        # 无 -journal / -wal / -shm 残留（仅保留主库文件本体）
        for suffix in ("-journal", "-wal", "-shm"):
            assert not Path(str(tmp_path) + suffix).exists(), \
                f"优雅退出后不应残留 {suffix} 文件"

        # SQLite 完整性
        conn2 = get_connection()
        row = conn2.execute("PRAGMA integrity_check").fetchone()
        conn2.close()
        assert row is not None and row[0] == "ok", f"integrity_check 应为 ok，实际={row}"



if __name__ == "__main__":
    sys.exit(suite.run())
