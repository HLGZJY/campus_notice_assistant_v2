# 校园通知智能助手 · 桌面版升级方案（v0.2.0）

> 状态：待评审 ｜ 版本：0.1 ｜ 编制日期：2026-09-01
> 上游依据：[PLAN.md](../PLAN.md)（原生桌面版规划）｜ [PACKAGING.md](PACKAGING.md)（v0.1.0 打包基建）
> 适用版本：当前 `VERSION = 0.1.0`（在线版 release 已发布），目标 **v0.2.0 桌面版（Windows）**

---

## 0. 结论摘要

| 项 | 结论 |
|---|---|
| **技术路线** | **pywebview + pystray + 自研轻量胶水层**，单进程内嵌 uvicorn；后端零改动、PyInstaller 链路 100% 复用 |
| **两条主线的关系** | 主线 A（前端优化收尾）是**内容侧**，主线 B（运行方式迁移）是**载体侧**；A 的 A4 桌面适配项与 B 强耦合，其余可与 B 并行 |
| **改造量级** | 新增 `desktop/` 壳包 ≈ 8 个模块；后端改造 ≈ 3 处（端口粘性、调度 pause/resume、控制面路由）；前端改造 ≈ 4 类（外链/剪贴板/DPI/设置页）；**68 个既有端点零改动** |
| **首版范围** | Windows x64 优先，macOS 放 P5 之后（无本机硬件，走 GitHub Actions） |
| **周期** | Windows 可交付版本 ≈ 6~7 周（Phase 0~4），完整分发能力 ≈ 10~11 周 |
| **最大风险** | 关闭控制台窗口后**日志不可见**（当前 `run_app.py:27` 仅 `basicConfig` 到 stdout）→ 必须先把日志落盘再做 console=False |
| **对 PLAN.md 的修正** | 3 处，见 §2.4（数据目录迁移优先级下调、localStorage/端口粘性坑新增、路由 history 模式无需改 hash） |

---

## 1. 升级目标

### 1.1 两条主线的定义

**主线 A：完成前端优化收尾，改造成原生 PC 桌面形态**

把 `frontend/` 的既有资产从「浏览器里跑的 Web 应用」打磨成「原生窗口里跑的应用界面」：工程规范化补齐、桌面环境适配（外链/剪贴板/DPI/焦点）、桌面专属设置与通知中心，最终由系统 WebView 窗口承载，观感与交互对齐原生工具类应用。

**主线 B：运行方式迁移为免后台命令的独立启动形态**

把当前的「`uvicorn` 命令 / `run_app.py` 起服务 → 残留终端窗口 → 自动拉起系统浏览器 → 浏览器标签页里用」迁移为「**双击桌面快捷方式 → 原生窗口出现 → 关闭即最小化到托盘**」：无终端窗口、无外部浏览器依赖、无需任何命令行操作、生命周期由应用自管。

> 两条主线是同一目标的两面：**B 提供"壳与生命周期"，A 提供"壳里的正确内容"**。B 可以先用现有前端跑通（Phase 1），A 的内容侧优化（A1~A3）并行推进，A4 桌面适配必须在 B 冻结前完成。

### 1.2 量化目标（v0.2.0 出口指标）

| 指标 | 现状（v0.1.0 在线版） | 目标（v0.2.0 桌面版） | 度量方式 |
|---|---|---|---|
| 终端窗口 | 1 个常驻控制台（spec `console=True`） | **0** | 进程树检查（`tasklist / 无 conhost`） |
| 浏览器依赖 | 依赖默认浏览器（新标签页） | **0**（系统 WebView 内嵌） | 任务管理器无浏览器进程 |
| 启动方式 | exe（仍需双击，但带控制台） | 双击快捷方式，**2s 内首屏** | 秒表 + health 轮询打点 |
| 进程数 | 1（exe）+ N（浏览器进程） | **1**（单进程，含 daemon 线程） | 任务管理器 |
| 壳层内存增量 | —（无壳） | **≤ 60 MB** | Phase 0 实测（§9.4） |
| 空闲总内存 | 60~120 MB（云端嵌入版） | **≤ 180 MB** | 同上 |
| 关闭行为 | 关窗口即退出 | **最小化到托盘，进程常驻** | 人工验收 |
| 双开 | 无保护（可双开写坏 SQLite） | **单实例锁 + 唤起主窗口** | 人工验收 |
| 开机自启 | Inno 安装时可选（已有） | **应用内开关，即时生效** | 注册表 + 注销重登 |
| 排障能力 | 控制台日志 | **日志文件 + 一键导出** | 模拟异常后导出 |

### 1.3 非目标（本期明确不做）

- 重写后端（Python 栈 + FastAPI 68 端点是核心资产，重写成本不可接受）；
- 重写前端为原生控件 UI（Vue3 八页面已完善，保留为 WebView 承载内容）；
- 差分更新、多用户账号体系、强制遥测；
- macOS 首版（无本机硬件，CI 构建可验证但无法本地联调，放 P5）；
- 移动端 / 服务端部署形态改动。

---

## 2. 现状盘点与关键判断

### 2.1 资产盘点（调研证据）

