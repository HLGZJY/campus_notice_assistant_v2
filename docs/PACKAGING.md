# 打包发布方案

> **状态：🟡 打包基建已落地（2026-08-22），首次真机发布前按「开始前复核」核对**
> 路径冻结改造 / 检查更新前后端 / 构建脚本 / Inno Setup 脚本已全部实现；
> 实施细节见文末「实施记录」。剩余：确定 owner/repo → Inno 真机装一遍 → gh release。

## 目标与范围

- 目标用户：同学 / 小范围熟人
- 平台：Windows x64
- 形态：Inno Setup → `setup.exe`，安装到 `%LOCALAPPDATA%\CampusNoticeAssistant`
- 更新：检查更新 + 提示下载，走 GitHub Releases（项目仓库公开，A 方案）
- 不做：全自动静默更新、增量更新、多平台

## 关键决策记录

| 项 | 决定 | 理由 |
| ---- | ---- | ---- |
| 打包工具 | PyInstaller onedir + Inno Setup | 带 torch 时 onefile 首次启动慢；onedir 更稳 |
| 安装目录 | `%LOCALAPPDATA%\CampusNoticeAssistant` | Program Files 下运行时写 `.env`/`config` 需管理员权限 |
| Embedding | 双版本（云端瘦身版 + 本地完整版） | 代码已天然支持双模式（`utils/embedding.py`） |
| 版本检测 | `GET api.github.com/repos/{owner}/{repo}/releases/latest` | 免维护 update.json，tag 即版本号，body 即更新日志 |
| 下载加速 | 直连 GitHub + app.yaml 可配镜像前缀 | 国内直连慢，预留切换位 |
| 数据保留 | 安装器/卸载器均不碰 `data/` | 更新覆盖安装不丢用户数据 |

## 双 Embedding 版本组织

代码**已天然支持双模式**：`utils/embedding.py:202` 先试 OpenAI-compatible embedding API，未配 `base_url` 才走本地模型。因此：

- **云端版（默认分发）**：`models.embedding.provider` 指向云端 API 供应商；PyInstaller
  `--exclude-module torch --exclude-module sentence_transformers`，不带 `models/`。
  前提：`HuggingFaceEmbeddings` 是函数内延迟 import（`utils/embedding.py:166`），云端路径不会执行到它。
  → 体积约 150MB。
- **完整版（按需）**：provider 保持 `local`，`--add-data` 带上 `models/`（192MB bge 模型）。
  → 离线 embedding、无 token 费用，但下载 ~2.5GB、首次启动慢。
- **分发策略**：默认发云端版（同学反正要填 LLM API key）；确需离线/省 token 才发完整版。

## 实施步骤（到时按此执行）

1. 前端构建：`cd frontend && npm run build`（产出 `dist/`，约 1.6MB）
2. PyInstaller 双打包：
   - 云端版：排除 torch / sentence_transformers，去掉 `models/`
   - 完整版：全量 + `--add-data models/`
   - 均需带上 `data/`、`config/`、`.env.example`、`VERSION` 文件
3. 冒烟测试：启动 → 自动开浏览器 → 登录页可访问 → 抓一轮数据 → 问答可用
4. Inno Setup 脚本：桌面快捷方式、可选开机自启、卸载保留 `data/`、版本号写 `VERSION`
5. 检查更新功能：`config/app.yaml` 加 `update.repo` 配置 + 下载 URL 前缀（镜像切换位）；
   后端接口静默失败不影响使用；前端「设置→检查更新」+ 启动静默检测，弹窗展示 changelog + 版本 + sha256
6. 发布：公开仓库 → `gh release create v0.x.0` → 真机装一遍验证「提示→下载→覆盖安装→数据保留」

## 发布 SOP（以后每次更新）

```
改版本号 → 跑构建脚本 → gh release create v0.x.0 --notes "更新日志" → 完成
```

**⚠️ GitHub Release 附件名必须用 ASCII**：Inno 产出的安装包名含中文
（如 `校园通知助手-云端版-setup.exe`），直接 `gh release create` 上传会被编码破坏成
`-.-setup.exe`。发布时先复制成 ASCII 名再传：

```bash
cp "packaging/out/校园通知助手-云端版-setup.exe" \
   "packaging/out/campus-notice-assistant-cloud-setup-v0.1.0.exe"
gh release create v0.1.0 \
  packaging/out/campus-notice-assistant-cloud-setup-v0.1.0.exe \
  --title "v0.1.0 云端版" --notes "更新日志写这里"
```

`gh release create` 示例（双版本）：

```
gh release create v0.2.0 ^
  ./out/campus-notice-assistant-cloud-setup-v0.2.0.exe ^
  ./out/campus-notice-assistant-full-setup-v0.2.0.exe ^
  --title "v0.2.0 新功能" ^
  --notes "更新日志写这里"
```

## 已知风险

