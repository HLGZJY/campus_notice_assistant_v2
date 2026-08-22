"""一键构建脚本（PACKAGING.md 实施步骤 1-4 的自动化）。

用法（⚠️ 请用专用构建环境 packaging/venv-build，避免无关大件被打进产物）：

    packaging/venv-build/Scripts/python packaging/build.py --flavor cloud   # 云端瘦身版（默认）
    packaging/venv-build/Scripts/python packaging/build.py --flavor full    # 完整版（含 torch + models/）
    追加 --innosetup 构建后编译安装包 setup.exe

构建环境首次搭建：
    python -m venv packaging/venv-build
    packaging/venv-build/Scripts/pip install -r packaging/requirements-build-cloud.txt
（完整版额外需要 sentence-transformers，建议用项目 .venv 跑 --flavor full）

流程：
  1. 读 VERSION（唯一版本号来源）
  2. 前端构建 npm run build（--skip-frontend 可跳过）
  3. PyInstaller 按 spec 打包（onedir）
  4. 布置应用根目录（config YAML / frontend dist / VERSION / .env.example / models）
     —— 与 utils/app_paths.py 的冻结路径解析严格对齐
  5. 云端版改写 dist 内 app.yaml：embedding 指向云端 API
  6. 生成 version.ini（Inno Setup 脚本读取版本号用）
  7. --innosetup 时调用 ISCC.exe 编译安装包并输出 sha256

发布 SOP（以后每次更新）：
    改 VERSION → python packaging/build.py --flavor cloud --innosetup
    → gh release create v<VERSION> out/校园通知助手-云端版-setup.exe --notes "更新日志"
"""
from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import subprocess
import sys
from pathlib import Path

import yaml

PACKAGING_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = PACKAGING_DIR.parent

CLOUD_EMBEDDING_DEFAULT_PROVIDER = "bailian"
CLOUD_EMBEDDING_DEFAULT_MODEL = "text-embedding-v4"


def log(msg: str) -> None:
    print(f"[build] {msg}", flush=True)


def run(cmd: list[str], cwd: Path | None = None) -> None:
    log(f"$ {' '.join(cmd)}" + (f"  (cwd={cwd})" if cwd else ""))
    proc = subprocess.run(cmd, cwd=str(cwd) if cwd else None)
    if proc.returncode != 0:
        raise SystemExit(f"[build] 命令失败（exit={proc.returncode}）：{' '.join(cmd)}")


def build_frontend() -> None:
    dist = PROJECT_ROOT / "frontend" / "dist"
    if dist.exists() and dist.stat().st_mtime > (PROJECT_ROOT / "frontend" / "package.json").stat().st_mtime:
        log("frontend/dist 已是最新（比 package.json 新），跳过构建（--force-frontend 可强制）")
        return
    npm = shutil.which("npm")
    if not npm:
        raise SystemExit("[build] 未找到 npm，请先安装 Node.js 或用 --skip-frontend 跳过")
    if not (PROJECT_ROOT / "frontend" / "node_modules").exists():
        run(["npm", "ci"], cwd=PROJECT_ROOT / "frontend")
    run(["npm", "run", "build"], cwd=PROJECT_ROOT / "frontend")


