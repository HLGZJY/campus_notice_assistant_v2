# -*- mode: python ; coding: utf-8 -*-
"""校园通知智能助手 PyInstaller spec（onedir 模式）。

双版本（PACKAGING.md 关键决策）：
  - 云端版（默认）：CNA_FLAVOR=cloud —— 排除 torch / sentence_transformers /
    transformers，不带本地 embedding 模型，实测约 226MB（须用 packaging/venv-build
    干净环境构建，见 PACKAGING.md 体积瘦身一节）
  - 完整版：CNA_FLAVOR=full —— 全量依赖（含 torch），models/ 由构建脚本
    复制到应用根目录（不走 datas，避免落进 _internal）

产物布局（onedir，PyInstaller 6.x）：
  dist-<flavor>/CampusNoticeAssistant/
    CampusNoticeAssistant.exe      ← 启动器（console=True，日志是排障入口）
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
FLAVOR = os.environ.get("CNA_FLAVOR", "cloud")  # cloud | full

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
    # 前提：utils/embedding.py 的 HuggingFaceEmbeddings 是函数内延迟 import，
    # 云端路径不会执行到（PACKAGING.md 已核实，开始前复核清单项）。
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
    [str(PROJECT_ROOT / "run_app.py")],
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
    console=True,  # 本地 Web 应用保留控制台：出问题时日志是唯一排障入口
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
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
