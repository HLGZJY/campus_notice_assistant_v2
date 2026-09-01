#!/usr/bin/env python3
"""批次 prompt 生成器 —— 让每个批次的执行变成「粘贴 → 干活 → 自动生成下一个」。

设计目标
--------
用户每轮只需要打开 `docs-local/acceptance/当前批次-prompt.md`，复制其中
`---` 之间的内容粘给新会话。会话跑完批次后，按 prompt 里的收尾序列执行
本脚本，看板即被更新、验收记录被归档、下一批 prompt 自动生成。

三个子命令
----------
  next                       生成下一个待办批次的启动 prompt（默认）
  status <批次> <状态>       更新计划文档里的批次看板行
  archive <批次>             从模板生成该批次的验收记录
  list                       打印看板进度

进度判定
--------
以 `docs/DESKTOP-BATCH-PLAN.md` 第 6 章看板表的「状态」列为唯一事实源：
第一个状态为「待开始」且未被标记「不排期」的批次，即为下一批。

用法示例
--------
  python tools/batch_prompt.py --list
  python tools/batch_prompt.py                       # 生成下一批 prompt
  python tools/batch_prompt.py --batch B07           # 强制生成指定批次
  python tools/batch_prompt.py --status B05 已通过 --base a1b2c3d --end 9f8e7d6 --conclusion "Gate 全绿"
  python tools/batch_prompt.py --archive B05 --base a1b2c3d --end 9f8e7d6

  # 等价的 positional 写法（--xxx 别名与 positional 均可用）：
  python tools/batch_prompt.py status B05 已通过 --base a1b2c3d --end 9f8e7d6
  python tools/batch_prompt.py archive B05
  python tools/batch_prompt.py list
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

# Windows 控制台可能是 GBK，统一按 UTF-8 输出，避免中文刷屏报错
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
PLAN = ROOT / "docs" / "DESKTOP-BATCH-PLAN.md"
ACC_DIR = ROOT / "docs-local" / "acceptance"
PROMPT_DIR = ACC_DIR / "prompts"
CURRENT_PROMPT = ACC_DIR / "当前批次-prompt.md"
TEMPLATE = ACC_DIR / "_模板-批次验收记录.md"
BASELINE = ACC_DIR / "regression-baseline.json"

# 看板表格列索引（split("|") 后，首尾各有一个空串）
COL_BATCH, COL_NAME, COL_STATUS, COL_BASE, COL_END, COL_UNIT, COL_CONCL, COL_RECORD = range(1, 9)

VALID_STATUS = ("待开始", "进行中", "待验收", "已通过", "阻塞")
DONE_STATUS = ("已通过",)
SKIP_STATUS = ("不排期",)


# --------------------------------------------------------------------------
# 基础工具
# --------------------------------------------------------------------------
def die(msg: str, code: int = 1) -> "NoReturn":  # type: ignore[valid-type]
    print(f"[错误] {msg}")
    raise SystemExit(code)


def clean(cell: str) -> str:
    """去掉 Markdown 表格单元格里的加粗与删除线标记。"""
    return cell.strip().replace("~~", "").replace("**", "").strip()


def run_git(*args: str) -> str:
    """执行 git 命令，失败返回空串而不是抛异常。"""
    try:
        r = subprocess.run(
            ["git", *args], cwd=ROOT, capture_output=True, text=True, encoding="utf-8"
        )
        return r.stdout.strip() if r.returncode == 0 else ""
    except (OSError, subprocess.SubprocessError):
        return ""


# --------------------------------------------------------------------------
# 看板解析
# --------------------------------------------------------------------------
def read_plan() -> str:
    if not PLAN.exists():
        die(f"找不到计划文档：{PLAN}")
    return PLAN.read_text(encoding="utf-8")


def parse_board(plan_text: str) -> list[dict]:
    """解析第 6 章看板表，返回批次行列表。"""
    rows: list[dict] = []
    in_board = False
    for line in plan_text.splitlines():
        if line.startswith("## 6."):
            in_board = True
            continue
        if in_board and line.startswith("## "):
            break
        if not in_board or not line.strip().startswith("|"):
            continue

        cells = line.split("|")
        if len(cells) < 9:
            continue
        bid = clean(cells[COL_BATCH])
        if not re.fullmatch(r"B\d{2}", bid):
            continue  # 表头或分隔行

        rows.append(
            {
                "id": bid,
                "name": clean(cells[COL_NAME]),
                "status": clean(cells[COL_STATUS]),
                "base": clean(cells[COL_BASE]),
                "end": clean(cells[COL_END]),
                "unit": clean(cells[COL_UNIT]),
                "conclusion": clean(cells[COL_CONCL]),
                "record": clean(cells[COL_RECORD]),
                "line": line,
            }
        )
    return rows


def pick_next(rows: list[dict]) -> dict | None:
    """第一个既未完成、又不排期的批次。"""
    for r in rows:
        if r["status"] in SKIP_STATUS or r["status"] in DONE_STATUS:
            continue
        if r["status"] == "待开始":
            return r
    # 没有「待开始」但有「进行中/待验收/阻塞」的，取第一个提醒用户
    for r in rows:
        if r["status"] not in SKIP_STATUS and r["status"] not in DONE_STATUS:
            return r
    return None


# --------------------------------------------------------------------------
# 批次章节提取
# --------------------------------------------------------------------------
def extract_section(plan_text: str, bid: str) -> str:
    """取出 `### B05 ...` 整节，终止于下一个 `### B` 或任意 `## `。

    返回内容不含标题行本身（标题由调用方另行渲染，避免重复）。
    """
    lines = plan_text.splitlines()
    start = None
    for i, line in enumerate(lines):
        if re.match(rf"^###\s+{re.escape(bid)}\b", line):
            start = i
            break
    if start is None:
        return f"（未能在计划文档中找到 `{bid}` 章节，请直接打开 docs/DESKTOP-BATCH-PLAN.md 查阅）"

    out = []
    for line in lines[start + 1 :]:
        if re.match(r"^###\s+B\d{2}\b", line) or line.startswith("## "):
            break
        out.append(line)

    # 去掉尾部空行与分隔线
    while out and (not out[-1].strip() or out[-1].strip() in ("---",)):
        out.pop()
    return "\n".join(out).replace("<PY>", "python")


def section_heading(plan_text: str, bid: str) -> str:
    m = re.search(rf"^###\s+{re.escape(bid)}[^\n]*", plan_text, re.M)
    return m.group(0).lstrip("# ").strip() if m else bid


# --------------------------------------------------------------------------
# 环境快照
# --------------------------------------------------------------------------
def env_snapshot() -> list[str]:
    branch = run_git("rev-parse", "--abbrev-ref", "HEAD") or "(无法获取)"
    head = run_git("rev-parse", "--short", "HEAD") or "(无法获取)"
    status = run_git("status", "--porcelain")
    dirty = "干净" if not status.strip() else f"有 {len(status.strip().splitlines())} 个未提交改动"
    baseline = "已存在" if BASELINE.exists() else "**尚未建立**"

    return [
        f"- 当前分支：`{branch}`",
        f"- 当前 HEAD（可直接作为本批 base commit）：`{head}`",
        f"- 工作区：{dirty}",
        f"- 回归基线 `{BASELINE.relative_to(ROOT).as_posix()}`：{baseline}",
    ]


# --------------------------------------------------------------------------
# prompt 组装
# --------------------------------------------------------------------------
def build_prompt(bid: str, section: str, heading: str) -> str:
    snap = "\n".join(env_snapshot())

    if not BASELINE.exists():
        baseline_warn = (
            "> **⚠ 回归基线尚未建立**。本批若涉及回归比对，先跑一次\n"
            "> `python tools/run_regression.py --save-baseline docs-local/acceptance/regression-baseline.json`；\n"
            "> 若本批确实不含回归动作，可跳过，但需在验收记录里注明。\n"
        )
    else:
        baseline_warn = ""

    return f"""# {bid} · 启动 prompt（复制下方 `---` 之间的全部内容）

