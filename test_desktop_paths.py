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

依赖：utils.app_paths（当前已存在，但三态接口待 B16 补齐 → 逐 case SKIP）

用法：python test_desktop_paths.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from tools._desktop_testkit import Suite  # noqa: E402

suite = Suite(batch="B16", module="utils.app_paths", title="路径三态与数据迁移（R1）")


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
    resolve_mode = suite.require(paths, "get_data_mode")
    get_data_dir = suite.require(paths, "get_data_dir")
    suite.pending(
        "分别模拟：源码运行（dev）→ 仓库根/data；"
        "冻结且存在 exe 同级 data（portable）→ exe 同级 data；"
        "冻结且 installed 标记存在 → %APPDATA%/CampusNoticeAssistant/data"
    )


@suite.case("2. 老版本 exe 同级 data/ 仍可读取（向后兼容）")
def _(paths):
    legacy = suite.require(paths, "resolve_legacy_data_dir")
    suite.pending(
        "构造 installed 态但 exe 同级存在 data/ 的场景，"
        "断言 legacy 目录被识别且可被读取（不静默丢数据）"
    )


@suite.case("3. 迁移幂等（重复执行结果一致）")
def _(paths):
    migrate = suite.require(paths, "migrate_data_dir")
    suite.pending(
        "对同一份源数据连续执行两次 migrate_data_dir()，"
        "断言目标条数一致、无重复插入、返回值标记 already_migrated"
    )


@suite.case("4. 迁移失败时原数据完整保留")
def _(paths):
    migrate = suite.require(paths, "migrate_data_dir")
    suite.pending(
        "注入写入失败（权限/磁盘满），断言 migrate 抛错后源目录文件数与校验和不变，"
        "且应用仍可从旧目录启动"
    )


if __name__ == "__main__":
    sys.exit(suite.run())
