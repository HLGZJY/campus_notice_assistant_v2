# 校园通知智能助手 v0.2.0 桌面版 · 分批开发计划

> 状态：待评审 ｜ 版本：0.1 ｜ 编制日期：2026-09-01
> 上游依据：[DESKTOP-UPGRADE.md](DESKTOP-UPGRADE.md)（v0.2.0 桌面版升级方案）
> 配套文档：[DESKTOP-ACCEPTANCE.md](DESKTOP-ACCEPTANCE.md)（验收手册 · Gate 细则与 check-list）
> 适用范围：Windows x64 桌面版从 v0.1.0 到 v0.2.0 的全部开发批次编排

---

## 0. 结论摘要

| 项 | 结论 |
|---|---|
| **批次划分** | 方案 6 个 Phase（P0~P6）拆解为 **B00~B22 共 23 个批次**，每批是「可独立开发 → 可独立验收 → 可独立回滚」的最小交付单元 |
| **平台范围（已决策）** | **只做 Windows x64**。macOS 不进入 v0.2.0（原决策项 1），对应 **B20 不排期**，相关工作量不再计入关键路径；macOS 留待 v0.3.0 具备硬件条件后再评估 |
| **工期** | 含 B20 约 38 个工作单元；**剔除 B20 后约 35 个工作单元**。Windows 可交付（B00~B12）≈ 19.5 单元 ≈ **6~7 周**，完整分发能力（到 B19）≈ 31.5 单元 ≈ **10~11 周**（与方案 §7.1 一致） |
| **关键路径** | B01 选型定案 → B03 壳骨架 → B08 生命周期 → B09 打包 → B12 真机冒烟，其中 **B09（console=False）是全局最高风险单点** |
| **验收模型** | 三层 Gate：**批次 Gate（23 个）→ 阶段 Gate（P0~P6 共 7 个）→ 发布 Gate（v0.2.0）**，任一层不通过不得进入下一层 |
| **质量底线** | 32 个既有 `test_*.py` 在 B08 / B12 / B17 / B22 各跑一轮全量，**无一项因桌面化而跳过**；新增 7 个 `test_desktop_*.py` 常驻 L1 |
| **最大风险对策** | R1（关闭控制台 stdout 为 None 启动即退）：B05 日志落盘先落地并验证，**B09 之前所有批次一律 `console=True`**，切 False 是 B09 的最后一步且可一键回退 |

---

## 1. 批次划分原则

### 1.1 四条原则

| # | 原则 | 说明 |
|---|---|---|
| 1 | **一个批次 = 一个可验收的增量** | 批次结束时应用必须处于「可运行状态」，不留半成品（避免跨批次的编译不过状态） |
| 2 | **高风险单点独立成批** | `console=False`、令牌中间件、WAL、数据迁移这类「改一处可能全盘崩」的改动单独成批，便于精准回滚 |
| 3 | **主线 A 与主线 B 交错推进** | A 侧内容优化（A1/A2/A3）与 B 侧壳建设并行；**A4 桌面适配必须在 B09 打包前完成**（硬前置） |
| 4 | **每批必有回滚点** | 每批开工前打 tag 或记录 base commit，批次内按原子任务增量提交，出问题可退到批次起点 |

### 1.2 批次粒度与编号

- 编号规则：`B{两位序号}`，序号按执行顺序递增，跨 Phase 连续（不按 Phase 分段编号，避免重排）。
- 粒度：单批 0.5~3 个工作单元（1 工作单元 ≈ 1 个有效工作日）。超过 3 单元的批次已在下方拆分。
- 批次内的原子任务编号：`B{nn}.T{k}`，作为提交信息引用标识。

### 1.3 优先级与取舍

- **P0 批次（B00~B12）**：不做完不发布，任何 P0 批次失败即阻塞后续。
- **P1 批次（B13~B18）**：可整体延后到 v0.2.1。**B16（数据目录三态与迁移）已确认保留在 v0.2.0**——虽当前无存量用户，但目录规范越晚做成本越高（后续每次升级都要额外兼容旧布局），属于「现在做最便宜」的前置投资。
- **不排期（B20）**：**macOS 构建与签名，本版本不做**。原因为无本机硬件、CI 可构建但无法联调（R10），且当前无 macOS 用户需求。相关 check-list（D-48/D-49）在验收手册中标记为「不适用」。

---

## 2. 批次总览

| 批次 | 名称 | Phase | 工作内容（方案编号） | 预估 | 前置 |
|---|---|---|---|---|---|
| **B00** | 环境与依赖准备 | — | 桌面依赖 venv、WebView2 核验、Inno/Node 工具链 | 0.5 | — |
| **B01** | 可行性实测与选型定案 | P0 | pywebview demo、资源实测、WebView2 兼容清单、评审定案 | 2.0 | B00 |
| **B02** | 前端工程规范化 | P0 | A1 typecheck/lint/engines、A2 类型源收敛、A3 懒加载（可选） | 1.5 | B00 |
| **B03** | 桌面壳骨架与后端托管 | P1 | B1 壳包 8 模块骨架、B2 uvicorn 子线程 | 2.0 | B01 |
| **B04** | 端口粘性与看门狗 | P1 | B3 端口粘性、B2 看门狗重启 | 1.5 | B03 |
| **B05** | 日志落盘与崩溃兜底 | P1 | B7 日志落盘、G1 补齐、excepthook + 导出 | 1.5 | B03 |
| **B06** | 托盘与窗口生命周期 | P1 | B5 托盘菜单、关闭最小化、窗口几何记忆 | 2.0 | B05 |
| **B07** | 单实例锁与唤起 | P1 | B6 socket 锁 + 唤起已有实例 | 1.0 | B06 |
| **B08** | 退出编排与全量回归 | P1 | K5 退出编排、关机信号、**32 测试首轮全量回归** | 1.5 | B07 |
| **B09** | 桌面 flavor 打包与无控制台 | P2 | B4 `desktop_main.py` + spec `console=False` + build flavor | 2.0 | B08, B02 |
| **B10** | 前端桌面适配 | P2 | A4 外链/剪贴板/DPI/localStorage | 2.0 | B02 |
| **B11** | 控制面路由与启动令牌 | P2 | B9 六端点、B10 令牌中间件、openapi 重导出 | 2.0 | B08 |
| **B12** | 真机装→跑→退冒烟 | P2 | L4/L5 全量验收，P2 阶段 Gate | 1.0 | B09, B10, B11 |
| **B13** | 自启开关与启动参数 | P3 | B8 HKCU Run、`--minimized/--autostart/--browser` | 1.0 | B11 |
| **B14** | 调度 pause/resume 与空闲触发 | P3 | B11、K9、空闲检测、电量感知 | 2.0 | B13 |
| **B15** | 桌面设置页 | P3 | A5 设置页桌面分组，对接 B13/B14 | 1.5 | B13 |
| **B16** | 数据目录三态与迁移向导 | P4 | B13 `app_paths` 三态、迁移向导（R1 修正） | 2.0 | B12 |
| **B17** | SQLite WAL 与自动备份 | P4 | B14、K8、并发回归、每周备份 | 1.0 | B16 |
| **B18** | 更新闭环 | P4 | B12 latest.json + 下载 + sha256 + 确认安装 | 2.5 | B12 |
| **B19** | CI 分发与 WebView2 检测 | P5 | 5.1~5.3 Actions 矩阵、冒烟、Bootstrapper 检测 | 2.0 | B12 |
| ~~**B20**~~ | ~~macOS 构建与签名~~ | ~~P5~~ | **不排期**（已决策：只做 Windows，macOS 留 v0.3.0） | — | — |
| **B21** | 打磨：通知中心与内存优化 | P6 | A6 通知中心、内存优化、崩溃上报完善 | 3.0 | B18 |
| **B22** | 发布 v0.2.0 | P6 | 稳定性观察、文档更新、Release Gate | 2.0 | B21 |