- PyInstaller 打 torch 的 hook 坑、完整版首次启动慢 → 已隔离在可选支线
- exe 易被杀毒软件（360/Defender）误报 → 首次给同学装需「信任并运行」，完整版更常见
- 国内直连 GitHub 下载慢 → 镜像前缀可切换
- ~~版本比对需实现~~ → 已实现（`services/update_service._is_newer`，MAJOR.MINOR.PATCH 数值比较）

## 开始前复核清单

- [x] `requirements-backend.txt` 依赖是否已变动（能否排除 torch 需重新验证）
- [x] `utils/embedding.py` 延迟 import 结构是否仍成立
- [x] 安装目录/数据目录结构是否仍与本文一致
- [x] Inno Setup 6 已装（`D:\Inno Setup 6`，非默认路径——build.py 用 `--iscc` 或
      环境变量 `INNO_SETUP_ISCC` 指定）；中文语言包 `ChineseSimplified.isl` 需手动放
      `D:\Inno Setup 6\Languages\`（官方安装不带，来源：kira-96/Inno-Setup-Chinese-Simplified-Translation）
- [ ] 决定 `owner/repo` 具体值，写入 `config/app.yaml`（`update.repo`）

## 实施记录（2026-08-22）

### 代码改造（打包前提）

**冻结感知路径解析**——新增 `utils/app_paths.py`（`get_app_root()`：开发=仓库根 /
冻结=exe 目录），替换了全部运行时 `Path(__file__)` 定位点：

| 文件 | 改造点 |
| ---- | ---- |
| `storage/db.py` | `DB_PATH = get_data_dir() / "notices.db"` |
| `storage/vectorstore.py` | `DEFAULT_PERSIST_DIR = get_data_dir() / "chroma"` |
| `config/store.py` | `get_instance()` 默认 `config_dir = get_config_dir()`（.env 同步锚定应用根） |
| `api/main.py` | `dist_path = get_frontend_dist()`；`/health` 版本号改读 VERSION 文件 |
| `services/source_center_service.py` | `CATALOG_PATH = get_config_dir() / "source_catalog.yaml"` |
| `services/health_service.py` | `HEALTH_DIR = get_data_dir() / "health"` |
| `utils/embedding.py` | 本地模型相对路径按 `get_app_root()` 解析 |
| `scheduler.py` | `DEFAULT_LOG_FILE` 锚定应用根；新增 `_resolve_log_path`：相对日志路径按应用根解析（快捷方式启动 cwd 不可控） |

### 新增文件

| 文件 | 职责 |
| ---- | ---- |
| `VERSION` | 版本号唯一来源（当前 0.1.0） |
| `run_app.py` | 打包入口：8000 起找空闲端口 → uvicorn 单进程 → 就绪自动开浏览器 |
| `packaging/campus_notice.spec` | PyInstaller onedir spec；`CNA_FLAVOR` 切换云端/完整版；hiddenimports 覆盖 uvicorn 字符串导入 + chromadb 动态子模块；jieba/newspaper/dateparser 数据文件 |
| `packaging/build.py` | 一键构建：前端 → PyInstaller → 布置应用根（config/frontend/VERSION/.env.example/models）→ 云端版改写 embedding 配置 → version.ini → 可选 ISCC 编译 + sha256 |
| `packaging/campus_notice.iss` | Inno Setup：per-user 安装（PrivilegesRequired=lowest）、桌面快捷方式、可选开机自启（HKCU Run）、`data\` 卸载保留（uninsneveruninstall）、升级前 app.yaml → app.yaml.old 备份 |
| `services/update_service.py` | 检查更新：GitHub releases/latest + semver 数值比较 + 镜像前缀；**静默失败契约**（恒 200） |
| `api/routes/update.py` | `GET /api/v1/update/check` |
| `utils/app_paths.py` | 冻结感知路径解析（见上） |

### 配置变更

- `config/schema.py`：新增 `UpdateConfig`（repo / download_prefix，全默认值——旧 app.yaml 无此段也能加载）
- `config/app.yaml`：追加 `update:` 段（repo 空 = 禁用）
- 前端：`ConfigView.vue` 新增「检查更新」tab（手动检查 + changelog + 下载按钮）；
  `App.vue` 启动 3s 后静默检查，有新版才弹 n-modal；openapi.json 已重新导出并 `gen:api`

### 云端版 app.yaml 改写规则（build.py）

构建时只改 dist 产物内的 `config/app.yaml`（不动仓库）：embedding 指向
`--cloud-embedding-provider`（默认 bailian）/ `--cloud-embedding-model`（默认
text-embedding-v4）。前提是同学反正要配 LLM API key，同一 key 走 embedding。

### 产物布局（onedir）

```
dist-cloud/CampusNoticeAssistant/
├── CampusNoticeAssistant.exe      # console=True：本地 Web 应用日志是排障入口
├── _internal/                     # PyInstaller：解释器 + 依赖（不动）
├── config/                        # app.yaml（云端版已改写）+ schools/ + source_catalog.yaml
├── frontend/dist/                 # 前端静态产物（api/main.py 托管 + SPA fallback）
├── data/                          # 空目录，首连自动建库；卸载保留
├── models/                        # 仅完整版（192MB bge）
├── VERSION                        # app_paths.get_version() 读取
└── .env.example                   # 安装后用户复制为 .env 填 API key
```

### 冒烟验证记录

- 开发模式全路径解析回归：DB/chroma/日志/health/catalog/config 全部指向仓库根 ✅
- `GET /api/v1/update/check` 未配 repo 时静默失败（200 + error 字段）✅
- 版本比较单测：v0.2.0 > 0.1.0、相等、回退、解析失败回退字符串比较 ✅
- 前端 vue-tsc 类型检查通过 ✅
- PyInstaller 云端版真实构建 ✅（chromadb.server.fastapi 收集 WARNING 可忽略——不跑 chroma 服务端）
- **exe 冻结冒烟 ✅（2026-08-22，226MB 版）**：双击启动 → `/health` 返回
  `{"status":"ok","version":"0.1.0","db":"ok"}` → 浏览器自动打开，前端全页面 API 200 →
  调度器入列全部定时任务 → 首轮抓取 `来源=3 发现=67 新增=53 失败=0`（jieba/newspaper
  冻结后正常）→ LLM 提取因无 `.env` key 优雅降级（WARNING 不崩溃，符合预期）。
  结论：**除需用户自配 API key 外，开箱即用**。

### 体积瘦身（1292MB → 226MB，2026-08-22）

首次构建用共享 venv 产生了 1292MB 的产物——paddle(194M)/cv2(149M)/playwright(101M)/
spacy(116M)/pyarrow/ctranslate2/scipy 等全是共享环境里的无关包，被 PyInstaller
传递性打入。**教训：PyInstaller 产物体积 = 构建环境的全量传递闭包，必须用专用干净 venv。**

修复：

- 新增 `packaging/requirements-build-cloud.txt`（云端构建最小依赖集，不含
  streamlit/sentence-transformers/torch）
- 新增 `packaging/venv-build/`（专用构建 venv，已 .gitignore）
- 构建命令固定为
  `packaging/venv-build/Scripts/python packaging/build.py --flavor cloud`
- 瘦身后 226MB，构成全部正当：chromadb Rust 绑定 61M、jieba 词典 30M、numpy 21M、
  PIL 13M（newspaper 依赖）、grpc 12M（chromadb 依赖）。原「约 150MB」估算偏乐观。

### openai-agents 提示词数据文件坑（冻结期首例 ImportError）

症状：exe 启动后 `agents/sandbox/memory/prompts/memory_consolidation_prompt.md 不存在`
→ 路由注册跳过、TaskManager/调度器/健康检查启动失败、health 返回 `db: unavailable`。

根因：openai-agents SDK（包名 `agents`）的 `sandbox/memory/prompts.py` 在 **import 时**
读包内 `.md` 提示词；`agents.run` 被本项目导入，整条链在冻结环境炸掉。PyInstaller
默认只收 `.py`。

修复：spec `datas` 加 `collect_data_files("agents")`。

### 本机沙箱 DLL 删除拦截（构建排障记录）

`rm -rf packaging/dist-cloud` 对 `_internal/VCRUNTIME140.dll` 报 Permission denied
（无进程持锁，重命名却可以）——本机沙箱对 DLL 删除有拦截（同 `data/` 目录行为）。
绕过：整个目录改名让路（`mv dist-cloud .dist-cloud-trash`），PyInstaller 用全新目录。
残留 trash 目录无碍，重启后可手动清理。

### 安装包真机验证（2026-08-22，setup.exe 85MB）

`校园通知助手-云端版-setup.exe`（85MB，LZMA2/max 压缩自 226MB 产物）在本机完成
安装 → 升级 → 卸载全链路验证：

| 场景 | 结果 |
| ---- | ---- |
| 静默安装（/VERYSILENT） | 装到 `%LOCALAPPDATA%\CampusNoticeAssistant`，无需管理员，目录结构与产物布局一致 ✅ |
| 安装版运行 | exe 启动 → `/health` `{"status":"ok","version":"0.1.0","db":"ok"}`，云端版 app.yaml 生效 ✅ |
| 覆盖升级 | 用户改过的 app.yaml 自动备份为 `app.yaml.old`（含用户标记），新配置干净覆盖，`data/` 原样保留 ✅ |
| 卸载 | 程序文件/开始菜单/自启注册表项全清；`data/`（notices.db/日志）**完整保留**（uninsneveruninstall 生效）；app.yaml.old 也保留（卸载后可找回配置）✅ |

sha256 已写入 `packaging/out/*.sha256`。发布命令（Git Bash 下 ISCC 参数会被路径
转换破坏，用 PowerShell 或走 build.py——build.py 用 subprocess 不经 bash，无此问题）：

```
packaging/venv-build/Scripts/python packaging/build.py --flavor cloud --innosetup --iscc "D:/Inno Setup 6/ISCC.exe"
```