| 层 | 现状 | 证据 | 桌面化影响 |
|---|---|---|---|
| 前端通信 | `API_BASE = '/api/v1'`，全程**相对路径** | `frontend/src/api/endpoints.ts:1`、`api/http.ts` | ✅ 随机端口天然可用 |
| SSE | **fetch + ReadableStream** 手动解帧（非 EventSource），相对路径 | `frontend/src/stores/useQaStore.ts:112-124` | ✅ 端口无关，WebView2 兼容 |
| 端口 | 单点探测 `find_free_port`（8000~8019），绑 `127.0.0.1` | `run_app.py:33-46,74-82` | ✅ 收敛，唯一改造点 |
| 路径 | `utils/app_paths.py` 已单点收敛（dev / frozen 两态） | `utils/app_paths.py:18-45` | ✅ 改造面小 |
| 落盘代码 | `Path(__file__)` 仅 2 处且均为 `sys.path.insert` | `crawler/base.py:24`、`crawler/web_crawler.py:34` | ✅ 无需改 |
| SQLite | `check_same_thread=False, timeout=30` | `storage/db.py:321` | ✅ uvicorn 放子线程无需改连接层 |
| 打包 | `build.py` + spec + iss 全链路已验证，安装版真机跑通 | `PACKAGING.md` 实施记录 | ✅ 90% 可复用 |
| 安装形态 | **per-user**（`{localappdata}`），`PrivilegesRequired=lowest` | `campus_notice.iss:39,42` | ✅ 无需管理员 |
| 自启 | Inno `[Tasks] autostart` + HKCU Run **已实现** | `campus_notice.iss:57,74-76` | ✅ 应用内开关可复用 |
| 更新检查 | `check_for_update()` 已实现（GitHub Releases + 镜像前缀 + 静默失败） | `services/update_service.py:26-59` | ⚠️ 只检查不下载 |
| 调度 | 5 个 job（crawl/extract/daily/reminder/config-watch），`max_instances=1, coalesce=True` | `scheduler.py:171-213` | ⚠️ **缺 pause/resume** |
| 日志 | scheduler 有 `RotatingFileHandler`（5MB×3）；`run_app.py` **仅 stdout** | `scheduler.py:101,120`、`run_app.py:27` | ❌ 关控制台即失明 |
| 测试 | 32 个 `test_*.py`，覆盖 API/调度/抓取/检索/任务/缓存/并发 | 根目录 | ✅ 回归基线充足 |
| 桌面代码 | `desktop|tray|webview|pystray` 全仓 **零命中** | grep | ➕ 全新建设 |

### 2.2 四条利好（决定路线可行）

1. **前端零硬编码后端地址**——无 `localhost:8000`（仅 dev proxy 用到），随机端口对前端透明；
2. **SSE 用 fetch 流而非 EventSource**——相对路径 + 无独立连接管理，桌面化改动为零（原以为这是最大风险，实测不是）；
3. **后端路径治理已收敛**（`app_paths` 单点 + `check_same_thread=False`）——内嵌线程不需要动存储层；
4. **打包/安装/自启基建全通**——桌面版只需新增一个 flavor 与入口脚本，不需要重建链路。

### 2.3 六处缺口（必须补齐）

| # | 缺口 | 证据 | 补齐动作 |
|---|---|---|---|
| G1 | 日志只到 stdout，关控制台后无排障入口 | `run_app.py:27` | 新增 `desktop/logging_setup.py`，root logger → `data/logs/app.log`（Rotating 5MB×3）+ uvicorn `log_config` 显式接管 |
| G2 | `console=True` | `campus_notice.spec:106` | desktop flavor 用 `console=False` |
| G3 | 自动开系统浏览器 | `run_app.py:49-60,71` | 移除，改 webview 加载 |
| G4 | 无托盘/单实例/窗口记忆/看门狗 | 全仓零命中 | `desktop/` 壳包 |
| G5 | 调度器无 pause/resume | `scheduler.py:236-238` 仅 start/stop | 新增 `pause()/resume()` + 控制面路由 |
| G6 | SQLite 未开 WAL | 全仓无 `journal_mode` | 连接初始化加 `PRAGMA journal_mode=WAL`（读写并发更稳） |

### 2.4 对 PLAN.md 的三处修正（重要）

| # | PLAN.md 原判断 | 修正后 | 理由 |
|---|---|---|---|
| **R1** | F5 数据目录迁移列为 **P0**，认为 exe 同级 = 程序目录、需迁到 `%APPDATA%` | **下调为 P1**，可作为 P4 阶段可选交付 | Inno 实际装到 `{localappdata}\CampusNoticeAssistant`（`iss:39`），已是 per-user 可写目录，写入权限与升级保留（`uninsneveruninstall`，`iss:64-65`）**均已验证可用**。迁移的真实收益是"程序与数据分离、升级可整目录替换"，属整洁性收益而非阻塞项。**但**必须同步做"三态兼容"：老用户 exe 同级 `data/` 必须能继续读到（向后兼容优先） |
| **R2** | 未识别端口随机化对 **localStorage** 的影响 | **新增为 P0 设计约束** | `localStorage` 按 origin 分区，origin 含端口。当前端口每次从 8000 起探测，若上次用了 8001，QA session_id、问答历史缓存、主题（`useQaStore.ts:26,29,63,73,192`、`useThemeStore.ts:16,49`）会**静默丢失**。对策见 §6 K1 **端口粘性** |
| **R3** | F7 建议关注路由模式，隐含"可能需要改 hash" | **不需要改**，保持 `createWebHistory()` | 桌面版加载的是 `http://127.0.0.1:<port>`，由后端 SPA fallback 托管（`api/main.py:159-166`），刷新/深链均由后端兜底返回 index.html。只有改成 `file://` 直载静态资源才会 404——本方案不采用该模式 |

---

## 3. 技术选型依据

### 3.1 选型约束（不可协商的前提）

1. 后端重度依赖 Python 生态（FastAPI / APScheduler / Chroma / openai-agents / jieba / newspaper4k）→ **后端必须原样保留，同进程最优**；
2. 前端是纯静态产物（`frontend/dist` 1.8 MB）→ 可被任意 WebView 承载，**不需要重写**；
3. 含 SSE 长连接 → 壳必须支持 fetch 流式读取（排除部分老旧内嵌内核方案）；
4. 资源占用最优优先（相对 Electron）→ 排除自带 Chromium 的方案；
5. PyInstaller + Inno Setup + GitHub Releases 已验证 → 优先复用，不重建分发链路。

### 3.2 候选对比

| 方案 | 壳内存增量 | 安装体积增量 | 后端复用 | 托盘/自启 | 自动更新 | 跨平台一致性 | 结论 |
|---|---|---|---|---|---|---|---|
| **A. pywebview + pystray** | +20~50 MB | **≈ 0**（复用系统 WebView2） | ✅ **同进程零改动** | 自研（≈1 周） | 自研（复用已有 update 服务） | WebView2 / WKWebView 有差异 | ✅ **选定** |
| B. Tauri v2 + Python sidecar | +15~40 MB | +5~10 MB | ⚠️ 需 sidecar 进程管理 | ✅ 官方插件 | ✅ 官方 updater | 同左 | 备选（P1） |
| C. Electron | +90~160 MB | +90~150 MB | ⚠️ sidecar | ✅ | ✅ 最成熟 | 双平台一致 | ❌ 排除（体积/内存违背目标） |
| D. PySide6 + QWebEngine | +120~250 MB | +200~300 MB | ✅ 同进程 | ✅ QSystemTrayIcon | 自研 | 一致 | ❌ 排除（自带 Chromium，与 Electron 同级重量） |