**里程碑口径**（便于对外承诺）

| 里程碑 | 包含批次 | 累计单元 | 折合周（按每周 3 批） |
|---|---|---|---|
| M1 Windows 可交付 | B00~B12 | 19.5 | ≈ 6~7 周 |
| M2 完整分发能力 | B00~B19 | 31.5 | ≈ 10~11 周 |
| M3 正式发布 | B00~B19 + B21 + B22 | **35.0** | ≈ 12~13 周 |

> B20（macOS）已移出计划，不计入任何里程碑。

---

## 3. 依赖关系

```
B00 环境准备
 ├──▶ B01 可行性实测 ──▶ [选型定案 Gate：不通过则触发 §3.5 切换准则，转 Tauri 重排]
 │         │
 │         └──▶ B03 壳骨架 ─┬─▶ B04 端口粘性+看门狗 ─┐
 │                          └─▶ B05 日志落盘 ─────────┤
 │                                                    ▼
 │                                    B06 托盘 ─▶ B07 单实例 ─▶ B08 退出编排+全量回归
 │                                                                      │
 ├──▶ B02 前端规范化 ─────────────────┬───────────────────────────────────┤
 │                                    ▼                                   ▼
 │                          B10 前端桌面适配(A4)              B09 打包(console=False)
 │                                    │                                   │
 │                                    └──────────┬────────────────────────┤
 │                                               ▼                        ▼
 │                                        B12 真机冒烟 ◀── B11 控制面路由+令牌
 │                                               │
 │        ┌──────────────────────────────────────┼──────────────────────────┐
 │        ▼                                      ▼                          ▼
 │   B13 自启 ─▶ B14 调度/空闲 ─▶ B15 设置页   B16 目录三态 ─▶ B17 WAL   B19 CI
 │                                              B18 更新闭环
 │                                                     │
 └──▶ B21 打磨（A6 通知中心/内存/崩溃上报）◀───────────┘
                          │
                          ▼
                     B22 发布 v0.2.0

（B20 macOS 已移出，不再出现在关键路径上）
```

**硬前置（不可违反）**

| 约束 | 原因 |
|---|---|
| B05 → B09 | 日志落盘是 `console=False` 的前置（R1） |
| B08 → B09 | 生命周期编排必须先跑通，否则无控制台后无法排障 |
| B10（A4）→ B12 | 桌面适配未完成则真机冒烟无意义 |
| B01 选型 Gate → B03 | 选型未定案不得投入壳建设 |
| B12 → B16/B18 | 迁移与更新必须建立在可运行的安装包之上 |

---

## 4. 环境准备（B00）

| 项 | 动作 | 验收 |
|---|---|---|
| B00.T1 | 建 `packaging/requirements-build-desktop.txt`，在 `packaging/venv-build` 中安装 `pywebview`、`pystray`（Pillow 应已随 newspaper 存在） | `pip freeze` 含两项，且原有 cloud flavor 构建仍成功 |
| B00.T2 | 核验本机 WebView2 运行时版本（注册表 `HKCU\Software\Microsoft\EdgeBLKMgr` 或 `msedgewebview2.exe` 版本） | 记录版本到 `docs-local/` |
| B00.T3 | 核验 Inno Setup（`ISCC.exe` 路径）、Node ≥ 20、Git 可用 | 三条命令均可执行 |
| B00.T4 | 建立验收记录目录 `docs-local/acceptance/`（不入库）与批次看板 | 目录存在 |
| B00.T5 | 建基线：全量跑 32 个 `test_*.py`，记录**基线通过清单** | 基线报告（后续回归以此为准，见 ACCEPTANCE §3） |
| B00.T6 | 建 `tools/run_regression.py`：收集 `test_*.py` 逐个执行，输出结构化结果并与基线比对，任一失败退出码非 0 | 回归统一入口 |

> **注意**：B00.T5 的基线是本计划所有「全量回归」的比较基准。若基线本身存在失败用例，必须先在 B00 内修复或单独记录为已知失败，否则后续回归无法判定责任。

---

## 5. 批次明细

> 每批含：目标 / 原子任务 / 提交序列 / Gate（自动化 + 人工）/ 产出 / 回滚点 / 风险。
> 人工 check-list 条目编号 `D-xx` 与 ACCEPTANCE.md 第 5 章一一对应。
> `<PY>` 表示项目 Python 解释器，`<NODE>` 表示 Node ≥ 20 环境。

### B01 · 可行性实测与选型定案（P0，2.0）

**目标**：用最小 demo 验证 pywebview 路线可行，产出实测数据，完成选型评审定案。

| 任务 | 内容 | 产出 |
|---|---|---|
| B01.T1 | 建 `scratch/webview_demo.py`：pywebview 窗口加载 `http://127.0.0.1:<port>`，起 uvicorn 托管现有 app | demo 脚本（`scratch/` 不入库） |
| B01.T2 | 跑通 8 页面 + SSE 流式问答 + 任务进度抽屉 | 冒烟报告 + 截图 |
| B01.T3 | 资源实测（方案 §9.4 六步）：后端基线 / 壳增量 / 嵌入模型态 / 启动耗时 5 次中位数 / 产物体积 | 实测数据表，写 `docs-local/` |
| B01.T4 | WebView2 兼容性：SSE、剪贴板、localStorage、125%/150% DPI、Win10 老版本检测 | 兼容清单 |
| B01.T5 | 选型评审定案，触发 §3.5 切换准则判断 | 评审结论（回写本计划与方案） |

**提交序列**：`chore(desktop): 桌面依赖与构建环境准备` → `docs(desktop): 可行性实测报告与选型评审结论`

**Gate B01（选型定案 Gate）**

| 类型 | 条件 |
|---|---|
| 自动 | demo 启动后 `/api/v1/health` 返回 200；8 页面 API 全部 200；SSE 流式输出有增量帧 |
| 度量 | 壳内存增量 ≤ 60 MB；空闲总内存 ≤ 180 MB；启动到 health 首次 200 ≤ 2s（5 次中位数） |
| 人工 | D-01 资源实测三态数据齐备 ｜ D-02 SSE 在 WebView2 下逐字输出 ｜ D-03 剪贴板可用 ｜ D-04 125%/150% 无破版 |
| 决策 | 三项度量**任一超标** → 触发 §3.5，评估转 Tauri，重排 B03 之后所有批次 |