def patch_cloud_embedding(app_yaml: Path, provider: str, model: str) -> None:
    """云端版改写 dist 内 app.yaml：embedding 从本地模型切到云端 API。

    只改构建产物，不动仓库里的 config/app.yaml。
    """
    data = yaml.safe_load(app_yaml.read_text(encoding="utf-8"))
    providers = data.get("providers", {})
    if provider not in providers:
        raise SystemExit(
            f"[build] app.yaml 中不存在供应商 '{provider}'，"
            f"请用 --cloud-embedding-provider 指定已有供应商（现有：{list(providers)}）"
        )
    data.setdefault("models", {})["embedding"] = {"provider": provider, "models": [model]}
    app_yaml.write_text(
        "# 云端版默认配置（构建脚本生成，embedding 走云端 API）\n"
        + yaml.safe_dump(data, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    log(f"云端版 app.yaml：embedding → {provider}/{model}")


def layout_app_root(dist_app: Path, flavor: str, cloud_provider: str, cloud_model: str) -> None:
    """布置应用根目录（exe 同级），与 utils/app_paths.py 冻结路径对齐。"""
    # config：只带 YAML（*.py 已作为代码进 _internal；*.bak / __pycache__ 不带）
    config_dst = dist_app / "config"
    config_dst.mkdir(parents=True, exist_ok=True)
    src_config = PROJECT_ROOT / "config"
    copied = []
    for item in ["app.yaml", "source_catalog.yaml"]:
        if (src_config / item).exists():
            shutil.copy2(src_config / item, config_dst / item)
            copied.append(item)
    schools_dst = config_dst / "schools"
    schools_dst.mkdir(exist_ok=True)
    for yml in (src_config / "schools").glob("*.yaml"):
        shutil.copy2(yml, schools_dst / yml.name)
        copied.append(f"schools/{yml.name}")
    log(f"config 已复制：{copied}")

    if flavor == "cloud":
        patch_cloud_embedding(config_dst / "app.yaml", cloud_provider, cloud_model)

    # 前端产物
    dist_src = PROJECT_ROOT / "frontend" / "dist"
    if not dist_src.exists():
        raise SystemExit("[build] frontend/dist 不存在（构建失败或被跳过），无法布置应用根目录")
    shutil.copytree(dist_src, dist_app / "frontend" / "dist", dirs_exist_ok=True)
    log(f"frontend/dist 已复制（{sum(1 for _ in (dist_app / 'frontend' / 'dist').rglob('*'))} 个文件）")

    # VERSION / .env.example
    shutil.copy2(PROJECT_ROOT / "VERSION", dist_app / "VERSION")
    shutil.copy2(PROJECT_ROOT / ".env.example", dist_app / ".env.example")

    # 完整版带本地模型（云端版不带，见 campus_notice.spec excludes）
    if flavor == "full":
        models_src = PROJECT_ROOT / "models"
        if models_src.exists():
            shutil.copytree(models_src, dist_app / "models", dirs_exist_ok=True)
            log(f"models/ 已复制（完整版）")

    # 空数据目录（SQLite 首连自动建库；卸载/升级不触碰）
    (dist_app / "data" / "logs").mkdir(parents=True, exist_ok=True)


def compile_innosetup(flavor: str, version: str, iscc_path: str | None = None) -> Path | None:
    """查找 ISCC.exe 编译安装包；找不到则提示手动编译。"""
    iss = PACKAGING_DIR / "campus_notice.iss"
    if not iss.exists():
        log(f"未找到 {iss.name}，跳过安装包编译")
        return None

    candidates = [
        iscc_path or "",
        os.environ.get("INNO_SETUP_ISCC", ""),
        r"D:\Inno Setup 6\ISCC.exe",
        r"C:\Program Files (x86)\Inno Setup 6\ISCC.exe",
        r"C:\Program Files\Inno Setup 6\ISCC.exe",
    ]
    iscc = next((c for c in candidates if c and Path(c).exists()), None)
    if not iscc:
        log("未找到 Inno Setup（ISCC.exe）。安装后重跑，或手动在 Inno Setup 编译器打开 "
            f"{iss} 编译（定义 Flavor={'cloud' if flavor == 'cloud' else 'full'}）")
        return None

    out_dir = PACKAGING_DIR / "out"
    out_dir.mkdir(exist_ok=True)
    run([iscc, f"/DFlavor={flavor}", f"/DVersion={version}", str(iss)])

    suffix = "云端版" if flavor == "cloud" else "完整版"
    setup = out_dir / f"校园通知助手-{suffix}-setup.exe"
    if not setup.exists():
        # OutputBaseFilename 含中文时部分环境生成的名字可能不同，做个兜底查找
        matches = sorted(out_dir.glob("*setup.exe"), key=lambda p: p.stat().st_mtime, reverse=True)
        if not matches:
            raise SystemExit("[build] Inno 编译完成但 out/ 下没找到 setup.exe")
        setup = matches[0]
    sha = hashlib.sha256(setup.read_bytes()).hexdigest()
    (out_dir / f"{setup.name}.sha256").write_text(f"{sha}  {setup.name}\n", encoding="utf-8")
    log(f"安装包：{setup}（{setup.stat().st_size / 1024 / 1024:.1f} MB）")
    log(f"sha256：{sha}")
    return setup


def main() -> None:
    parser = argparse.ArgumentParser(description="校园通知智能助手一键构建")
    parser.add_argument("--flavor", choices=["cloud", "full"], default="cloud",
                        help="cloud=云端瘦身版（默认）| full=完整版（含 torch + 本地模型）")
    parser.add_argument("--skip-frontend", action="store_true", help="跳过前端构建（复用现有 dist）")
    parser.add_argument("--force-frontend", action="store_true", help="强制重新构建前端")
    parser.add_argument("--innosetup", action="store_true", help="构建后调用 Inno Setup 编译安装包")
    parser.add_argument("--iscc", default=None, help="ISCC.exe 路径（Inno Setup 装在非默认位置时指定）")
    parser.add_argument("--cloud-embedding-provider", default=CLOUD_EMBEDDING_DEFAULT_PROVIDER,
                        help=f"云端版 embedding 供应商（默认 {CLOUD_EMBEDDING_DEFAULT_PROVIDER}）")
    parser.add_argument("--cloud-embedding-model", default=CLOUD_EMBEDDING_DEFAULT_MODEL,
                        help=f"云端版 embedding 模型（默认 {CLOUD_EMBEDDING_DEFAULT_MODEL}）")
    args = parser.parse_args()

    version = (PROJECT_ROOT / "VERSION").read_text(encoding="utf-8").strip()
    if not version:
        raise SystemExit("[build] VERSION 文件为空")
    log(f"版本号：{version}（flavor={args.flavor}）")

    if not args.skip_frontend:
        if args.force_frontend:
            dist_dir = PROJECT_ROOT / "frontend" / "dist"
            if dist_dir.exists():
                shutil.rmtree(dist_dir)
        build_frontend()
    else:
        log("跳过前端构建（--skip-frontend）")

    dist_path = PACKAGING_DIR / f"dist-{args.flavor}"
    work_path = PACKAGING_DIR / f"build-{args.flavor}"
    env = {**os.environ, "CNA_FLAVOR": args.flavor}
    log(f"PyInstaller 打包中（onedir，flavor={args.flavor}）……")
    proc = subprocess.run(
        [sys.executable, "-m", "PyInstaller", str(PACKAGING_DIR / "campus_notice.spec"),
         "--distpath", str(dist_path), "--workpath", str(work_path), "--noconfirm"],
        cwd=str(PROJECT_ROOT), env=env,
    )
    if proc.returncode != 0:
        raise SystemExit("[build] PyInstaller 失败，见上方日志")

    app_dir = dist_path / "CampusNoticeAssistant"
    if not app_dir.exists():
        raise SystemExit(f"[build] 未找到产物目录 {app_dir}")

    layout_app_root(app_dir, args.flavor, args.cloud_embedding_provider, args.cloud_embedding_model)

    # Inno Setup 的版本号来源（campus_notice.iss 用 ReadIni 读取）
    (PACKAGING_DIR / "version.ini").write_text(
        f"[Version]\nvalue={version}\n", encoding="utf-8"
    )

    total_mb = sum(f.stat().st_size for f in app_dir.rglob("*") if f.is_file()) / 1024 / 1024
    log(f"完成：{app_dir}（{total_mb:.0f} MB）")
    log(f"冒烟测试：双击 {app_dir / 'CampusNoticeAssistant.exe'} → 自动开浏览器 → 抓一轮数据 → 问答可用")

    if args.innosetup:
        compile_innosetup(args.flavor, version, iscc_path=args.iscc)


if __name__ == "__main__":
    main()