> 数字为典型空闲值，**Phase 0 必须实测**（§9.4）。总内存 = 后端基线（云端嵌入版 60~120 MB）+ 壳增量，Electron/Qt 路线将逼近 300~500 MB，违背 §1.2 目标。

### 3.3 选定理由（pywebview）

1. **后端零改动**：同进程内嵌，75 个端点、6 个调度 job、TaskManager、SSE 全部原样工作，不需要 sidecar 进程管理与 IPC；
2. **打包链路 100% 复用**：只需新增一个 `desktop` flavor 与入口脚本，spec 的 hiddenimports / datas 体系不动；
3. **前端已天然适配**：相对路径 + fetch 流 SSE + 后端 SPA fallback（§2.2），这是选型时最大的不确定项，实测已排除；
4. **纯 Python**：与现有团队栈一致，无 Rust/Node 工具链，CI 与本地联调成本最低；
5. **资源最优**：无自带内核，壳增量极小（复用系统 WebView2，Win11 预装）。

**代价（明确接受）**：托盘/自启/单实例/更新器需自研。其中自启在 Inno 侧已有基础（`iss:57,74-76`），托盘用 pystray（成熟库），单实例用 socket 锁（约 50 行），更新器复用现有 `update_service`（补下载与校验）——**自研总量可控在 1~2 周**。

### 3.4 关键子项选型

| 子项 | 选型 | 理由 |
|---|---|---|
| 窗口 | **pywebview**（Win: WebView2 / mac: WKWebView） | 系统共享内核，零体积增量 |
| 托盘 | **pystray** + Pillow（Pillow 已随 newspaper 依赖存在） | 纯 Python，Win/mac 统一 API |
| 单实例 | **socket 端口锁**（绑定固定高位端口，失败即已有实例 + 通知其唤起） | 跨平台统一，比文件锁/named mutex 简单，且天然带"唤醒"通道 |
| 自启 | Win: **HKCU Run**（复用 Inno 已有实现）｜ mac: LaunchAgent plist | per-user，免管理员 |
| 更新 | **复用 GitHub Releases + `update_service`**，新增 `latest.json` 清单 + sha256 校验 | 现有检查链路已通，只补"下载→校验→安装" |
| 空闲检测 | Win: `GetLastInputInfo`（ctypes）｜ mac: `CGEventSourceSecondsSinceLastEventType` | 无第三方依赖，约 30 行 |
| 系统通知 | pystray `notify()`（Win balloon）/ 后续可换 `winotify` | 首版够用，避免引入新依赖 |
| 日志 | 复用 `RotatingFileHandler`（`scheduler.py:101` 已验证） | 与现有日志体系一致 |

### 3.5 切换准则（何时放弃 pywebview 转 Tauri）

| 触发条件 | 动作 |
|---|---|
| Phase 0 实测壳内存 > 60 MB | 重新评估，优先排查依赖，其次考虑 Tauri |
| WebView2/SSE 兼容性问题无法在 1 周内解决 | 转 Tauri（WKWebView/WebView2 同为系统内核，问题可能依旧，需先定位到具体层） |
| 需要全局快捷键 / 深度原生通知 / 官方 updater 且自研成本超 2 周 | 评估 Tauri |
| 安装包体积成为硬指标（< 30 MB） | 评估 Tauri（当前云端版 setup 85 MB，主因是 Chroma/jieba 而非壳） |
| 只需要 Windows 且生态够用 | 维持 pywebview，不迁移 |

**架构上预留"壳可替换"边界**：`desktop/` 与后端只通过两个接口耦合——① 起停 uvicorn 线程（`server.py`）② `/api/v1/desktop/*` 控制面路由。换壳时只需重写 `desktop/` 的窗口/托盘部分。

---

## 4. 目标架构

### 4.1 进程模型

```
┌────────────────── 桌面应用进程（PyInstaller onedir, console=False） ──────────────────┐
│ 主线程   pywebview 窗口（WebView2）                                                    │
│          └─ 加载 http://127.0.0.1:<sticky_port>（后端托管 SPA fallback）               │
│          └─ 关闭事件 → hide + 托盘（真退出由托盘菜单触发）                             │
│ daemon 线程  uvicorn.Server（api.main:app，含 TaskManager + APScheduler lifespan）     │
│ daemon 线程  pystray 托盘（打开/暂停调度/检查更新/打开日志/退出）                       │
│ daemon 线程  看门狗：每 5s 探 /api/v1/health，连续 3 次失败 → 重建 server 线程          │
│ daemon 线程  空闲检测（GetLastInputInfo）→ 触发/挂起重活                               │
│ 生命周期  启动：单实例锁 → 端口粘性 → 起后端 → 等 health → 显示窗口                    │
│           退出：server.should_exit=True → join → 停调度 → 停托盘 → 释放锁              │
└───────────────────────────────────────────────────────────────────────────────────────┘
```

**为什么单进程内嵌而非双进程**：实现最简单、崩溃路径单一、无 IPC 开销；SQLite 单写者模型与配置写权唯一性（既有架构前提）天然保持；后端崩溃由看门狗线程重启，不依赖外部监护进程。

### 4.2 目标目录结构（新增部分）