**回滚点**：demo 在 `scratch/`，不入库，无回滚成本。

**风险**：R2（WebView2 缺失）、R3（SSE/剪贴板差异）、R13（新依赖体积）

---

### B02 · 前端工程规范化（P0，1.5）

**目标**：补齐 typecheck / lint / engines，建立前端质量门禁（方案 A1），并推进类型源收敛（A2）。

| 任务 | 内容 | 产出 |
|---|---|---|
| B02.T1 | `frontend/package.json` 增 `typecheck`（`vue-tsc --noEmit`）、`lint`（ESLint 9 flat + vue）、`engines.node >= 20`；`build` 前强制类型检查 | 三条脚本可跑 |
| B02.T2 | 修复 `typecheck` / `lint` 首轮报错（允许先 `warn` 后收敛，但 error 必须清零） | 错误清单 + 修复记录 |
| B02.T3 | A2：审计 `api/schema.ts` 与 `api/types.ts`（openapi 生成）重叠面，产出重叠清单与「openapi 单一事实源」规则；`schema.ts` 仅保留 SSE 事件与前端枚举 | 重叠清单 + 收敛规则 |
| B02.T4 | （可选，P1）A3：路由懒加载 + 产物体积归因；`dist` 1.8 MB 属健康量级，可延到 B21 | 体积归因表 |

**提交序列**：`chore(web): 补 typecheck/lint 脚本与 engines 约束` → `fix(web): 类型检查与 lint 首轮错误清零` → `refactor(web): 类型源收敛为 openapi 单一事实源`

**Gate B02**

| 类型 | 条件 |
|---|---|
| 自动 | `cd frontend && <NODE> npm run typecheck && npm run lint && npm run build` 全部退出码 0 |
| 自动 | `<NODE> npm run gen:api` 后 `git diff --stat src/api/types.ts` 无意外变更（确认单一事实源） |
| 人工 | D-05 三条脚本均可独立执行 ｜ D-06 `schema.ts` 与 `types.ts` 无重复定义 |

**回滚点**：`feat(web): 数据源中心...` 之后的 base commit；B02 只改前端，后端零影响。

---

### B03 · 桌面壳骨架与后端托管（P1，2.0）

**目标**：新建 `desktop/` 壳包 8 模块骨架，把 uvicorn 托管到 daemon 线程（方案 B1 + B2 前半）。

| 任务 | 内容 | 产出 |
|---|---|---|
| B03.T1 | 建 `desktop/` 包：`__init__.py`、`__main__.py`（`python -m desktop` 开发入口）、`app.py`（主控编排）、`config.py`（settings.json） | 壳包骨架 |
| B03.T2 | `server.py`：`uvicorn.Config` + `uvicorn.Server` 实例，在 daemon 线程 `server.run()`（**禁用 `uvicorn.run()`**，见 K2） | 后端托管模块 |
| B03.T3 | 启动序列：起后端 → 轮询 `/api/v1/health` 直到 200 → 创建 pywebview 窗口加载该端口 | 可启动的窗口 |
| B03.T4 | 退出：`server.should_exit=True` → `join(timeout=10)` → `sys.exit(0)`；预留 `scheduler.stop()` 与托盘停止位（B06/B08 填充） | 退出路径 |

**提交序列**：`feat(desktop): 桌面壳包骨架与 python -m desktop 开发入口` → `feat(desktop): uvicorn 托管到 daemon 线程与 health 就绪等待`

**Gate B03**

| 类型 | 条件 |
|---|---|
| 自动 | `<PY> -m desktop` 启动成功，窗口出现，`/api/v1/health` 200 |
| 自动 | 关闭窗口后进程退出（本批尚未做托盘，关闭即退出属预期）；无残留线程 |
| 自动 | `<PY> test_api_smoke.py` 通过（后端在线程内行为不变） |
| 人工 | D-07 `python -m desktop` 可启动且 8 页面可访问 ｜ D-08 退出后无残留进程 |

**回滚点**：删除 `desktop/` 目录即回到 v0.1.0 状态（`run_app.py` 未改动）。

**风险**：R4（lifespan 重跑 / scheduler 可重入）——B03 阶段先观察，B04 看门狗必须解决。

---

### B04 · 端口粘性与看门狗（P1，1.5）

**目标**：实现 K1 端口粘性，补上看门狗自动重启后端线程（B3 + B2 后半）。

| 任务 | 内容 | 产出 |
|---|---|---|
| B04.T1 | `server.py` 迁移 `run_app.py:33-46` 端口探测逻辑，新增「优先复用 `data/runtime.json` 上次端口，失败才顺序探测并写回」；写失败降级为随机端口（可容忍） | 端口粘性 |
| B04.T2 | 看门狗 daemon 线程：每 5s 探 `/api/v1/health`，连续 3 次失败 → 先 `scheduler.stop()`，再**重建 `Server` 实例**重启（K2：不可复用已返回实例） | 自恢复能力 |
| B04.T3 | `start_scheduler()` 加幂等保护（已有实例先 stop），解决 R4 | 可重入调度 |
| B04.T4 | 新增 `test_desktop_port_sticky.py`：首次分配→写入→二次启动复用；端口被占自动切换 | 单元测试 |

**提交序列**：`feat(desktop): 端口粘性（data/runtime.json）` → `feat(desktop): 后端看门狗与崩溃自恢复` → `fix(scheduler): start 幂等保护，支撑后端线程重建` → `test(desktop): 端口粘性与看门狗用例`

**Gate B04**

| 类型 | 条件 |
|---|---|
| 自动 | `<PY> test_desktop_port_sticky.py` 通过 |
| 自动 | 手动 kill 后端线程 → 看门狗 15s 内重启，health 恢复 200 |
| 自动 | `<PY> test_scheduler_integration.py` 通过（幂等改造无回归） |
| 人工 | D-09 连续两次启动端口一致 ｜ D-10 端口被占时自动切换且主题/会话不丢 ｜ D-11 制造后端崩溃后自动恢复 |

**回滚点**：`server.py` 端口逻辑可单独退回顺序探测（删 runtime.json 读取分支）。

**风险**：R4、R5（端口粘性失效导致 localStorage 丢失）

---

### B05 · 日志落盘与崩溃兜底（P1，1.5）★ 关键批次

**目标**：补齐 G1，让应用在**没有控制台**的情况下仍可排障。这是 B09 的硬前置。

| 任务 | 内容 | 产出 |
|---|---|---|
| B05.T1 | `desktop/logging_setup.py`：root logger 加 `RotatingFileHandler(data/logs/app.log, maxBytes=5MB, backupCount=3)`（复用 `scheduler.py:101` 已验证参数） | 日志落盘 |
| B05.T2 | uvicorn 侧显式传 `log_config`，把 `uvicorn` / `uvicorn.access` 指向同一 handler 且 `propagate=False`（K3：否则默认 dictConfig 覆盖 root） | uvicorn 日志接管 |
| B05.T3 | `console=False` 兼容：全局清理 `print()` 与 `basicConfig()` 到 stdout 的调用，重定向 `sys.stdout/stderr`（为 None 时） | 无 stdout 安全 |
| B05.T4 | `sys.excepthook` + `threading.excepthook` → 写日志 + 弹窗（含「导出日志」按钮） | 崩溃兜底 |
| B05.T5 | `test_desktop_logging.py`：无 stdout 环境下 root logger 仍写文件；uvicorn 日志不覆盖 root | 单元测试 |