> 生成时间：{datetime.now().strftime("%Y-%m-%d %H:%M")}
> 由 `tools/batch_prompt.py` 自动生成，请勿手工编辑本文件——需要调整请改脚本或计划文档。

---

你正在接手 **campus_notice_assistant_v2** 项目的 v0.2.0 桌面版改造。动手前先读这三份文档（它们是唯一权威依据，不要凭常识猜）：

1. `docs/DESKTOP-UPGRADE.md` —— 升级方案：技术选型（pywebview + pystray）、K1~K9 避坑要点、R1~R14 风险清单
2. `docs/DESKTOP-BATCH-PLAN.md` —— 分批计划：B00~B22，每批含目标 / 原子任务 / 提交序列 / Gate / 回滚点 / 风险
3. `docs/DESKTOP-ACCEPTANCE.md` —— 验收手册：三层 Gate、缺陷分级、L1~L6 命令、51 条 check-list、脚本骨架说明

**项目路径**：`{ROOT}`
**版本**：当前 `0.1.0`，目标 `v0.2.0` 桌面版（只做 Windows x64）

**已拍板的决策（不要重新讨论）**：
- **只做 Windows x64**，macOS 不做（B20 已移出计划）
- **B16 数据目录三态与迁移保留在 v0.2.0**（无存量用户也要做）
- 技术路线 pywebview + pystray + 自研胶水层，单进程内嵌 uvicorn，**后端 68 个端点零改动**
- 最高风险 **R1：关控制台后 sys.stdout 为 None 导致启动即退**——B05 日志落盘是 B09 的硬前置，B09 必须先 `console=True` 跑通再切 `console=False`