```
desktop/                       # 桌面壳（新增）
├── __init__.py
├── __main__.py                # python -m desktop（开发模式入口）
├── app.py                     # 壳主控：编排启动/退出/信号
├── config.py                  # 壳设置（窗口几何/自启/关闭行为），落 settings.json
├── logging_setup.py           # 日志落盘 + uvicorn log_config 接管
├── server.py                  # uvicorn 线程托管 + 端口粘性 + 看门狗
├── tray.py                    # pystray 托盘与菜单
├── single_instance.py         # socket 单实例锁 + 唤醒已有实例
├── autostart.py               # Win HKCU / mac LaunchAgent
├── idle.py                    # 空闲检测（P2）
└── updater.py                 # 下载 + sha256 校验 + 静默安装（P3）

desktop_main.py                # PyInstaller 桌面版入口（与 run_app.py 并存）
api/routes/desktop.py          # 控制面路由（新增，/api/v1/desktop/*）
```

> `run_app.py` **保留不动**，作为开发模式与 `--browser` 降级入口（故障时用浏览器打开，见 §10 决策项 5）。

### 4.3 新增控制面 API（`/api/v1/desktop/*`）

| 端点 | 方法 | 用途 | 优先级 |
|---|---|---|---|
| `/desktop/status` | GET | 壳状态：版本/端口/后端健康/调度状态/窗口可见性 | P0 |
| `/desktop/scheduler` | POST | `{"action": "pause"\|"resume"}` 暂停恢复调度 | P1 |
| `/desktop/autostart` | POST | `{"enabled": bool}` 开关开机自启 | P0 |
| `/desktop/open-log-dir` | POST | 打开日志目录（系统文件管理器） | P0 |
| `/desktop/restart-backend` | POST | 手动重启后端线程（排障用） | P1 |
| `/desktop/quit` | POST | 请求应用退出（前端"退出应用"按钮） | P1 |

安全：与 §6 K7 启动令牌共用校验中间件；`/desktop/quit` 仅限本机且需带令牌。

---

## 5. 改造范围（WBS）

### 5.1 主线 A：前端优化收尾

| # | 任务 | 优先级 | 依据/证据 | 验收标准 |
|---|---|---|---|---|
| **A1** | 工程规范化：补 `typecheck`（`vue-tsc --noEmit`）、`lint`（ESLint 9 flat + vue）、`engines.node >= 20`；构建前强制类型检查 | P0 | `frontend/package.json:7-10` 仅 dev/build/preview/gen:api | 三条脚本可跑通；CI 能拦截类型错误 |
| **A2** | 类型源收敛：审计 `api/schema.ts`（手写）与 `api/types.ts`（openapi 生成）重叠面，确立"openapi 单一事实源"，`schema.ts` 仅保留生成类型无法表达的部分（SSE 事件/前端枚举） | P1 | 契约驱动原则（README §4.9） | 产出重叠清单与收敛规则；无重复定义 |
| **A3** | 构建与加载性能：`router/index.ts:12-26` 改路由懒加载；先做产物体积归因再决定是否优化依赖 | P1 | `frontend/dist` 实测 1.8 MB（量级健康） | 首屏 JS 体积下降；8 个页面功能不变 |
| **A4** | **桌面环境适配**（桌面化硬前置）：① 外链统一走系统浏览器（`App.vue:52`、`ConfigView.vue:121` 的 `window.open`，及 6 页面 10 处 `target=_blank`）② 剪贴板（`DataSourceCenterView.vue:559`）③ DPI 125%/150% 缩放 ④ 确认 localStorage 依赖项在 WebView2 中可用（依赖 K1 端口粘性） | **P0** | 见证据列 | 外链走默认浏览器；缩放无破版；主题/会话跨重启保持 |
| **A5** | 桌面专属 UI：设置页新增"桌面"分组（开机自启/启动方式/关闭行为/最小化到托盘/日志导出/检查更新） | P1 | F2/F4/F6 | 开关即时生效并持久化 |
| **A6** | 通知中心：未读徽标 + 通知列表（更新摘要/周报/提醒），已读持久化 | P2 | F4 | 徽标与已读状态正确 |

### 5.2 主线 B：运行方式迁移与桌面壳

| # | 任务 | 优先级 | 改造点 | 验收标准 |
|---|---|---|---|---|
| **B1** | 新建 `desktop/` 壳包（8 模块） | P0 | 新增 | 模块职责清晰，可 `python -m desktop` 启动 |
| **B2** | 进程模型落地：uvicorn 托管到 daemon 线程 + 看门狗 | P0 | 新增 | 注入崩溃可自动恢复；连续 3 次失败弹窗 |
| **B3** | 端口粘性（K1） | P0 | `run_app.py:37-46` 逻辑迁到 `desktop/server.py` | 连续两次启动端口一致；端口被占自动切换且不丢 localStorage 关键项 |
| **B4** | 打包改造：`build.py` 增 `--flavor desktop`；spec 入口改 `desktop_main.py` 且 `console=False` | P0 | `build.py:176`、`campus_notice.spec:82,106` | 产出 exe 无控制台窗口 |
| **B5** | 托盘 + 关闭最小化 + 窗口几何记忆 | P0 | 新增 | 点 X 隐藏到托盘；几何跨重启恢复 |
| **B6** | 单实例锁 + 唤起已有实例 | P0 | 新增 | 双开第二实例退出并唤起主窗口 |
| **B7** | 日志落盘 + 一键导出 | P0 | 补 G1 | 无控制台下异常可导出日志 |
| **B8** | 开机自启应用内开关 + 启动参数 `--minimized/--autostart/--browser` | P1 | 复用 `iss:57,74-76` | 注销重登后自动驻留托盘 |
| **B9** | 控制面路由 `/api/v1/desktop/*` | P0 | 新增 `api/routes/desktop.py` | 6 个端点可用（§4.3） |
| **B10** | 启动令牌与 API 校验中间件 | P0 | 新增 | 无令牌请求被拒（本机越权防护） |
| **B11** | 调度 pause/resume + 空闲触发 + 电量感知 | P1 | 补 G5 | 空闲 5 分钟跑重活；动鼠标 1s 内挂起 |
| **B12** | 更新闭环：`latest.json` + 下载 + sha256 + 确认安装 | P1 | 扩展 `update_service.py` | 发布新 tag 后可完成一次完整升级 |
| **B13** | 数据目录三态 + 老数据迁移向导 | P1（修正 R1） | 改 `utils/app_paths.py` | 老用户 exe 同级 `data/` 平滑迁移，原数据保留 |
| **B14** | SQLite 开 WAL | P1 | 补 G6 | 并发测试全绿 |