**提交序列**：`feat(desktop): 日志落盘与 uvicorn log_config 接管` → `fix(desktop): 清理 stdout 依赖，兼容 console=False` → `feat(desktop): 未捕获异常钩子与日志导出` → `test(desktop): 无 stdout 环境日志用例`

**Gate B05**

| 类型 | 条件 |
|---|---|
| 自动 | `<PY> test_desktop_logging.py` 通过 |
| 自动 | `sys.stdout = None` 模拟下启动流程不抛异常（脚本化断言） |
| 自动 | 制造未捕获异常 → `data/logs/app.log` 出现完整 traceback |
| 人工 | D-12 无控制台下异常可落盘 ｜ D-13 一键导出日志可用 ｜ D-14 uvicorn 日志与 app 日志不重复、不互相覆盖 |

**回滚点**：`logging_setup.py` 为独立模块，可整体摘除。

**风险**：**R1（最高优先级风险）**——本批不通过则**禁止进入 B09**。

---

### B06 · 托盘与窗口生命周期（P1，2.0）

**目标**：pystray 托盘菜单、关闭最小化、窗口几何记忆（B5）。

| 任务 | 内容 | 产出 |
|---|---|---|
| B06.T1 | `tray.py`：pystray 图标 + 菜单（打开主界面 / 暂停调度 / 检查更新 / 打开日志目录 / 退出），`notify()` 气球通知 | 托盘模块 |
| B06.T2 | `window.closing` 事件 → `window.hide()` + 返回 False 取消关闭（K5） | 关闭最小化 |
| B06.T3 | `config.py`：窗口位置/大小写入 `settings.json`，启动时恢复；首次启动给默认几何 | 几何记忆 |
| B06.T4 | 托盘「退出」串联 B03 退出路径（完整编排在 B08） | 退出入口 |

**提交序列**：`feat(desktop): pystray 托盘与菜单` → `feat(desktop): 关闭最小化到托盘` → `feat(desktop): 窗口几何记忆与 settings.json`

**Gate B06**

| 类型 | 条件 |
|---|---|
| 自动 | 关闭窗口 → 进程仍存活且 health 200（脚本化：关闭后 curl health） |
| 自动 | 托盘退出 → 进程完全消失，无 `conhost`/子进程残留 |
| 人工 | D-15 托盘菜单五项逐项可用 ｜ D-16 窗口位置/大小重启后恢复 ｜ D-17 点 X 隐藏到托盘，进程常驻 |

**回滚点**：托盘模块可整体禁用（启动参数 `--no-tray`），窗口退回「关闭即退出」。

---

### B07 · 单实例锁与唤起（P1，1.0）

**目标**：K6 socket 端口锁，防双开写坏 SQLite（R7）。

| 任务 | 内容 | 产出 |
|---|---|---|
| B07.T1 | `single_instance.py`：启动绑定固定高位端口 51999；失败即判定已有实例 → 发送 `activate` → 自身退出 | 单实例锁 |
| B07.T2 | 主实例监听 socket，收到 `activate` → `window.restore() + show() + focus()` | 唤起 |
| B07.T3 | 端口被其他程序占用的兜底：Windows 侧 `msvcrt.locking` 文件锁，并记录日志 | 兜底路径 |
| B07.T4 | `test_desktop_single_instance.py`：二次启动不产生第二个后端；唤起指令可达 | 单元测试 |

**提交序列**：`feat(desktop): 单实例 socket 锁与已有实例唤起` → `test(desktop): 单实例与唤起用例`

**Gate B07**

| 类型 | 条件 |
|---|---|
| 自动 | `<PY> test_desktop_single_instance.py` 通过 |
| 人工 | D-18 二次启动不双开且主窗口被唤起 ｜ D-19 端口 51999 被占时走文件锁兜底且启动成功 |

**回滚点**：单实例模块独立，可开关关闭。

**风险**：R7

---

### B08 · 退出编排与全量回归（P1，1.5）★ 阶段 Gate

**目标**：K5 完整退出编排 + 关机信号，**跑 P1 阶段全量回归**。

| 任务 | 内容 | 产出 |
|---|---|---|
| B08.T1 | 退出序列：写窗口几何 → `server.should_exit=True` → join → `scheduler.stop()` → `pystray.stop()` → 释放单实例锁 → `sys.exit(0)` | 优雅退出 |
| B08.T2 | Windows `WM_QUERYENDSESSION`（关机/注销）走同一退出路径 | 关机安全 |
| B08.T3 | 退出前若有 running 任务 → 二次确认「有任务进行中，确认退出？」 | 数据保护 |
| B08.T4 | `test_desktop_lifecycle.py`：关闭→隐藏（进程存活）；退出→后端停、调度停、锁释放 | 单元测试 |
| B08.T5 | **全量回归第 1 轮**：32 个既有 `test_*.py` 逐个执行，与 B00 基线比对 | 回归报告 |

**提交序列**：`feat(desktop): 优雅退出编排与关机信号处理` → `feat(desktop): 有运行中任务的退出二次确认` → `test(desktop): 生命周期用例` → `test: P1 阶段全量回归（32 用例）`

**Gate B08（= P1 阶段 Gate）**

| 类型 | 条件 |
|---|---|
| 自动 | `<PY> test_desktop_lifecycle.py` 通过 |
| 自动 | **32 个既有测试全部通过，且与 B00 基线一致（无新增失败、无跳过）** |
| 自动 | 关机/注销模拟：数据目录无 `-journal` 残留、SQLite 完整性 `PRAGMA integrity_check` = ok |
| 人工 | D-20 托盘退出后进程完全消失 ｜ D-21 关机注销时优雅退出，重启后数据无损坏 ｜ D-22 有任务运行时退出有二次确认 |

**回滚点**：退回 B07 末 commit；壳层模块互不耦合，可逐块禁用。

---

### B09 · 桌面 flavor 打包与无控制台（P2，2.0）★ 最高风险

**目标**：`desktop_main.py` 入口 + spec `console=False` + `build.py --flavor desktop`（B4）。

| 任务 | 内容 | 产出 |
|---|---|---|
| B09.T1 | 建 `desktop_main.py`（与 `run_app.py` 并存）；`campus_notice.spec` 按 flavor 切换入口与 `console` 值 | 入口脚本 |
| B09.T2 | `build.py` 增 `--flavor desktop`，`dist-desktop` / `build-desktop` 工作目录，`CNA_FLAVOR=desktop` 环境变量 | 构建链路 |
| B09.T3 | **先以 `console=True` 构建一次并跑通全部路径**，确认无 stdout 崩溃后再切 `console=False` | 中间验证 |
| B09.T4 | 切 `console=False` 重建，验证无终端窗口、无浏览器进程 | 桌面版 exe |
| B09.T5 | Inno Setup 增 desktop flavor 分支，产出 `校园通知助手-桌面版-setup.exe` + sha256 | 安装包 |

