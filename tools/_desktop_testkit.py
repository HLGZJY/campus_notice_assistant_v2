"""桌面版离线验收骨架（内部件，不直接执行，也不会被 run_regression 收集）。

解决的问题：7 个 test_desktop_*.py 需要在对应批次实现前就落地（把断言契约固化下来），
但此时 desktop/* 模块还不存在——直接 import 会报错并污染回归结果。
本模块提供统一的「未实现即跳过」语义：

  - 模块尚未实现 / 缺少接口 / 断言体未填充  →  [SKIP]，退出码 0（不阻断回归）
  - 断言失败（AssertionError 或其他异常）   →  [FAIL]，退出码 1
  - 断言通过                                →  [PASS]

约定（供 tools/run_regression.py 解析，勿改）：
  最后一行必须是以下三种之一：
      结果: 全部通过
      结果: 全部通过（N 项跳过）
      结果: 全部跳过（原因）
      结果: N 项失败 -> [名字, ...]

用法（各 test_desktop_*.py 内）：
    from tools._desktop_testkit import Suite

    suite = Suite(batch="B04", module="desktop.server", title="端口粘性")

    @suite.case("首次启动分配端口并写入 data/runtime.json")
    def _(srv):
        resolve = suite.require(srv, "resolve_port")   # 缺接口 → SKIP
        raise NotImplementedError("待 B04 实现：...")   # 未填充 → SKIP
        assert ...                                      # 真断言 → PASS/FAIL

    if __name__ == "__main__":
        suite.run()
"""
from __future__ import annotations

import contextlib
import importlib
import sys
import time
import traceback
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass


def skip(reason: str) -> NotImplementedError:
    """构造一个「跳过」异常（与真失败区分：断言失败应为 AssertionError）。"""
    return NotImplementedError(reason)


class Suite:
    """一组离线验收断言，按批次惰性激活。"""

    def __init__(self, batch: str, module: str, title: str):
        self.batch = batch
        self.module = module
        self.title = title
        self._cases: list[tuple[str, object]] = []

    # ---------- 注册 ----------
    def case(self, name: str):
        def deco(fn):
            self._cases.append((name, fn))
            return fn

        return deco

    # ---------- 断言辅助 ----------
    def require(self, mod, name: str, hint: str = ""):
        """取模块上的接口；缺失则 SKIP（说明该批次尚未实现）。"""
        if not hasattr(mod, name):
            raise skip(
                f"{getattr(mod, '__name__', mod)} 缺少接口 {name}"
                f"（{self.batch} 待实现）{('：' + hint) if hint else ''}"
            )
        return getattr(mod, name)

    def pending(self, what: str):
        """断言体尚未填充。"""
        raise skip(f"待 {self.batch} 实现：{what}")

    # ---------- 执行 ----------
    def run(self) -> int:
        print(f"== {self.batch} {self.title} ==")
        print(f"   依赖模块：{self.module}")

        mod, load_err = self._load()
        if mod is None:
            for name, _ in self._cases:
                print(f"  [SKIP] {name}  ({load_err})")
            print(f"结果: 全部跳过（{load_err}）")
            return 0

        failures: list[str] = []
        skipped = 0
        for name, fn in self._cases:
            t0 = time.time()
            try:
                fn(mod)
                cost = time.time() - t0
                print(f"  [PASS] {name}" + (f"  ({cost:.2f}s)" if cost > 1 else ""))
            except NotImplementedError as exc:
                skipped += 1
                print(f"  [SKIP] {name}  ({exc})")
            except AssertionError as exc:
                failures.append(name)
                print(f"  [FAIL] {name}  ({exc})")
            except Exception as exc:  # noqa: BLE001 - 骨架阶段要暴露真实异常类型
                failures.append(name)
                print(f"  [ERROR] {name}  ({type(exc).__name__}: {exc})")
                traceback.print_exc(limit=3)

        if failures:
            print(f"结果: {len(failures)} 项失败 -> {failures}")
            return 1
        if skipped == len(self._cases):
            print("结果: 全部跳过（断言体均为待实现占位）")
            return 0
        if skipped:
            print(f"结果: 全部通过（{skipped} 项跳过）")
            return 0
        print("结果: 全部通过")
        return 0

    def _load(self):
        try:
            return importlib.import_module(self.module), None
        except Exception as exc:  # noqa: BLE001
            return None, f"{self.module} 尚未实现（{self.batch}）：{type(exc).__name__}: {exc}"


# --------------------------------------------------------------------------
# 临时数据库：绝不在真实 data/app.db 上跑 PRAGMA / 并发写
# --------------------------------------------------------------------------
def _cleanup_db_files(p: Path) -> None:
    """删除临时库及其 WAL/共享内存/journal 附属文件。"""
    for suffix in ("", "-wal", "-shm", "-journal"):
        f = Path(str(p) + suffix)
        try:
            if f.exists():
                f.unlink()
        except OSError:
            pass


def new_tmp_db_path() -> Path:
    """在 data/ 下生成一个未使用的临时库路径（不创建文件）。"""
    tmp_dir = ROOT / "data"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    return tmp_dir / f"_tmp_{uuid.uuid4().hex}.db"


@contextlib.contextmanager
def temp_db_path():
    """产出临时库路径，退出即删除（不修改任何全局状态）。

    推荐用法：storage.db.get_connection(db_path=tmp) —— 该接口支持显式传参，
    比 monkeypatch 全局 DB_PATH 更安全。
    """
    tmp_path = new_tmp_db_path()
    _cleanup_db_files(tmp_path)
    try:
        yield tmp_path
    finally:
        _cleanup_db_files(tmp_path)


@contextlib.contextmanager
def temp_db(module_name: str = "storage.db", attr: str = "DB_PATH"):
    """把 storage.db.DB_PATH 临时指向 data/ 下的临时文件，退出即恢复并删除。

    仅在被测逻辑不接受 db_path 参数时使用（与既有 test_health.py 做法一致）。
    """
    mod = importlib.import_module(module_name)
    original = getattr(mod, attr, None)
    tmp_path = new_tmp_db_path()

    _cleanup_db_files(tmp_path)
    setattr(mod, attr, tmp_path)
    try:
        yield tmp_path
    finally:
        setattr(mod, attr, original)
        _cleanup_db_files(tmp_path)


def connect(module_name: str = "storage.db"):
    """取一个到当前（临时）DB 的连接。"""
    mod = importlib.import_module(module_name)
    return mod.get_connection()