## 本批任务：{heading}

{section}

## 开工前环境快照

{snap}

{baseline_warn}
## 工作方式（务必遵守）

1. **只推进 {bid}，不要顺手改其他批次的东西**。跨批改动会破坏「按批次回滚」的能力。
2. 开工即把上面快照里的 HEAD 记为 **base commit**（若工作区不干净，先确认那些改动是否属于其他工作，不要误提交）。
3. 批内按 `B{{nn}}.T{{k}}` **逐个原子任务提交**，不要攒成一个大提交；提交信息用**中文 + type 前缀**：
   `feat|fix|refactor|perf|test|docs|chore|ci(desktop|web|api|scheduler|db|packaging|paths): 简述`
4. 每批结束前**必须跑该批 Gate**（自动项 + 该批 D-xx 人工项），不通过就地修，**不接受「部分通过」**；修完 P0/P1 要重跑全批 Gate。
5. **任何既有测试失败 = 本批不通过**，无论是否本批改动引起——先回滚验证，再定位责任。
6. 需要新增依赖时先检查 `requirements*.txt`；桌面依赖走 `packaging/venv-build` 专用环境。

## 验证命令（工作目录为项目根）

```bash
# 桌面新增用例（骨架阶段预期：1 通过 / 6 跳过 / 0 失败，退出码 0）
python tools/run_regression.py --pattern "test_desktop_*.py"

# 全量回归 + 与基线比对（B08/B12/B17/B22 各跑一轮）
python tools/run_regression.py --baseline docs-local/acceptance/regression-baseline.json

# 首次建基线（只需一次，需有 API key / 网络）
python tools/run_regression.py --save-baseline docs-local/acceptance/regression-baseline.json

# 前端契约
cd frontend && npm run typecheck && npm run lint && npm run gen:api && npm run build

# 打包冒烟（默认 dry-run，加 --apply 才真装真卸）
python tools/smoke_desktop.py --setup <setup.exe 路径>
```

**骨架机制**：7 个 `test_desktop_*.py` 已是可运行骨架。模块一实现，对应 case 自动从 `[SKIP]` 变为真断言；未实现前不影响回归（跳过即退出码 0）。`test_desktop_wal.py` 的断言跑在临时库上，**绝不触碰生产库 `data/notices.db`**。

## 收尾（Gate 通过后自动执行，不用问我）

> 只 commit 不 push；push 前必须先征得用户同意。

```bash
# 1. 取结束 commit
END=$(git rev-parse --short HEAD)

# 2. 更新看板（<BASE> 换成开工前的 base commit）
python tools/batch_prompt.py --status {bid} 已通过 --base <BASE> --end "$END" --conclusion "Gate 全绿（一句话结论）"

# 3. 归档验收记录（脚本从模板生成）
python tools/batch_prompt.py --archive {bid} --base <BASE> --end "$END"

# 4. 按实跑结果填写验收记录的第 3/4/5/6 节（自动验证结果 / 人工 check-list / 缺陷 / 结论）

# 5. 提交看板更新（精确添加，绝不 git add -A——工作区可能有不属于本批的无关改动）
git add docs/DESKTOP-BATCH-PLAN.md
git status --short   # 若仍有本批相关的未提交文件，先补提交；无关改动（如 docs/USAGE.md）保持原样不要动
git commit -m "docs(desktop): {bid} 验收通过，更新批次看板"

# 6. 生成下一批 prompt
python tools/batch_prompt.py --next
```

**Gate 未通过时**：不要标记「已通过」，改为 `--status {bid} 阻塞`，在验收记录第 5 节登记缺陷，修完重跑全批 Gate。P0/P1 **不得跨批遗留**。

## 完成后汇报（3~5 行）