**提交序列**：`feat(desktop): desktop_main.py 入口与 flavor 化 spec` → `feat(packaging): build.py 增 desktop flavor` → `feat(packaging): 桌面版 console=False 与安装包产出`

**Gate B09**

| 类型 | 条件 |
|---|---|
| 自动 | `packaging/venv-build/Scripts/python packaging/build.py --flavor desktop --innosetup` 全程退出码 0 |
| 自动 | 进程树检查：启动后任务管理器/conhost 计数为 0（`tasklist \| findstr conhost` 无新增） |
| 自动 | 无浏览器进程被拉起 |
| 自动 | 产物启动 → `/api/v1/health` 200 → 8 页面 API 200 → 关闭退出 |
| 人工 | D-23 双击快捷方式启动无终端窗口 ｜ D-24 安装包静默安装无需管理员 ｜ D-25 覆盖升级后配置备份、data 保留 |

**回滚点**：`campus_notice.spec` 的 `console` 值改回 `True` 即恢复可排障状态；`run_app.py` 全程未改动，在线版 flavor 不受影响。

**风险**：**R1**（本批是 R1 的最终验证点）、R6（杀软误报）、R9（中文路径）、R13（新依赖冻结坑）

---

### B10 · 前端桌面适配（P2，2.0）

**目标**：A4 桌面环境适配，是 B12 真机冒烟的硬前置。

| 任务 | 内容 | 产出 |
|---|---|---|
| B10.T1 | 外链统一走系统浏览器：`App.vue:52`、`ConfigView.vue:121` 的 `window.open` 及 6 页面 10 处 `target=_blank` → 统一 helper | 外链 helper |
| B10.T2 | webview 侧统一拦截新窗口事件（R14 兜底，双保险） | 兜底拦截 |
| B10.T3 | 剪贴板：`DataSourceCenterView.vue:559` 改用降级链（navigator.clipboard → execCommand） | 剪贴板可用 |
| B10.T4 | DPI：125% / 150% 缩放验证与样式修正 | 无破版 |
| B10.T5 | 验证 localStorage 在 WebView2 中可用且**跨重启保持**（依赖 B04 端口粘性；若 storage 分区漂移则固定 `--user-data-dir` 到应用数据目录） | 状态持久化 |

**提交序列**：`feat(web): 外链统一走系统浏览器 helper` → `feat(desktop): 拦截 webview 新窗口事件兜底` → `fix(web): 剪贴板降级链与 DPI 缩放适配` → `fix(web): 固定 user-data-dir 修复 storage 分区漂移`

**Gate B10**

| 类型 | 条件 |
|---|---|
| 自动 | `cd frontend && <NODE> npm run typecheck && npm run lint && npm run build` 退出码 0 |
| 人工 | D-26 外链点击走系统默认浏览器 ｜ D-27 125%/150% 缩放无破版 ｜ D-28 主题与 QA 会话连续两次启动后仍保持 ｜ D-29 剪贴板复制可用 |

**回滚点**：前端改动可整批 revert；外链 helper 与兜底拦截两项互不依赖。

**风险**：R3、R5、R14

---

### B11 · 控制面路由与启动令牌（P2，2.0）

**目标**：`/api/v1/desktop/*` 六端点 + K7 启动令牌校验（B9 + B10）。

| 任务 | 内容 | 产出 |
|---|---|---|
| B11.T1 | 新增 `api/routes/desktop.py`：`/status`、`/autostart`、`/open-log-dir`（P0）与 `/scheduler`、`/restart-backend`、`/quit`（P1）共 6 端点（§4.3） | 控制面路由 |
| B11.T2 | openapi 重新导出 + `npm run gen:api`，前端类型同步 | 类型一致 |
| B11.T3 | K7 启动令牌：每次启动 `secrets.token_urlsafe(32)`，webview 初始化脚本注入 `window.__CNA_TOKEN__`，前端 http client 统一加 `X-Desktop-Token` 头 | 令牌注入 |
| B11.T4 | 后端中间件：仅对 `/api/v1/*` 且 `127.0.0.1` 生效；**开发模式与 `--browser` 降级模式关闭校验**（避免影响 dev 流程与测试脚本） | 校验中间件 |
| B11.T5 | `test_desktop_token.py`：无令牌 401 / 有令牌 200 / 开发模式关闭校验 | 单元测试 |

**提交序列**：`feat(api): 桌面控制面路由 /api/v1/desktop/*` → `feat(api): 启动令牌注入与校验中间件` → `chore(web): openapi 重新导出与类型生成` → `test(desktop): 令牌校验用例`

**Gate B11**

| 类型 | 条件 |
|---|---|
| 自动 | `<PY> test_desktop_token.py` 通过 |
| 自动 | 6 个端点逐个 curl 验证返回符合契约 |
| 自动 | **32 个既有测试仍全绿**（中间件不得误伤既有端点） |
| 人工 | D-30 无令牌请求被拒（401）｜ D-31 开发模式与 `--browser` 模式不受令牌影响 |

**回滚点**：中间件可整体关闭（配置项）；路由模块可摘除（前端对应 UI 未上线前无依赖）。

---

### B12 · 真机装→跑→退冒烟（P2，1.0）★ 阶段 Gate

**目标**：P2 阶段 Gate，L4/L5 全量验收，确认 Windows 桌面版可交付。

| 任务 | 内容 | 产出 |
|---|---|---|
| B12.T1 | 脚本化冒烟：装 → 启动（无控制台）→ `/api/v1/health` 200 → 8 页面 API 200 → 关闭退出 | 冒烟脚本 |
| B12.T2 | 真机 check-list 全量勾验（ACCEPTANCE §5 的 D-01~D-31 中与 P2 相关项） | 勾验记录 |
| B12.T3 | 装/升/卸三连：静默安装（无管理员）→ 覆盖升级（配置备份、data 保留）→ 卸载（程序清、data 保留、自启项清） | 三连报告 |
| B12.T4 | **全量回归第 2 轮**：32 个既有测试（打包产物内跑） | 回归报告 |

**提交序列**：`test(desktop): 打包产物冒烟脚本` → `docs(desktop): P2 阶段验收记录与装升卸三连报告`

**Gate B12（= P2 阶段 Gate = 里程碑 M1）**

| 类型 | 条件 |
|---|---|
| 自动 | L1 新增 `test_desktop_*.py` 全绿 ｜ L2 32 个既有测试全绿 ｜ L3 前端 typecheck/lint/gen:api 全绿 ｜ L4 脚本化冒烟全绿 |
| 人工 | L5 check-list 100% 勾验通过（D-01~D-31 全数） |
| 度量 | 资源实测达标（§9.4）：壳增量 ≤ 60 MB、空闲总内存 ≤ 180 MB、首屏 ≤ 2s |
| 安全 | 回滚方案就绪：v0.1.0 在线版安装包仍可获取，桌面版严重故障可回退 |

