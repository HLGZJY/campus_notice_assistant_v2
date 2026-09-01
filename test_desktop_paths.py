"""B16 应用路径三态与数据迁移验收（离线）。

对应 docs/DESKTOP-ACCEPTANCE.md §4：
  1. dev / portable / installed 三态解析正确
  2. 老版本 exe 同级 data/ 仍可读取（向后兼容）
  3. 迁移幂等（重复执行结果一致）
  4. 迁移失败时原数据完整保留

设计依据见 docs/DESKTOP-UPGRADE.md §2.4 R1：
Inno 实际装到 {localappdata}\\CampusNoticeAssistant，已是 per-user 可写目录，
故数据目录迁移由 P0 下调为 P1（整洁性收益，非阻塞项）；
**但老用户 exe 同级 data/ 必须能继续读到（向后兼容优先）**。

三态判定（B16.T1）：
- dev       ：未冻结 → 仓库根 /data
- portable  ：冻结且 exe 同级存在 data/（老布局/便携，向后兼容优先）
- installed ：冻结且无 exe 同级 data → %APPDATA%/CampusNoticeAssistant/data

依赖：utils.app_paths（B16 补齐 get_data_mode / resolve_legacy_data_dir / migrate_data_dir）

用法：python test_desktop_paths.py
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).parent))

from tools._desktop_testkit import Suite  # noqa: E402

suite = Suite(batch="B16", module="utils.app_paths", title="路径三态与数据迁移（R1）")


def _make_legacy(tmp_root: Path, files: dict[str, str]) -> Path:
    """构造一个含若干文件的 data 目录，返回其路径。"""
    data = tmp_root / "data"
    for rel, content in files.items():
        f = data / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(content, encoding="utf-8")
    return data


@suite.case("0. dev 态解析正确（当前即可验证）")
def _(paths):
    """开发态基线：本条不依赖 B16 新增接口，现在就能跑。"""
    get_app_root = suite.require(paths, "get_app_root")
    get_data_dir = suite.require(paths, "get_data_dir")
    root = get_app_root()
    assert (root / "utils" / "app_paths.py").exists(), f"dev 态 app_root 解析异常：{root}"
    assert get_data_dir() == root / "data", f"dev 态 data 目录解析异常：{get_data_dir()}"


@suite.case("1. dev / portable / installed 三态解析正确")
def _(paths):
    get_data_mode = suite.require(paths, "get_data_mode")
    get_data_dir = suite.require(paths, "get_data_dir")
    app_paths = paths

    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        exe_dir = tmp / "app"
        exe_dir.mkdir(parents=True)
        appdata = tmp / "appdata"
        appdata.mkdir(parents=True)

        # dev 态：未冻结
        with mock.patch.object(sys, "frozen", False, create=True):
            assert get_data_mode() == "dev", "未冻结应解析为 dev"
            assert get_data_dir() == Path(__file__).resolve().parents[0] / "data", \
                f"dev 态 data 解析异常：{get_data_dir()}"

        # portable 态：冻结 + exe 同级存在 data/
        legacy = _make_legacy(exe_dir, {"notices.db": "db", "chroma/x.txt": "vec"})
        with mock.patch.object(sys, "frozen", True, create=True), \
             mock.patch.object(sys, "executable", str(exe_dir / "App.exe")), \
             mock.patch.dict(os.environ, {"APPDATA": str(appdata)}, clear=False):
            assert get_data_mode() == "portable", "exe 同级有 data 应解析为 portable"
            assert get_data_dir() == exe_dir / "data", \
                f"portable 态 data 解析异常：{get_data_dir()}"

        # installed 态：冻结 + 无 exe 同级 data + APPDATA 指向临时目录
        (exe_dir / "data").rename(tmp / "removed_data")  # 移除 data，模拟新装
        with mock.patch.object(sys, "frozen", True, create=True), \
             mock.patch.object(sys, "executable", str(exe_dir / "App.exe")), \
             mock.patch.dict(os.environ, {"APPDATA": str(appdata)}, clear=False):
            assert get_data_mode() == "installed", "无 exe 同级 data 应解析为 installed"
            expected = appdata / "CampusNoticeAssistant" / "data"
            assert get_data_dir() == expected, \
                f"installed 态 data 解析异常：{get_data_dir()}"

        _ = app_paths  # 保留引用，避免 lint 告警


@suite.case("2. 老版本 exe 同级 data/ 仍可读取（向后兼容）")
def _(paths):
    resolve_legacy = suite.require(paths, "resolve_legacy_data_dir")
    get_data_mode = suite.require(paths, "get_data_mode")

    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        exe_dir = tmp / "app"
        exe_dir.mkdir(parents=True)
        appdata = tmp / "appdata"
        appdata.mkdir(parents=True)
        _make_legacy(exe_dir, {"notices.db": "db", "source_catalog.yaml": "y"})

        # frozen + exe 同级存在 data → 必须识别为 portable（不静默丢老数据）
        with mock.patch.object(sys, "frozen", True, create=True), \
             mock.patch.object(sys, "executable", str(exe_dir / "App.exe")), \
             mock.patch.dict(os.environ, {"APPDATA": str(appdata)}, clear=False):
            assert get_data_mode() == "portable", "老数据存在时必须向后兼容为 portable"
            legacy = resolve_legacy()
            assert legacy is not None and legacy.exists(), "应识别出老版本 data 目录"
            assert (legacy / "notices.db").read_text(encoding="utf-8") == "db", \
                "老数据文件必须可读（不静默丢失）"


@suite.case("3. 迁移幂等（重复执行结果一致）")
def _(paths):
    migrate = suite.require(paths, "migrate_data_dir")

    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        source = _make_legacy(tmp / "legacy", {
            "notices.db": "db-content",
            "chroma/idx.bin": "vec-bytes",
            "logs/app.log": "log",
        })
        target = tmp / "installed" / "CampusNoticeAssistant" / "data"

        r1 = migrate(source, target)
        assert r1 == "migrated", f"首次迁移应返回 migrated，实际 {r1}"

        # 校验条数一致
        src_files = [p for p in source.rglob("*") if p.is_file() and p.name != ".migrated"]
        dst_files = [p for p in target.rglob("*") if p.is_file() and p.name != ".migrated"]
        assert len(dst_files) == len(src_files), \
            f"迁移后条数不一致：源 {len(src_files)} 目标 {len(dst_files)}"
        for sf in src_files:
            rel = sf.relative_to(source)
            d = target / rel
            assert d.exists() and d.stat().st_size == sf.stat().st_size, f"目标缺失或不一致：{rel}"

        # 原数据保留
        assert (source / "notices.db").exists(), "迁移后源数据必须保留"
        assert (source / ".migrated").exists(), "迁移后应写 .migrated 标记"

        # 幂等：二次执行返回 already_migrated，目标不变
        r2 = migrate(source, target)
        assert r2 == "already_migrated", f"二次迁移应幂等，实际 {r2}"
        dst_files2 = [p for p in target.rglob("*") if p.is_file() and p.name != ".migrated"]
        assert len(dst_files2) == len(src_files), "二次迁移不得重复插入"


@suite.case("4. 迁移失败时原数据完整保留")
def _(paths):
    migrate = suite.require(paths, "migrate_data_dir")

    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        source = _make_legacy(tmp / "legacy", {
            "a.db": "aaaa",
            "b.db": "bbbb",
            "c.db": "cccc",
        })
        # 记录源快照（文件名 + 大小 + 内容）
        src_before = {
            p.relative_to(source).as_posix(): p.read_bytes()
            for p in source.rglob("*") if p.is_file()
        }
        target = tmp / "installed" / "data"

        # 注入「复制中失败」：第二个文件复制时抛 OSError
        real_copy2 = shutil.copy2
        calls = {"n": 0}

        def _failing_copy2(src, dst, **kw):
            calls["n"] += 1
            if calls["n"] >= 2:
                raise OSError("模拟磁盘写入失败")
            return real_copy2(src, dst, **kw)

        try:
            with mock.patch.object(shutil, "copy2", side_effect=_failing_copy2):
                migrate(source, target)
            raised = False
        except OSError:
            raised = True

        assert raised, "注入失败后 migrate 应抛 OSError"

        # 源目录文件数与校验和完全不变
        src_after = {
            p.relative_to(source).as_posix(): p.read_bytes()
            for p in source.rglob("*") if p.is_file()
        }
        assert src_after == src_before, "迁移失败后源数据必须完整保留（可回退继续用）"
        # 源未被标记为已迁移
        assert not (source / ".migrated").exists(), "迁移失败后不得写 .migrated 标记"


if __name__ == "__main__":
    sys.exit(suite.run())