说明：本批改了哪些文件 / Gate 自动项与人工项结果 / 有无 P0/P1 缺陷 / 下一批是哪一个、prompt 已生成在哪。
"""


# --------------------------------------------------------------------------
# 子命令
# --------------------------------------------------------------------------
def cmd_next(batch: str | None = None) -> int:
    plan_text = read_plan()
    rows = parse_board(plan_text)
    if not rows:
        die("未能从计划文档解析出批次看板，请检查第 6 章表格格式")

    if batch:
        target = next((r for r in rows if r["id"].upper() == batch.upper()), None)
        if not target:
            die(f"看板中不存在批次 {batch}")
    else:
        target = pick_next(rows)
        if not target:
            print("看板中所有批次均已完成或不排期，没有下一个批次了。")
            return 0

    bid = target["id"]
    section = extract_section(plan_text, bid)
    heading = section_heading(plan_text, bid)
    content = build_prompt(bid, section, heading)

    PROMPT_DIR.mkdir(parents=True, exist_ok=True)
    archived = PROMPT_DIR / f"{bid}-启动prompt.md"
    archived.write_text(content, encoding="utf-8")
    CURRENT_PROMPT.write_text(content, encoding="utf-8")

    print("=" * 78)
    print(f"已生成批次 prompt：{bid} · {target['name']}")
    print("=" * 78)
    print(f"  固定入口（每次都打开这个）：{CURRENT_PROMPT}")
    print(f"  归档副本：                {archived}")
    print(f"  当前状态：{target['status']}")
    if target["status"] != "待开始":
        print(f"  ⚠ 该批次状态为「{target['status']}」，确认是否要重做或续做")
    print("-" * 78)
    remain = [r["id"] for r in rows if r["status"] not in DONE_STATUS and r["status"] not in SKIP_STATUS]
    print(f"  剩余待办（{len(remain)}）：{' '.join(remain)}")
    print("=" * 78)
    return 0


def cmd_status(bid: str, status: str, base: str | None, end: str | None, conclusion: str | None) -> int:
    if status not in VALID_STATUS:
        die(f"状态必须是其中之一：{' / '.join(VALID_STATUS)}")

    plan_text = read_plan()
    rows = parse_board(plan_text)
    target = next((r for r in rows if r["id"].upper() == bid.upper()), None)
    if not target:
        die(f"看板中不存在批次 {bid}")

    cells = target["line"].split("|")
    if len(cells) < 9:
        die("看板行格式异常，列数不足")

    cells[COL_STATUS] = f" {status} "
    if base:
        cells[COL_BASE] = f" {base} "
    if end:
        cells[COL_END] = f" {end} "
    if conclusion:
        cells[COL_CONCL] = f" {conclusion} "
    new_line = "|".join(cells)

    updated = plan_text.replace(target["line"], new_line, 1)
    if updated == plan_text:
        die("看板行替换失败（原文未匹配），计划文档可能已被手工改过")

    PLAN.write_text(updated, encoding="utf-8")
    print(f"[OK] 看板已更新：{bid} → {status}")
    if base:
        print(f"     base commit : {base}")
    if end:
        print(f"     结束 commit : {end}")
    if conclusion:
        print(f"     验收结论    : {conclusion}")
    return 0


def cmd_archive(bid: str, base: str | None, end: str | None) -> int:
    if not TEMPLATE.exists():
        die(f"找不到验收记录模板：{TEMPLATE}")

    plan_text = read_plan()
    rows = parse_board(plan_text)
    target = next((r for r in rows if r["id"].upper() == bid.upper()), None)
    name = target["name"] if target else ""

    ACC_DIR.mkdir(parents=True, exist_ok=True)
    # 已存在同名归档时不覆盖，避免冲掉填写过的内容
    existing = sorted(ACC_DIR.glob(f"{bid}-*验收记录.md"))
    if existing:
        print(f"[跳过] 已存在验收记录，未覆盖：{existing[0].name}")
        return 0

    # 批次名可能含路径非法字符（如 B14「调度 pause/resume 与空闲触发」的 `/`），
    # 直接拼进文件名会抛 FileNotFoundError。统一替换为安全字符 `-`。
    safe_name = "".join(
        ch if ch not in '<>:"/\\|?*' else "-" for ch in name
    ).replace(" ", "")
    out = ACC_DIR / f"{bid}-{safe_name}-验收记录.md"
    text = TEMPLATE.read_text(encoding="utf-8")
    text = text.replace("{批次名}", name).replace("{xx}", bid[1:]).replace("B{xx}", bid)
    text = text.replace("- 日期：YYYY-MM-DD", f"- 日期：{datetime.now().strftime('%Y-%m-%d')}")
    if base or end:
        text = text.replace(
            "- base commit：{hash}  ／  结束 commit：{hash}",
            f"- base commit：{base or '(待填)'}  ／  结束 commit：{end or '(待填)'}",
        )
    out.write_text(text, encoding="utf-8")

    # 回写看板「记录」列
    plan_text2 = read_plan()
    rows2 = parse_board(plan_text2)
    t2 = next((r for r in rows2 if r["id"].upper() == bid.upper()), None)
    if t2:
        cells = t2["line"].split("|")
        cells[COL_RECORD] = f" {out.name} "
        PLAN.write_text(plan_text2.replace(t2["line"], "|".join(cells), 1), encoding="utf-8")

    print(f"[OK] 验收记录已生成：{out}")
    print("     接下来请填写其中的 3（自动验证结果）/ 4（人工 check-list）/ 5（缺陷）/ 6（结论）四节")
    return 0


def cmd_list() -> int:
    plan_text = read_plan()
    rows = parse_board(plan_text)
    if not rows:
        die("未能解析出批次看板")

    print("=" * 78)
    print("批次看板进度")
    print("=" * 78)
    print(f"{'批次':<6}{'名称':<26}{'状态':<8}{'单元':<6}base → end")
    print("-" * 78)
    for r in rows:
        span = f"{r['base']} → {r['end']}" if r["base"] != "—" else "—"
        print(f"{r['id']:<6}{r['name']:<26}{r['status']:<8}{r['unit']:<6}{span}")
    print("-" * 78)
    done = [r["id"] for r in rows if r["status"] in DONE_STATUS]
    skip = [r["id"] for r in rows if r["status"] in SKIP_STATUS]
    remain = [r["id"] for r in rows if r["status"] not in DONE_STATUS and r["status"] not in SKIP_STATUS]
    print(f"已通过 {len(done)} ／ 不排期 {len(skip)} ／ 剩余 {len(remain)}")
    nxt = pick_next(rows)
    if nxt:
        print(f"下一批：{nxt['id']} · {nxt['name']}（{nxt['status']}）")
    print("=" * 78)
    return 0


# --------------------------------------------------------------------------
def main() -> int:
    parser = argparse.ArgumentParser(
        description="批次 prompt 生成器：next / status / archive / list（--xxx 别名与 positional 均可用）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("command", nargs="?", default=None,
                        choices=["next", "status", "archive", "list"],
                        help="子命令（positional 写法）")
    parser.add_argument("batch", nargs="?", help="批次号，如 B05（status/archive 时使用）")
    parser.add_argument("value", nargs="?", help="status 子命令的状态：待开始/进行中/待验收/已通过/阻塞")
    parser.add_argument("--batch", dest="batch_opt", help="指定生成哪个批次的 prompt（next 用）")
    parser.add_argument("--next", action="store_true", help="别名：等价于子命令 next")
    parser.add_argument("--list", action="store_true", help="别名：等价于子命令 list")
    parser.add_argument("--status", nargs=2, metavar=("BATCH", "STATUS"),
                        help="别名：等价于 status <BATCH> <STATUS>")
    parser.add_argument("--archive", nargs=1, metavar="BATCH",
                        help="别名：等价于 archive <BATCH>")
    parser.add_argument("--base", help="base commit")
    parser.add_argument("--end", help="结束 commit")
    parser.add_argument("--conclusion", help="验收结论（一句话）")

    args = parser.parse_args()

    # ---- 选项别名优先（prompt 收尾序列使用这种写法）----
    if args.list:
        return cmd_list()
    if args.next:
        return cmd_next(args.batch_opt)
    if args.status:
        bid, status = args.status
        return cmd_status(bid, status, args.base, args.end, args.conclusion)
    if args.archive:
        return cmd_archive(args.archive[0], args.base, args.end)

    # ---- positional 写法 ----
    if args.command == "next":
        return cmd_next(args.batch_opt)
    if args.command == "list":
        return cmd_list()
    if args.command == "status":
        if not args.batch:
            die('status 需要批次号与状态，例如：status B05 已通过  或  --status B05 已通过')
        if not args.value:
            die(f"status 需要显式给出状态（{' / '.join(VALID_STATUS)}），不接受默认值——避免误标「已通过」")
        return cmd_status(args.batch, args.value, args.base, args.end, args.conclusion)
    if args.command == "archive":
        if not args.batch:
            die("archive 需要指定批次号，例如：archive B05")
        return cmd_archive(args.batch, args.base, args.end)
    if args.command is None:
        return cmd_next(args.batch_opt)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