**里程碑产出**：**Windows 桌面版可用安装包**（v0.2.0-rc1）

---

### B13 · 自启开关与启动参数（P3，1.0）

**目标**：B8 开机自启应用内开关 + 启动参数。

| 任务 | 内容 | 产出 |
|---|---|---|
| B13.T1 | `autostart.py`：Win 写/删 `HKCU\...\Run`（复用 `campus_notice.iss:57,74-76` 已有落点） | 自启模块 |
| B13.T2 | 对接 B11 的 `/desktop/autostart` 端点，应用内开关即时生效 | 开关联动 |
| B13.T3 | 启动参数 `--minimized`（静默到托盘）、`--autostart`（自启场景标记）、`--browser`（降级用浏览器） | 启动参数 |
| B13.T4 | `--browser` 降级模式：跳过 webview，沿用 `run_app.py:49-60` 拉浏览器（决策项 4 保留） | 降级通道 |

**提交序列**：`feat(desktop): 开机自启应用内开关（HKCU Run）` → `feat(desktop): 启动参数 --minimized/--autostart/--browser`

**Gate B13**

| 人工/自动 | 条件 |
|---|---|
| 自动 | 切换开关后注册表值正确增删（脚本读取校验） |
| 人工 | D-32 注销重登后自动驻留托盘 ｜ D-33 启动参数三项均生效 ｜ D-34 `--browser` 降级可用 |

---

### B14 · 调度 pause/resume 与空闲触发（P3，2.0）

**目标**：B11 调度 pause/resume（K9）+ 空闲检测触发重活（补 G5）。

| 任务 | 内容 | 产出 |
|---|---|---|
| B14.T1 | `scheduler.py` 新增 `pause()` / `resume()`（`scheduler.py:236-238` 现有仅 start/stop），经 `/desktop/scheduler` 暴露 | 调度控制 |
| B14.T2 | `idle.py`：Win `GetLastInputInfo`（ctypes）空闲检测，约 30 行 | 空闲检测 |
| B14.T3 | 重活 gate：空闲 5 分钟（可配）才跑抓取/批量提取；检测到输入 1s 内挂起 | 重活调度 |
| B14.T4 | 电量/睡眠感知：避免合盖、睡眠、虚拟机下误判（R11 唤醒补偿） | 误判防护 |

**提交序列**：`feat(scheduler): pause/resume 与控制面端点对接` → `feat(desktop): 空闲检测与重活 gate` → `fix(desktop): 睡眠唤醒补偿与电量感知`

**Gate B14**

| 类型 | 条件 |
|---|---|
| 自动 | `<PY> test_scheduler_integration.py` 通过（pause/resume 无回归） |
| 人工 | D-35 空闲 5 分钟触发重活 ｜ D-36 动鼠标后 1s 内挂起 ｜ D-37 睡眠唤醒后不误判 |

**风险**：R11

---

### B15 · 桌面设置页（P3，1.5）

**目标**：A5 设置页「桌面」分组，把 B13/B14 能力暴露给用户。

| 任务 | 内容 | 产出 |
|---|---|---|
| B15.T1 | 设置页新增「桌面」分组：开机自启 / 启动方式 / 关闭行为 / 最小化到托盘 / 日志导出 / 检查更新 | 设置页 UI |
| B15.T2 | 调用控制面端点（B11）持久化到 `settings.json`，开关即时生效 | 状态联动 |
| B15.T3 | 前端契约检查：typecheck / lint / gen:api 一致 | 前端质量 |

**提交序列**：`feat(web): 设置页桌面分组（自启/启动方式/关闭行为）` → `feat(web): 日志导出与检查更新入口`

**Gate B15**

| 类型 | 条件 |
|---|---|
| 自动 | `npm run typecheck && npm run lint && npm run build` 退出码 0 |
| 人工 | D-38 桌面设置项开关即时生效并持久化 ｜ D-39 重启后设置保持 |

---

### B16 · 数据目录三态与迁移向导（P4，2.0）

**目标**：B13 `app_paths` 三态改造 + 老数据迁移（R1 修正，优先级 P1）。

> **已决策保留（2026-09-01）**：当前虽无存量用户，但**目录规范越晚做，兼容成本越高**——每拖一个版本，后续升级就要多兼容一种历史布局。现在做是「成本最低的前置投资」，故保留在 v0.2.0。

| 任务 | 内容 | 产出 |
|---|---|---|
| B16.T1 | `utils/app_paths.py:18-45` 扩为三态：`dev`（源码）/ `portable`（exe 同级 data，向后兼容）/ `installed`（`%APPDATA%`） | 三态路径 |
| B16.T2 | 迁移向导：只读复制 + 校验 + 标记，原数据保留，可重试（R8） | 迁移向导 |
| B16.T3 | `test_desktop_paths.py`：三态解析 + 迁移幂等 | 单元测试 |

**提交序列**：`refactor(paths): app_paths 三态改造（dev/portable/installed）` → `feat(desktop): 老数据迁移向导（只读复制+校验）` → `test(desktop): 路径三态与迁移幂等用例`

**Gate B16**

| 类型 | 条件 |
|---|---|
| 自动 | `<PY> test_desktop_paths.py` 通过 |
| 自动 | 老版本（exe 同级 data）升级后数据条数一致、向量库可用 |
| 人工 | D-40 老用户 data 平滑迁移，原数据保留 ｜ D-41 迁移失败可回退旧目录继续用 |

**风险**：R8、R9

---

### B17 · SQLite WAL 与自动备份（P4，1.0）

**目标**：B14 开 WAL（K8）+ 每周自动备份 + **全量回归第 3 轮**。

| 任务 | 内容 | 产出 |
|---|---|---|
| B17.T1 | `storage/db.py:321` 连接初始化加 `PRAGMA journal_mode=WAL; PRAGMA synchronous=NORMAL;` | WAL 开启 |
| B17.T2 | 每周自动备份，保留 3 份；备份脚本需处理 `-wal` / `-shm` 文件 | 备份机制 |
| B17.T3 | `test_desktop_wal.py` + `test_db_concurrency.py` 并发回归 | 并发验证 |
| B17.T4 | **全量回归第 3 轮**：32 个既有测试 | 回归报告 |

**提交序列**：`perf(db): SQLite 开启 WAL 与 synchronous=NORMAL` → `feat(desktop): 每周自动备份（保留 3 份，含 WAL 文件）` → `test(desktop): WAL 并发回归` → `test: P4 阶段全量回归（32 用例）`

**Gate B17**

| 类型 | 条件 |
|---|---|
| 自动 | `<PY> test_desktop_wal.py`、`test_db_concurrency.py`、`test_vectorstore_singleton.py` 全绿 |
| 自动 | 32 个既有测试全绿，与基线一致 |
| 人工 | D-42 托盘后台跑调度时前端轮询不卡顿 ｜ D-43 备份可恢复 |

**风险**：R7、R8

---

