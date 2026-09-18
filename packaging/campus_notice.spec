# -*- mode: python ; coding: utf-8 -*-
"""校园通知智能助手 PyInstaller spec（onedir 模式）。

三版本（PACKAGING.md / DESKTOP-UPGRADE.md §4.2）：
  - 云端版（默认）：CNA_FLAVOR=cloud —— 排除 torch / sentence_transformers /
    transformers，不带本地 embedding 模型，实测约 226MB（须用 packaging/venv-build
    干净环境构建，见 PACKAGING.md 体积瘦身一节）
  - 完整版：CNA_FLAVOR=full —— 全量依赖（含 torch），models/ 由构建脚本
    复制到应用根目录（不走 datas，避免落进 _internal）
  - 桌面版：CNA_FLAVOR=desktop —— 复用云端瘦身依赖集，入口切到 desktop_main.py，
    且 `console=False`（无控制台窗口，R1 的最终验证点，见 B09）。

入口与 console 由 flavor 决定：
  - cloud / full → run_app.py + console=True（在线版，控制台日志是排障入口）
  - desktop     → desktop_main.py + console=False（桌面版，无终端窗口）
  调试开关：desktop flavor 下设置环境变量 CNA_DESKTOP_CONSOLE=True 可临时
  以 console=True 构建（B09.T3「先跑通再切 False」的中间验证，不作为交付形态）。

产物布局（onedir，PyInstaller 6.x）：
  dist-<flavor>/CampusNoticeAssistant/
    CampusNoticeAssistant.exe      ← 启动器（console 值见上；桌面版日志走 data/logs/app.log）
    _internal/                      ← Python 解释器 + 依赖（PyInstaller 自动）
    config/  frontend/dist/  VERSION  .env.example  [models/]
                                    ← 构建脚本（build.py）负责复制到应用根，
                                      与 utils/app_paths.py 的冻结路径解析对齐

用法（由 build.py 调用，也可手动）：
  set CNA_FLAVOR=cloud && pyinstaller packaging/campus_notice.spec \
      --distpath packaging/dist-cloud --workpath packaging/build-cloud --noconfirm
"""

import os
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

PROJECT_ROOT = Path(SPECPATH).parent  # spec 位于 packaging/，上一级即项目根
FLAVOR = os.environ.get("CNA_FLAVOR", "cloud")  # cloud | full | desktop

# 桌面版（B09.T1）：入口脚本 + console 值随 flavor 切换。
#   - desktop：desktop_main.py，console=False（无终端窗口；可被 CNA_DESKTOP_CONSOLE=True 覆盖）
#   - cloud/full：run_app.py，console=True（在线版保留控制台排障入口）
if FLAVOR == "desktop":
    ENTRY_SCRIPT = str(PROJECT_ROOT / "desktop_main.py")
    _console_override = os.environ.get("CNA_DESKTOP_CONSOLE", "").strip().lower()
    CONSOLE = _console_override == "true"  # 调试开关，非交付形态
else:
    ENTRY_SCRIPT = str(PROJECT_ROOT / "run_app.py")
    CONSOLE = True

hiddenimports = [
    # uvicorn.run("api.main:app") 的字符串导入 + uvicorn 按名字拼装的内部模块，
    # 静态分析一律看不见，必须显式声明
    "api.main",
    "uvicorn.logging",
    "uvicorn.loops.auto",
    "uvicorn.loops.asyncio",
    "uvicorn.protocols.http.auto",
    "uvicorn.protocols.http.h11_impl",
    "uvicorn.protocols.http.httptools_impl",
    "uvicorn.protocols.websockets.auto",
    "uvicorn.protocols.websockets.wsproto_impl",
    "uvicorn.lifespan.on",
    "uvicorn.lifespan.off",
]
excludes: list[str] = []
datas = [
    # 运行时按相对路径读数据文件的包（词典 / 语料 / 语言包）
    *collect_data_files("jieba"),
    *collect_data_files("newspaper"),
    *collect_data_files("dateparser"),
    # openai-agents SDK（包名 agents）的 sandbox.memory.prompts 在 import 时读
    # 包内 .md 提示词文件；不收集会炸掉 agents.run 整条 import 链
    # （症状：路由注册跳过 / TaskManager / 调度器启动失败 / 健康检查 degraded）
    *collect_data_files("agents"),
    # 品牌图标：托盘运行时加载（sys._MEIPASS/app.ico）；exe 图标见 EXE(icon=)
    (str(Path(SPECPATH) / "app.ico"), "."),
]

# chromadb 用 importlib 字符串加载 segment 实现（运行期拼模块名），
# 静态分析收不全 → 收全子模块
hiddenimports += collect_submodules("chromadb")

if FLAVOR == "full":
    # torch / transformers 由 hooks-contrib 官方 hook 收集；models/ 由 build.py
    # 复制到应用根目录（embedding 相对路径 models/... 按应用根解析）
    pass
else:
    # 云端瘦身版：embedding 只走 OpenAI-compatible API。
    # 前提：utils/embedding.py 的 HuggingFaceEmbeddings 仅在本地分支内延迟 import，
    # 且本地依赖缺失时抛带指引的 RuntimeError（在线路径永不触发该 import）。
    excludes += [
        "torch",
        "torchvision",
        "sentence_transformers",
        "transformers",
        "diffusers",
        "tokenizers",
        "safetensors",
    ]

a = Analysis(
    [ENTRY_SCRIPT],
    pathex=[str(PROJECT_ROOT)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="CampusNoticeAssistant",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=CONSOLE,  # 由 flavor 决定（见文件头）：desktop=False / cloud·full=True
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(Path(SPECPATH) / "app.ico"),  # exe/快捷方式/窗口标题栏共用品牌图标
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="CampusNoticeAssistant",
)