### 5.3 需保留的既有功能（回归契约）

以下功能**必须原样保留**，任何一项回归即视为失败：

| 类别 | 具体功能 | 保留方式 | 回归验证 |
|---|---|---|---|
| 接口 | 11 路由模块 / 68 端点 + SSE | 零改动 | `test_api_smoke.py` |
| 异步任务 | TaskManager + 任务锁幂等 + 崩溃恢复 | 零改动（lifespan 照常拉起） | `test_tasks.py`、`test_resume.py` |
| 调度 | 5 个 job（crawl/extract/daily/reminder/config-watch） | 零改动 | `test_scheduler_integration.py` |
| 抓取 | 增量抓取 + 内容指纹 + 年龄过滤 | 零改动 | `test_incremental_crawl.py`、`test_content_fingerprint.py`、`test_crawl_age_filter.py` |
| 提取 | 规则预筛 + 中文时间解析 + 重试归因 | 零改动 | `test_fast_path.py`、`test_gongshi_period.py`、`test_retry_attribution.py` |
| RAG | 混合检索 + 缓存 + 引用 + SSE 错误契约 | 零改动 | `test_hybrid.py`、`test_qa_cache.py`、`test_qa_citation.py`、`test_qa_sse_error.py` |
| 存储 | SQLite 并发 + 向量一致性 | 零改动（加 WAL 需回归） | `test_db_concurrency.py`、`test_vectorstore_singleton.py` |
| 订阅/提醒 | 规则匹配 + 提醒生成 | 零改动 | `test_subscription.py`、`test_reminder.py` |
| Token 计量 | 统一调用点记账 | 零改动 | `test_token_usage.py` |
| 更新检查 | GitHub Releases + 静默失败契约 | 扩展（保留原契约） | 手工：未配 repo 时恒 200 |
| 打包 | cloud/full 双 flavor + per-user 安装 + data 卸载保留 + app.yaml.old 备份 | 并存（新增 desktop flavor） | 真机装→升→卸三连 |
| 前端 | 8 个页面全部功能 + 响应式断点（960/720/540） | 只增不改 | 逐页冒烟 + 缩放验证 |

---

## 6. 关键技术决策与实现要点（避坑）

### K1 端口粘性（新增，PLAN.md 未覆盖）

**问题**：`localStorage` 按 origin 分区且 origin 含端口。当前每次启动从 8000 起探测，若上次落在 8001（8000 被占），QA session_id、问答历史缓存、主题设置全部丢失，且用户无法理解原因。

**方案**：上次成功端口写入 `data/runtime.json`；启动时**优先尝试复用**，失败（被占/绑定异常）才回退到顺序探测并把新端口写回。同时把 `data/runtime.json` 写失败视为可容忍（降级为随机端口）。

**补充**：Phase 0 需实测 pywebview 的 WebView2 是否因 `--user-data-dir` 变化导致 storage 分区漂移；若漂移，改用固定 user-data-dir（落在应用数据目录）。

### K2 uvicorn 托管到子线程与重启

要点：
- 用 `uvicorn.Config(...) + uvicorn.Server(cfg)`，在 daemon 线程里 `server.run()`（内部自建 event loop，子线程安全）；**不要用 `uvicorn.run()`**（它会装 signal handler，子线程中会抛 `ValueError: set_wakeup_fd only works in main thread`）；
- 退出用 `server.should_exit = True` 再 `thread.join(timeout=10)`；
- **重启必须重建 `Server` 实例**（`server.run()` 返回后 loop 已关闭，不可复用）；重启会重跑 lifespan → TaskManager / scheduler 会重新 `start()`，需确认 `start_scheduler()` 可重入（**风险点：见 §8 R7**）；
- `log_config` 必须显式传入（见 K3），否则 uvicorn 会用默认 dictConfig 覆盖 root logger。

### K3 日志落盘（console=False 的前置条件）

- 新增 `desktop/logging_setup.py`：root logger 加 `RotatingFileHandler('data/logs/app.log', maxBytes=5MB, backupCount=3)`（复用 `scheduler.py:101` 已验证参数）；
- uvicorn 侧传 `log_config=<自定义 dictConfig>`，把 `uvicorn*`/`uvicorn.access` logger 指向同一文件 handler（`propagate=False`），避免重复输出；
- `console=False` 时 `sys.stdout` 为 None → 任何残留的 `print()` / `basicConfig()` 到 stdout 的调用必须清理或重定向，否则崩溃（**这是关闭控制台后最常见的启动即退**）；
- 未捕获异常钩子 `sys.excepthook` + `threading.excepthook` → 写日志 + 弹窗（含"导出日志"按钮）。

### K4 路由模式保持 history（无需改 hash）

桌面版加载 `http://127.0.0.1:<port>/`，前端与 API 同源（相对路径），刷新与深链由 `api/main.py:159-166` 的 SPA fallback 兜底。**不要改成 `file://` 直载静态资源**（会 404 且丢失 SSE/fetch 能力）。

### K5 关闭语义与优雅退出

- pywebview `window.closing` 事件 → `window.hide()` + 返回 False 取消关闭；
- 托盘"退出" → 按顺序：写窗口几何 → `server.should_exit=True` → join → `scheduler.stop()` → `pystray.stop()` → 释放单实例锁 → `sys.exit(0)`；
- 注册 `WM_QUERYENDSESSION`（Windows 关机/注销）走同一退出路径，避免数据损坏；
- 二次确认：若有 running 任务，退出前提示"有任务进行中，确认退出？"。

### K6 单实例锁

- 启动时尝试绑定固定高位端口（如 51999）；失败 → 说明已有实例 → 向该端口发送 `activate` 指令 → 自身立即退出；
- 主实例监听该 socket，收到 `activate` → `window.restore() + window.show() + focus()`；
- 端口被其他程序占用的兜底：改用同名 named mutex / 文件锁（Windows 侧 `msvcrt.locking`），并记录日志。

### K7 启动令牌