### B18 · 更新闭环（P4，2.5）

**目标**：B12 latest.json + 下载 + sha256 + 确认安装（决策项 3：用户确认，不静默）。

| 任务 | 内容 | 产出 |
|---|---|---|
| B18.T1 | Release 增 `latest.json` 清单（版本、下载地址、sha256、更新说明） | 更新清单 |
| B18.T2 | `updater.py`：下载 → sha256 校验 → 弹窗确认 → 调用安装包 → 退出当前实例 | 更新器 |
| B18.T3 | 保留 `update_service.py:26-59` 原契约（未配 repo 时恒 200、静默失败） | 契约保全 |
| B18.T4 | 端到端：发布测试 tag → 应用内完成一次完整升级 | 升级验证 |

**提交序列**：`feat(desktop): latest.json 更新清单与下载校验` → `feat(desktop): 更新确认安装与重启` → `test(desktop): 更新闭环端到端`

**Gate B18**

| 类型 | 条件 |
|---|---|
| 自动 | 未配 repo 时 `/api/v1/update/check` 恒 200（原契约不破） |
| 人工 | D-44 发布新 tag 后可完成一次完整升级 ｜ D-45 sha256 校验失败时中止并提示 |

**风险**：R12（自研更新器工期超预期 → 首版降级为「提示 + 跳转下载页」）

---

### B19 · CI 分发与 WebView2 检测（P5，2.0）

**目标**：GitHub Actions 矩阵 + 冒烟 + WebView2 检测与静默安装（5.1~5.3）。

| 任务 | 内容 | 产出 |
|---|---|---|
| B19.T1 | Actions 矩阵：windows-latest 构建 → 冒烟 → 打安装包 → 上传 Release | CI 流水线 |
| B19.T2 | 发布 SOP 更新（沿用 PACKAGING.md，ASCII 命名附件） | SOP |
| B19.T3 | Inno 安装时检测 WebView2 注册表，缺失则静默装 Evergreen Bootstrapper（R2） | 运行时检测 |
| B19.T4 | 分发页：sha256 + 下载说明 + 误报申诉说明（R6 无证书过渡方案） | 分发页 |

**提交序列**：`ci(desktop): Actions 构建矩阵与产物冒烟` → `feat(packaging): WebView2 检测与 Evergreen 静默安装` → `docs(packaging): 分发页 sha256 与误报申诉说明`

**Gate B19（= P5 阶段 Gate = 里程碑 M2）**

| 类型 | 条件 |
|---|---|
| 自动 | CI 流水线跑通，产物可下载 |
| 人工 | D-46 干净机（无 WebView2）安装后可直接运行 ｜ D-47 分发页 sha256 与实际产物一致 |

---

### ~~B20 · macOS 构建与签名~~ · 不排期

**决策结论（2026-09-01）：v0.2.0 只做 Windows x64，macOS 不做。**

| 项 | 说明 |
|---|---|
| 原定内容 | 5.4~5.5 macOS runner 构建 WKWebView 版、DMG 打包、签名与公证 |
| 不做的理由 | 无本机硬件无法联调（R10）；GitHub Actions 可构建但只能验证"能编出来"，无法验证"能跑起来"；当前无 macOS 用户需求，投入产出比不成立 |
| 对计划的影响 | 从关键路径移除，M3 由 38.0 降为 **35.0** 工作单元；验收手册中 D-48 / D-49 标记为**不适用** |
| 遗留要求 | **架构上仍保留壳可替换边界**（方案 §3.5）：`desktop/` 与后端只通过「uvicorn 起停」与 `/api/v1/desktop/*` 两个接口耦合，pywebview 在 macOS 走 WKWebView，未来接入时只需补窗口/托盘的平台分支，不需要动后端 |
| 重启条件 | v0.3.0 若具备 macOS 硬件或明确需求，按本文档格式新增批次，复用 B19 的 CI 基础设施 |

---

### B21 · 打磨：通知中心与内存优化（P6，3.0）

**目标**：A6 通知中心 + 内存优化 + 崩溃上报完善。

| 任务 | 内容 | 产出 |
|---|---|---|
| B21.T1 | A6 通知中心：未读徽标 + 通知列表（更新摘要/周报/提醒），已读持久化 | 通知中心 |
| B21.T2 | 内存优化：嵌入模型懒加载 / 空闲释放 | 内存下降 |
| B21.T3 | 崩溃上报与日志导出完善 | 可观测性 |
| B21.T4 | （若 B02.T4 未做）A3 路由懒加载与产物体积归因 | 加载性能 |

**提交序列**：`feat(web): 通知中心（未读徽标与已读持久化）` → `perf(desktop): 嵌入模型懒加载与空闲释放` → `feat(desktop): 崩溃上报与日志导出完善`

**Gate B21**

| 类型 | 条件 |
|---|---|
| 自动 | 前端 typecheck/lint/build 全绿；32 个既有测试全绿 |
| 度量 | 空闲总内存较 B01 实测不劣化 |
| 人工 | D-50 通知徽标与已读状态正确 ｜ D-51 内存优化后长跑 24h 无增长 |

---

### B22 · 发布 v0.2.0（P6，2.0）★ 发布 Gate

**目标**：稳定性观察 + 文档齐备 + 正式 Release。

| 任务 | 内容 | 产出 |
|---|---|---|
| B22.T1 | 真机连续运行 7 天无 P0 崩溃（崩溃 = 需手动重启才能恢复） | 稳定性报告 |
| B22.T2 | **全量回归第 4 轮**：32 个既有测试 + 7 个 `test_desktop_*.py` | 最终回归报告 |
| B22.T3 | 文档：USAGE 增桌面版章节（托盘说明、日志导出路径、卸载与数据保留策略）、README 更新、VERSION 升 0.2.0 | 文档 |
| B22.T4 | Release：tag v0.2.0 + 安装包附件 + sha256 + 更新日志 | Release |

**Gate B22（= 发布 Gate，逐条对应方案 §9.5）**

| # | 条件 |
|---|---|
| 1 | L1~L4 全绿，L5 check-list **100%** 勾验通过 |
| 2 | 32 个既有测试全绿，**无一项因桌面化而跳过** |
| 3 | 真机连续运行 7 天无 P0 崩溃 |
| 4 | 资源实测达标（§9.4） |
| 5 | 回滚方案就绪：v0.1.0 在线版安装包可获取 |
| 6 | 文档齐备：USAGE 桌面版章节 + README + Release Notes |

---

## 6. 批次看板（进度追踪）

> 每批完成后更新本表，并在 `docs-local/acceptance/B{xx}-验收记录.md` 归档验收记录。
> 状态：`待开始` / `进行中` / `待验收` / `已通过` / `阻塞`

