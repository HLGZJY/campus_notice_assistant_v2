"""B12 打包产物冒烟（端到端，默认 dry-run）。

对应 docs/DESKTOP-ACCEPTANCE.md §4「tools/smoke_desktop.py」断言链：
  1. 静默安装无需管理员
  2. 启动后无控制台 / 浏览器进程
  3. /api/v1/health 200
  4. 8 页面 API 200
  5. SSE 增量帧
  6. 关闭退出无残留
  7. 卸载后程序清、data 保留、自启项清

安全约定：
  - 默认 **dry-run**，只打印将要执行的步骤，绝不安装/卸载任何东西；
  - 只有显式加 --apply 才会真正执行（会安装并卸载被测安装包，请在虚拟机或确认无碍的机器上跑）。

依赖：B09 产出 desktop flavor 安装包、B12 填充本脚本步骤实现。

用法：
    python tools/smoke_desktop.py --setup packaging/out/校园通知助手-桌面版-setup.exe
    python tools/smoke_desktop.py --exe "C:/.../CampusNoticeAssistant/校园通知助手.exe" --apply
    python tools/smoke_desktop.py --setup <setup.exe> --apply --json-out docs-local/acceptance/smoke-b12.json

退出码：存在 FAIL → 1；全部 SKIP/PASS → 0。
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

BATCH = "B12"

# 8 个页面的前端路由（与前端 router 一致，用于步骤 4 的 API/页面可达性检查）
PAGES = [
    "/",                 # Dashboard 首页
    "/notices",          # 通知浏览
    "/todos",            # 待办中心
    "/qa",               # 智能问答
    "/subscriptions",    # 订阅管理
    "/config",           # 系统配置
    "/sources",          # 数据源中心
    "/token-usage",      # Token 用量
]


class Pending(NotImplementedError):
    """步骤尚未实现 → SKIP（不阻断回归）。"""


STEPS: list[tuple[str, object]] = []


def step(name: str):
    def deco(fn):
        STEPS.append((name, fn))
        return fn

    return deco


# ---------------------------------------------------------------------------
# 步骤实现（B12 填充；当前全部为待实现占位）
# ---------------------------------------------------------------------------
@step("1. 静默安装无需管理员")
def _s1(ctx):
    raise Pending(
        "待 B12 实现：subprocess 执行 '<setup.exe> /VERYSILENT /NORESTART /SUPPRESSMSGBOXES'，"
        "断言退出码 0 且未触发 UAC（per-user 安装，PrivilegesRequired=lowest）"
    )


@step("2. 启动后无控制台 / 浏览器进程")
def _s2(ctx):
    raise Pending(
        "待 B12 实现：启动 exe → tasklist 采样进程树，"
        "断言无 conhost 归属本进程、无 msedge/chrome/firefox 子进程"
    )


@step("3. /api/v1/health 200")
def _s3(ctx):
    raise Pending(
        "待 B12 实现：轮询 http://127.0.0.1:<sticky_port>/api/v1/health 至多 10s，"
        "断言 200 且记录首屏耗时（目标 ≤ 2s）"
    )


@step("4. 8 页面 API 200")
def _s4(ctx):
    raise Pending(f"待 B12 实现：逐个请求 {len(PAGES)} 个页面路由与关键 API，断言均 200")


@step("5. SSE 增量帧")
def _s5(ctx):
    raise Pending(
        "待 B12 实现：向问答 SSE 端点发一次请求，断言收到 ≥2 个增量帧且最终帧为完成态"
    )


@step("6. 关闭退出无残留")
def _s6(ctx):
    raise Pending(
        "待 B12 实现：触发关闭 → 断言进程退出、无子线程/子进程残留、"
        "data/runtime.json 端口写回正确"
    )


@step("7. 卸载后程序清、data 保留、自启项清")
def _s7(ctx):
    raise Pending(
        "待 B12 实现：执行卸载 → 断言程序目录清空、data/ 保留（uninsneveruninstall）、"
        "HKCU Run 自启项被清除"
    )


# ---------------------------------------------------------------------------
def main() -> int:
    ap = argparse.ArgumentParser(description="桌面版打包产物冒烟")
    ap.add_argument("--setup", type=Path, help="安装包 setup.exe 路径（走安装→冒烟→卸载全流程）")
    ap.add_argument("--exe", type=Path, help="已安装的 exe 路径（跳过安装与卸载）")
    ap.add_argument("--apply", action="store_true", help="真正执行；默认 dry-run")
    ap.add_argument("--json-out", type=Path, help="结果 JSON 输出路径")
    args = ap.parse_args()

    if not args.setup and not args.exe:
        ap.error("需提供 --setup 或 --exe 之一")

    ctx = {
        "setup": args.setup,
        "exe": args.exe,
        "apply": args.apply,
        "root": ROOT,
    }

    print(f"== {BATCH} 打包产物冒烟 ==")
    print(f"   目标：{args.setup or args.exe}")
    print(f"   模式：{'APPLY（会真实安装/卸载）' if args.apply else 'DRY-RUN（不执行任何副作用）'}")
    print(f"   页面：{len(PAGES)} 个")

    failures: list[str] = []
    skipped = 0
    detail: list[dict] = []

    for name, fn in STEPS:
        t0 = time.time()
        try:
            fn(ctx)
            status, note = "PASS", ""
        except Pending as exc:
            status, note, skipped = "SKIP", str(exc), skipped + 1
        except AssertionError as exc:
            status, note, _ = "FAIL", str(exc), failures.append(name)
        except Exception as exc:  # noqa: BLE001
            status, note, _ = "ERROR", f"{type(exc).__name__}: {exc}", failures.append(name)
        cost = time.time() - t0
        print(f"  [{status:>5}] {name}" + (f"  ({note})" if note else ""))
        detail.append({"step": name, "status": status, "note": note, "duration": round(cost, 2)})

    if failures:
        summary = f"结果: {len(failures)} 项失败 -> {failures}"
        exit_code = 1
    elif skipped == len(STEPS):
        summary = f"结果: 全部跳过（{BATCH} 步骤待实现）"
        exit_code = 0
    elif skipped:
        summary = f"结果: 全部通过（{skipped} 项跳过）"
        exit_code = 0
    else:
        summary = "结果: 全部通过"
        exit_code = 0
    print(summary)

    if args.json_out:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(
            json.dumps(
                {
                    "generated_at": datetime.now().isoformat(timespec="seconds"),
                    "batch": BATCH,
                    "target": str(args.setup or args.exe),
                    "apply": args.apply,
                    "summary": summary,
                    "steps": detail,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        print(f"结果 JSON：{args.json_out}")

    return exit_code


if __name__ == "__main__":
    sys.exit(main())