- 每次启动生成 `secrets.token_urlsafe(32)`，通过 `webview` 初始化脚本注入前端全局（`window.__CNA_TOKEN__`），前端 http client 统一加 `X-Desktop-Token` 头；
- 后端中间件校验：仅对 `/api/v1/*` 生效，且 `127.0.0.1` 来源；
- 开发模式（`python -m desktop`）与 `--browser` 降级模式下**令牌校验关闭**，避免影响现有 dev 流程与测试脚本。

### K8 SQLite WAL

连接初始化加 `PRAGMA journal_mode=WAL; PRAGMA synchronous=NORMAL;`（`storage/db.py`）。收益：读写不互斥，托盘后台跑调度时前端轮询不卡顿。风险：需回归 `test_db_concurrency.py`；WAL 下 `data/` 目录会出现 `-wal`/`-shm` 文件，备份脚本需一并处理。

### K9 调度 pause/resume

`scheduler.py` 新增 `pause()/resume()`（APScheduler `scheduler.pause()` 原生支持），经 `/api/v1/desktop/scheduler` 暴露；托盘菜单"暂停调度"调用它——替代"停线程"的粗暴做法，避免重跑 lifespan。

---

## 7. 实施步骤

### 7.1 阶段总览

| 阶段 | 名称 | 主线 | 关键交付 | 出口条件（Gate） |
|---|---|---|---|---|
| **P0** | 可行性验证 + 前端优化启动 | A1,A2,A3 | 选型实测报告；前端工程规范补齐 | Shell 内存 < 60MB；SSE 在 WebView2 正常；typecheck/lint 可跑 |
| **P1** | 桌面壳 MVP（Windows） | B1,B2,B3,B5,B6,B7 | 窗口/托盘/单实例/最小化/看门狗/日志 | 无控制台启动成功；8 页面可用；32 个测试全绿 |
| **P2** | 运行方式切换与打包 | B4,B9,B10,A4 | desktop flavor 构建；控制面路由；令牌 | 安装包真机装→跑→退全通；外链走系统浏览器 |
| **P3** | 工具能力 | B8,B11,A5 | 自启开关；空闲触发；桌面设置页 | 注销重登自启；空闲 5 分钟触发重活 |
| **P4** | 数据与安全 | B13,B14,B12 | 目录三态 + 迁移向导；WAL；更新闭环 | 老数据迁移前后一致；一次完整升级走通 |
| **P5** | 分发与 macOS | CI/签名/DMG | CI 矩阵 + 冒烟 + 签名（如具备证书） | 双平台产物可安装 |
| **P6** | 打磨与发布 | A6, 内存优化 | 通知中心；崩溃上报；正式 release | 稳定 2 周无 P0；v0.2.0 发布 |

### 7.2 依赖关系

```
P0 可行性 + 前端规范化
 ├─▶ P1 桌面壳 MVP ──▶ P2 打包与运行方式切换 ──┬─▶ P3 工具能力
 │        (A4 必须在 P2 前完成)                 ├─▶ P4 数据与安全（可与 P3 并行）
 └─▶ A1/A2/A3 前端优化（与 P1~P4 并行）        └─▶ P5 分发 ─▶ P6 发布
```

### 7.3 各阶段任务明细

**P0（第 1 周）— 可行性验证 + 前端优化启动**

| 序 | 任务 | 产出 |
|---|---|---|
| 0.1 | 建最小 pywebview demo：加载 `http://127.0.0.1:<port>`，跑通 8 页面 + SSE 流式问答 + 任务进度 | 冒烟报告 + 截图 |
| 0.2 | 资源实测：壳/后端/嵌入模型三态内存与启动耗时 | 实测数据表（§9.4） |
| 0.3 | WebView2 兼容性：Win10 老版本检测方案、SSE/剪贴板/DPI 验证 | 兼容清单 |
| 0.4 | 前端 A1 工程规范化（typecheck/lint/engines） | 脚本 + CI 钩子 |
| 0.5 | **选型评审定案**（本文档 + 实测数据） | 评审结论，触发 §3.5 切换准则判断 |

**P1（第 2~3 周）— 桌面壳 MVP**

| 序 | 任务 | 产出 |
|---|---|---|
| 1.1 | `desktop/` 骨架 + `python -m desktop` 开发入口 | 壳包 |
| 1.2 | `server.py`：端口粘性 + uvicorn 子线程 + 看门狗 | 后端托管模块 |
| 1.3 | `logging_setup.py`：日志落盘 + excepthook | 排障能力 |
| 1.4 | `tray.py` + 关闭最小化 + 窗口几何记忆 | 托盘与生命周期 |
| 1.5 | `single_instance.py` socket 锁 + 唤起 | 单实例 |
| 1.6 | 退出编排（K5）+ 关机信号处理 | 优雅退出 |
| 1.7 | 32 个既有测试全跑一遍（回归基线） | 回归报告 |

**P2（第 4 周）— 运行方式切换与打包**

| 序 | 任务 | 产出 |
|---|---|---|
| 2.1 | `desktop_main.py` 入口 + spec `console=False` + `build.py --flavor desktop` | 构建链路 |
| 2.2 | 前端 A4 桌面适配（外链/剪贴板/DPI/localStorage） | 前端改造 |
| 2.3 | `api/routes/desktop.py` 控制面 6 端点 + openapi 重新导出 + `gen:api` | 控制面 |
| 2.4 | 启动令牌 K7（注入 + 校验中间件） | 安全加固 |
| 2.5 | 真机冒烟：装→启动（无控制台）→ 8 页面 → SSE → 退出 | 冒烟报告 |

**P3（第 5 周）— 工具能力**
3.1 自启开关（B8，复用 Inno 已有着陆点）｜ 3.2 启动参数 `--minimized/--autostart/--browser` ｜ 3.3 调度 pause/resume（K9）｜ 3.4 空闲检测 + 重活 gate + 电量感知（B11）｜ 3.5 前端 A5 桌面设置页

**P4（第 6~7 周）— 数据与安全**
4.1 `app_paths` 三态改造（dev / portable / installed）｜ 4.2 老数据迁移向导（只读复制 + 校验 + 标记）｜ 4.3 SQLite WAL（K8）+ 并发回归 ｜ 4.4 自动备份（每周，保留 3 份）｜ 4.5 更新闭环 B12（latest.json + 下载 + sha256 + 确认安装）

