"""模块 2.5：向量一致性校验脚本。

对比 Chroma 与 SQLite 的通知 ID 集合，检测残留（幽灵）向量并可选一键清理。

幽灵向量定义：notice_id 在 Chroma 中存在，但已不存在于 SQLite（通知被删除）。
判定基准取 SQLite 全量通知 ID 而非"可索引"子集——通知处于 raw/failed 等状态时
其向量仍属有效内容，不应被当作残留误删。

用法：
    python check_vector_consistency.py            # 只读检查，无残留时输出"一致"
    python check_vector_consistency.py --fix      # 自动清理幽灵向量
    python check_vector_consistency.py --json     # 机器可读输出
    python check_vector_consistency.py --persist-dir data/chroma

退出码：
    0 = 无残留向量（一致）
    1 = 存在残留向量（未清理）或读取失败
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# 确保包能正确导入
sys.path.insert(0, str(Path(__file__).parent))

# Windows 控制台默认 cp1252 无法打印中文，统一用 UTF-8
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from storage.vectorstore import check_consistency  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(
        description="向量一致性校验（Chroma vs SQLite 通知 ID 集合）"
    )
    parser.add_argument(
        "--fix",
        action="store_true",
        help="自动清理幽灵向量（默认只报告）",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="以 JSON 输出结果",
    )
    parser.add_argument(
        "--persist-dir",
        type=Path,
        default=None,
        help="Chroma 持久化目录（默认 data/chroma）",
    )
    args = parser.parse_args()

    result = check_consistency(persist_dir=args.persist_dir, fix_ghosts=args.fix)

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        ghosts_found = result.get("ghosts_found") or []
        missing = result.get("missing") or []
        print("=" * 60)
        print("向量一致性校验（Chroma vs SQLite）")
        print("=" * 60)
        print(f"SQLite 通知数 : {result.get('sqlite_notices', '-')}")
        print(f"Chroma 通知数 : {result.get('chroma_notices', '-')}")
        print(f"残留向量      : {len(ghosts_found)}  {ghosts_found if ghosts_found else ''}")
        print(f"缺失向量      : {len(missing)}  {missing[:20] if missing else ''}")
        if result.get("ghosts_removed"):
            print(f"已清理        : {result['ghosts_removed']} chunks")
        print("-" * 60)
        if result.get("error"):
            print(f"!! 读取向量库失败: {result['error']}")
        elif result.get("consistent"):
            if ghosts_found:
                print("✅ 已清理残留，向量一致")
            else:
                print("✅ 向量一致（无残留）")
        else:
            print("❌ 存在残留向量，请运行 `python check_vector_consistency.py --fix` 清理")
        print("=" * 60)

    if result.get("error"):
        return 1
    if not result.get("consistent"):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