| 批次 | 名称 | 状态 | base commit | 结束 commit | 单元 | 验收结论 | 记录 |
|---|---|---|---|---|---|---|---|
| B00 | 环境与依赖准备 | 已通过 | dbec3fa | ce439e9 | 0.5 | 6 项任务全部通过，基线 33 过/6 跳/0 败 | [B00-验收记录](docs-local/acceptance/B00-验收记录.md)（不入库） |
| B01 | 可行性实测与选型定案 | 待开始 | — | — | 2.0 | — | — |
| B02 | 前端工程规范化 | 待开始 | — | — | 1.5 | — | — |
| B03 | 桌面壳骨架与后端托管 | 待开始 | — | — | 2.0 | — | — |
| B04 | 端口粘性与看门狗 | 待开始 | — | — | 1.5 | — | — |
| B05 | 日志落盘与崩溃兜底 | 待开始 | — | — | 1.5 | — | — |
| B06 | 托盘与窗口生命周期 | 待开始 | — | — | 2.0 | — | — |
| B07 | 单实例锁与唤起 | 待开始 | — | — | 1.0 | — | — |
| B08 | 退出编排与全量回归 | 待开始 | — | — | 1.5 | — | — |
| B09 | 桌面 flavor 打包与无控制台 | 待开始 | — | — | 2.0 | — | — |
| B10 | 前端桌面适配 | 待开始 | — | — | 2.0 | — | — |
| B11 | 控制面路由与启动令牌 | 待开始 | — | — | 2.0 | — | — |
| B12 | 真机装跑退冒烟 | 待开始 | — | — | 1.0 | — | — |
| B13 | 自启开关与启动参数 | 待开始 | — | — | 1.0 | — | — |
| B14 | 调度 pause/resume 与空闲触发 | 待开始 | — | — | 2.0 | — | — |
| B15 | 桌面设置页 | 待开始 | — | — | 1.5 | — | — |
| B16 | 数据目录三态与迁移向导 | 待开始 | — | — | 2.0 | — | — |
| B17 | SQLite WAL 与自动备份 | 待开始 | — | — | 1.0 | — | — |
| B18 | 更新闭环 | 待开始 | — | — | 2.5 | — | — |
| B19 | CI 分发与 WebView2 检测 | 待开始 | — | — | 2.0 | — | — |
| ~~B20~~ | ~~macOS 构建与签名~~ | **不排期** | — | — | — | 已决策：只做 Windows | — |
| B21 | 打磨：通知中心与内存优化 | 待开始 | — | — | 3.0 | — | — |
| B22 | 发布 v0.2.0 | 待开始 | — | — | 2.0 | — | — |

---

## 7. 提交与工作日志规范

### 7.1 提交信息格式

沿用现有仓库约定（中文 + type 前缀）：

```
<type>(<scope>): <中文简述>

[可选正文：动机与影响面]
[可选尾注：Refs B{nn}.T{k}]
```

- `type`：`feat` / `fix` / `refactor` / `perf` / `test` / `docs` / `chore` / `ci`
- `scope`：`desktop` / `web` / `api` / `scheduler` / `db` / `packaging` / `paths`
- 一个原子任务一次提交；**禁止把多个批次的任务混在一次提交里**（保证可按批次回滚）

### 7.2 每批必做的三件事

1. 开工前记录 base commit（写入看板表）；
2. 批次内按 `B{nn}.T{k}` 增量提交，不跨批；
3. 结束后在 `docs-local/acceptance/B{xx}-验收记录.md` 归档：环境、commit 区间、Gate 逐条结果、缺陷清单、结论。

### 7.3 工作日志

追加到 `docs-local/` 的当周工作日志（不入库），每条含：日期 / 批次 / 完成事项 / 验证结果 / 遗留问题 / 下批计划。

---

## 8. 与 DESKTOP-UPGRADE.md 的映射与偏差

### 8.1 方案任务 → 批次映射（无遗漏校验）

| 方案编号 | 任务 | 批次 |
|---|---|---|
| A1 | 前端工程规范化 | B02.T1~T2 |
| A2 | 类型源收敛 | B02.T3 |
| A3 | 构建与加载性能 | B02.T4（可延 B21.T4） |
| A4 | 桌面环境适配 | B10 |
| A5 | 桌面专属设置页 | B15 |
| A6 | 通知中心 | B21.T1 |
| B1 | desktop 壳包 | B03.T1 |
| B2 | uvicorn 托管 + 看门狗 | B03.T2 / B04.T2 |
| B3 | 端口粘性 | B04.T1 |
| B4 | 打包改造 | B09 |
| B5 | 托盘 + 关闭最小化 + 几何记忆 | B06 |
| B6 | 单实例锁 | B07 |
| B7 | 日志落盘 + 导出 | B05 |
| B8 | 自启开关 + 启动参数 | B13 |
| B9 | 控制面路由 | B11.T1 |
| B10 | 启动令牌 | B11.T3~T4 |
| B11 | 调度 pause/resume + 空闲触发 | B14 |
| B12 | 更新闭环 | B18 |
| B13 | 数据目录三态 + 迁移向导 | B16 |
| B14 | SQLite WAL | B17.T1 |
| G1~G6 | 六处缺口 | G1→B05、G2→B09、G3→B09、G4→B03~B08、G5→B14、G6→B17 |
| K1~K9 | 避坑要点 | 全部落入对应批次，见各批任务 |
| P5 5.1~5.5 | 分发与 macOS | B19 / B20 |
| P6 6.1~6.4 | 打磨发布 | B21 / B22 |
| 自动备份（P4 4.4） | 每周备份保留 3 份 | B17.T2 |

### 8.2 对方案的三点执行层补充

| # | 补充 | 理由 |
|---|---|---|
| 1 | 新增 **B00 环境准备批**，并把「32 测试基线」固化为 B00.T5 | 方案默认基线已存在，但无基线记录则后续三轮回归无法判定责任 |
| 2 | 把 **B09 拆为「先 console=True 跑通 → 再切 False」两步**（B09.T3→T4） | 落实 R1 对策：最后一步才切 False，且失败可一键回退 |
| 3 | 全量回归**明确排 4 轮**（B08 / B12 / B17 / B22），而不是「各阶段跑一轮」的模糊表述 | 让回归成为可追踪的关卡，而不是随时可跳过的动作 |

### 8.3 待决策项对排期的影响

| 决策项 | 结论（2026-09-01） | 影响 |
|---|---|---|
| **1 macOS 是否进 v0.2.0** | **不进，只做 Windows**。B20 移出关键路径 | M3 由 38.0 降为 35.0 单元；D-48/D-49 转不适用 |
| **7 数据目录迁移（B16）是否做** | **做，保留在 v0.2.0**。当前虽无存量用户，但目录规范越晚做兼容成本越高 | B16 保留；M3 维持 35.0 单元 |
| 3 更新确认方式 | 未决（倾向用户确认，不静默） | 影响 B18 复杂度 |
| 4 保留 `--browser` 降级 | 未决（建议保留，约 0.5 天成本，价值是排障兜底） | B13.T4 |
| 6 签名证书预算 | 未决（无证书期走 sha256 + 下载说明页 + 误报申诉） | 影响 R6 与 B19 |

---

*编制：基于 DESKTOP-UPGRADE.md v0.1（2026-09-01）｜ 评审通过后作为 v0.2.0 执行计划，验收细则见 DESKTOP-ACCEPTANCE.md*