**P5（第 8~11 周）— 分发与 macOS**
5.1 GitHub Actions 矩阵（windows + macos）｜ 5.2 构建 → 冒烟 → 打安装包 → 上传 Release ｜ 5.3 WebView2 检测与静默安装（Inno）｜ 5.4 签名与公证（需证书，无证书则出"下载说明页"过渡）｜ 5.5 DMG 打包（macOS）

**P6（第 12 周+）— 打磨发布**
6.1 A6 通知中心 ｜ 6.2 内存优化（嵌入模型懒加载/空闲释放）｜ 6.3 崩溃上报/日志导出完善 ｜ 6.4 v0.2.0 正式 release + 文档更新（README/USAGE）

---

## 8. 风险点与对策

| # | 风险 | 概率 | 影响 | 对策 |
|---|---|---|---|---|
| R1 | **关闭控制台后 stdout 为 None 导致启动即退** | 高 | 高 | K3 先落地并验证：全局清理 print；`log_config` 显式接管；P1 阶段先以 `console=True` 跑通全部路径，**最后一步**才切 `console=False` |
| R2 | WebView2 在老 Win10 缺失 | 中 | 高 | Inno 安装时检测注册表，缺失则静默装 Evergreen Bootstrapper；兜底 `--browser` 降级模式 |
| R3 | WebView2 下 SSE / 剪贴板 / localStorage 行为差异 | 中 | 中 | P0 专项验证（0.3）；localStorage 依赖 K1 端口粘性；失败降级到后端持久化 |
| R4 | uvicorn 重启触发 lifespan 重跑，scheduler 不可重入 | 中 | 中 | K2 明确重建实例；`start_scheduler` 加幂等保护（已有实例则先 stop）；看门狗重启前先 `stop()` |
| R5 | 端口粘性失效导致 localStorage 静默丢失 | 中 | 中 | K1 + P0 验证 storage 分区；关键状态（主题）考虑迁到后端配置 |
| R6 | PyInstaller 产物被杀软误报 | 高 | 中 | 签名证书（有预算）；误报申诉说明文档；分发页提供 sha256 |
| R7 | 双开写坏 SQLite | 低 | 高 | K6 单实例锁（首启即加锁）；WAL 降低损坏概率 |
| R8 | 数据迁移中途失败 | 低 | 高 | 只读复制 + 校验 + 原数据保留 + 可重试（R1 修正后优先级已下调，迁移失败可继续用旧目录） |
| R9 | 中文路径 / DPI / 高分屏问题 | 中 | 中 | P2 专项验证（中文安装路径、125%/150% 缩放）；沿用既有响应式断点经验 |
| R10 | macOS 无本机硬件，联调受限 | 高 | 中 | GitHub Actions macos runner 构建；本地仅做代码级评审；macOS 放 P5，不阻塞 Windows 交付 |
| R11 | 空闲检测误判（合盖/睡眠/虚拟机） | 中 | 中 | 唤醒补偿 + 电量感知 + 空闲时长可配（默认 5 分钟） |
| R12 | 自研托盘/更新器工期超预期（> 2 周） | 中 | 中 | 触发 §3.5 切换准则评估 Tauri；首版更新可只做"提示 + 跳转下载页"，把自动安装放 P4 |
| R13 | 依赖引入（pywebview/pystray）增大产物体积或引入新冻结坑 | 中 | 中 | 用 `packaging/venv-build` 专用干净 venv（沿用既有教训）；新增依赖加入 `requirements-build-desktop.txt`；冻结后立刻跑全量冒烟 |
| R14 | 前端 A4 外链/`target=_blank` 遗漏（10 处分散在 6 个页面） | 中 | 低 | webview 侧统一拦截新窗口事件（兜底）+ 前端统一 helper（主动），双保险 |

---

## 9. 验证方式

### 9.1 分层验证矩阵

| 层级 | 验证内容 | 手段 | 时机 |
|---|---|---|---|
| L1 单元/离线 | 端口粘性、令牌校验、单实例锁、路径三态、日志配置 | 新增 `test_desktop_*.py`（见 9.2） | 每次提交 |
| L2 既有回归 | 后端 32 个 `test_*.py` 全绿 | `python test_*.py` | P1/P2/P4 各跑一轮 |
| L3 前端契约 | `vue-tsc` 类型检查 + lint + openapi 类型生成一致 | `npm run typecheck / lint / gen:api` | 每次前端改动 |
| L4 构建冒烟 | 构建产物启动 → `/api/v1/health` 200 → 8 页面 API 200 → 关闭退出 | 脚本化冒烟（沿用 PACKAGING 冒烟清单） | 每次打包 |
| L5 真机场景 | 装/升/卸三连、自启、托盘、单实例、空闲触发、睡眠唤醒 | 人工 check-list（9.3） | 每阶段出口 |
| L6 资源实测 | 内存/CPU/启动耗时/产物体积 | 9.4 方法 | P0 + P6 |

### 9.2 建议新增的测试脚本

| 脚本 | 覆盖 |
|---|---|
| `test_desktop_port_sticky.py` | 端口粘性：首次分配 → 写入 → 二次启动复用；端口被占自动切换 |
| `test_desktop_single_instance.py` | 二次启动不产生第二个后端；唤起指令可达 |
| `test_desktop_logging.py` | 无 stdout 环境下 root logger 仍写文件；uvicorn 日志不覆盖 root |
| `test_desktop_token.py` | 无令牌 401 / 有令牌 200 / 开发模式关闭校验 |
| `test_desktop_lifecycle.py` | 关闭→隐藏（进程存活）；退出→后端停、调度停、锁释放 |
| `test_desktop_paths.py` | 三态路径解析（dev / portable / installed）+ 迁移幂等 |
| `test_desktop_wal.py` | WAL 开启后并发读写回归 |

### 9.3 桌面版验收 check-list（人工，阶段出口逐项勾验）

**启动与形态**
- [ ] 双击快捷方式启动，**无终端窗口**
- [ ] 无浏览器进程被拉起，内容在系统 WebView 窗口内
- [ ] 启动到首屏 ≤ 2s；后端 health 200
- [ ] 关闭按钮 → 窗口隐藏、进程常驻托盘
- [ ] 托盘菜单：打开主界面 / 暂停调度 / 检查更新 / 打开日志目录 / 退出 逐项可用
- [ ] 托盘"退出" → 进程完全消失，无残留线程
- [ ] 二次启动 → 唤起已有实例，不双开
- [ ] 窗口位置/大小重启后恢复

**功能一致性（8 页面逐页冒烟）**
- [ ] 通知浏览/筛选/分页 ｜ 待办中心 ｜ 智能问答（**SSE 流式输出正常**）｜ 订阅管理 ｜ 系统配置 ｜ 数据源中心（112 源）｜ 首页 Dashboard ｜ Token 用量
- [ ] 任务抽屉：抓取/批量提取/重匹配的实时进度正常（SSE）
- [ ] 外链点击 → 系统默认浏览器打开，不在 webview 内跳转
- [ ] 主题/QA 会话在**连续两次启动后**仍保持（验证 K1）

**健壮性**
- [ ] kill 后端线程 → 看门狗重启，前端自动恢复
- [ ] 制造未捕获异常 → 弹窗 + 日志落盘 + 一键导出
- [ ] 关机/注销时优雅退出，重启后数据无损坏
- [ ] 125% / 150% 缩放下无破版
- [ ] 中文安装路径下可正常启动与读写

**安装分发**
- [ ] 静默安装（无管理员）｜ 覆盖升级（配置备份、data 保留）｜ 卸载（程序清、data 保留、自启项清）

### 9.4 资源占用实测方法（P0 必做）

1. 基线：云端嵌入版后端单独启动（`python run_app.py` 不打开浏览器），稳定 3 分钟后记录 **私有工作集内存**（任务管理器 / `psutil`）；
2. 壳增量：pywebview demo 启动同样后端，记录同一指标，差值即壳增量；
3. 嵌入模型态：本地 bge 模型加载后再测一次，得到三态数据；
4. 启动耗时：从进程创建到 `/api/v1/health` 首次 200 的墙上时间（取 5 次中位数）；
5. 产物体积：`du -sh dist-desktop` 与 setup.exe 大小；
6. 记录环境（OS 版本、WebView2 版本、CPU/内存），写入 `docs-local/`（不入库）。

**达标线**：壳增量 ≤ 60 MB；空闲总内存 ≤ 180 MB；启动到首屏 ≤ 2s。超标则触发 §3.5 重新评估。

### 9.5 发布 gate（v0.2.0 可发布的最低条件）

1. L1~L4 全绿，L5 check-list 100% 勾验通过；
2. 32 个既有测试全绿（无一项因桌面化而跳过）；
3. 真机连续运行 7 天无 P0 崩溃（崩溃 = 需手动重启才能恢复）；
4. 资源实测达标（§9.4）；
5. 回滚方案就绪：保留 v0.1.0 在线版安装包，桌面版严重故障时可回退；
6. 文档齐备：USAGE 增桌面版章节（托盘说明、日志导出路径、卸载与数据保留策略）。

---

## 10. 待决策项（评审时拍板）

| # | 问题 | 建议 | 影响 |
|---|---|---|---|
| 1 | macOS 是否进入 v0.2.0 范围？ | **不进**，Windows 优先，macOS 放 v0.3.0 | 影响 P5 排期与是否需要 GitHub Actions macos runner |
| 2 | 桌面版默认嵌入模式？ | **默认云端**（省内存、启动快），设置可切本地 | 影响默认产物体积与离线可用性 |
| 3 | 更新确认方式？ | 首版**用户确认**（下载+校验后弹窗确认安装），不做静默 | 影响 B12 实现复杂度与用户打扰 |
| 4 | 是否保留浏览器降级模式？ | **保留** `--browser` 参数（webview 故障时可用浏览器访问） | 约 0.5 天成本，价值是排障与兼容性兜底 |
| 5 | 是否保留 `run_app.py` 在线版 flavor？ | **保留**（cloud/full 并存），桌面版为新增第三 flavor | 保证老用户与无 WebView2 环境可用 |
| 6 | 代码签名证书预算？ | 无证书期先用"sha256 + 下载说明页 + 误报申诉文档"过渡 | 影响 R6 杀软误报的严重程度 |
| 7 | 数据目录迁移（B13）是否在 v0.2.0 做？ | **P4 阶段做，但优先级 P1**（R1 修正）；若排期紧张可延到 v0.2.1 | 影响 P4 工作量与老用户升级体验 |
| 8 | 周报（LLM 摘要）默认开启？ | **默认关闭**，设置里可开（涉及每周固定 token 开销） | 影响 A6 与 LLM 成本 |

---

## 11. 与 PLAN.md 的差异说明

本文档以 PLAN.md 为上游依据，仅在其基础上做了**三处实质修正**（§2.4：R1 数据目录迁移优先级下调、R2 新增端口粘性/localStorage 约束、R3 路由模式无需改 hash）与**两处补充**：

1. **补充了"前端优化"这条主线**——PLAN.md 的 F7 只覆盖了桌面适配（外链/下载/DPI），未覆盖工程规范化（A1）、类型源收敛（A2）、加载性能（A3）。本方案将其补齐为主线 A，并与主线 B 建立依赖映射（A4 是 B 的硬前置，其余可并行）；
2. **补充了实现级避坑要点**（§6 K1~K9）——PLAN.md 是规划级文档，未涉及 uvicorn 子线程、stdout 为 None、端口粘性、lifespan 重跑等具体工程陷阱，这些恰恰是"关掉控制台"这一步最容易踩的坑。

**未变更部分**：技术选型结论（pywebview P0 / Tauri P1）、非目标、资源占用目标、阶段划分骨架、更新与分发策略、风险清单主体，均与 PLAN.md 保持一致。

---

*编制：基于 PLAN.md v0.1 + 代码现状调研（2026-09-01）｜ 评审通过后转为 v0.2.0 执行计划并回写 PLAN.md*
