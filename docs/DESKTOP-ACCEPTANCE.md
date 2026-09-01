# 校园通知智能助手 v0.2.0 桌面版 · 验收手册

> 状态：待评审 ｜ 版本：0.1 ｜ 编制日期：2026-09-01
> 上游依据：[DESKTOP-UPGRADE.md](DESKTOP-UPGRADE.md) §9 验证方式 ｜ 配套：[DESKTOP-BATCH-PLAN.md](DESKTOP-BATCH-PLAN.md) 分批开发计划
> 定位：把方案里的「验证方式」章节变成**可复制执行的命令 + 可勾选的清单 + 可归档的记录**

---

## 0. 结论摘要

| 项 | 结论 |
|---|---|
| **验收模型** | 三层：**批次 Gate（B-Gate，23 个）→ 阶段 Gate（P-Gate，7 个）→ 发布 Gate（R-Gate，v0.2.0）**，逐层放行，不得跳层 |
| **平台范围** | **只做 Windows x64**（已决策）。macOS 相关 D-48 / D-49 标记为**不适用**，不计入通过率 |
| **自动化覆盖** | L1 新增 7 个 `test_desktop_*.py`（**骨架已落地，未实现前自动 SKIP 不阻断回归**）；L2 既有 32 个 `test_*.py`；L3 前端 typecheck/lint/gen:api；L4 打包冒烟 `tools/smoke_desktop.py`；统一入口 `tools/run_regression.py` |
| **人工覆盖** | 51 条编号（D-01~D-51），其中 **D-48 / D-49 不适用**，实际执行 49 条；按批次分配，阶段 Gate 与发布 Gate 全量复验 |
| **质量底线** | 32 个既有测试在 B08 / B12 / B17 / B22 各跑一轮，**无一项因桌面化而跳过**；任一既有测试失败即判定该批次不通过 |
| **缺陷规则** | P0/P1 阻断批次通过，P2 可带入下批但发布前清零，P3 不阻断 |
| **不通过处理** | 批次 Gate 不通过 → 原地修复并重跑全批 Gate（不接受「部分通过」） |

---

## 1. 三层 Gate 模型

```
L1 单元 ─┐
L2 回归 ─┤
L3 前端 ─┼─▶ 批次 Gate（B-Gate）──▶ 阶段 Gate（P-Gate）──▶ 发布 Gate（R-Gate）
L4 冒烟 ─┤        23 个               7 个                  1 个
L5 人工 ─┤
L6 资源 ─┘
```

| 层 | 名称 | 谁触发 | 通过判定 | 不通过后果 |
|---|---|---|---|---|
| **B-Gate** | 批次 Gate | 每批结束时 | 该批所有自动项退出码 0 + 该批 D-xx 全勾 + 无 P0/P1 遗留 | 不得开始下一批（P0 批次）或记录延期（P1 批次） |
| **P-Gate** | 阶段 Gate | B01 / B08 / B12 / B19（及 B21 末） | 阶段内所有 B-Gate 通过 + 全量回归轮次通过 + 度量达标 | 不得进入下一 Phase |
| **R-Gate** | 发布 Gate | B22 结束时 | 方案 §9.5 六条全满足（见本手册第 8 章） | 不得发布 v0.2.0 |

**阶段 Gate 与批次的对应**

| 阶段 Gate | 等价批次 Gate | 额外条件 |
|---|---|---|
| **P0 Gate** | B01（选型定案 Gate） | 资源实测三项达标；不达标触发 §3.5 切换准则 |
| **P1 Gate** | B08 | 全量回归第 1 轮通过 |
| **P2 Gate** | B12（= 里程碑 M1） | 全量回归第 2 轮 + L5 的 D-01~D-31 全勾 + 装升卸三连 |
| **P3 Gate** | B15（P3 末） | D-32~D-39 全勾 |
| **P4 Gate** | B18（P4 末） | 全量回归第 3 轮（在 B17）+ D-40~D-45 全勾 |
| **P5 Gate** | B19（= 里程碑 M2） | CI 跑通 + D-46~D-47（**macOS 已决定不做**，D-48/D-49 不适用） |
| **P6 Gate** | B22（= R-Gate） | 见第 8 章 |

---

## 2. 缺陷分级与阻断规则

| 级别 | 定义 | 示例 | 阻断规则 |
|---|---|---|---|
| **P0 致命** | 应用无法启动 / 启动即退 / 数据损坏 / 核心功能不可用 / 需手动重启才能恢复的崩溃 | `console=False` 后启动即退（R1）；双开写坏 SQLite（R7）；升级后数据丢失（R8） | **立即阻断**；必须在当前批次内修复；修复后重跑该批**全部** Gate 项 |
| **P1 严重** | 主要功能异常但存在绕行路径 | 托盘菜单某项不可用；外链走 webview 内跳转；主题跨重启丢失 | 阻断批次通过；同批修复或显式登记为「已知问题 + 绕行方案」并经评审同意 |
| **P2 一般** | 次要功能 / 体验 / 文案 / 边界场景 | DPI 局部错位；通知文案不准确；空闲检测偶发误判 | 不阻断批次，登记入缺陷清单；**发布前必须清零或转为已知问题** |
| **P3 建议** | 优化项 | 启动耗时可再优化；日志格式改进 | 不阻断，纳入 B21 打磨池 |

**三条硬规则**

1. **任何既有测试失败 = 该批次不通过**，无论是否与本批改动相关（先回滚验证，再定位责任）。
2. **P0/P1 缺陷不得跨批次遗留**；确需跨批的，必须在验收记录中写明绕行方案与最晚修复批次。
3. **复验范围**：修复任何 P0/P1 后，重跑该批全部 Gate 项，不接受只跑失败项。

---

## 3. 六层验证矩阵（执行化）

> `<PY>` = 项目 Python 解释器；`<NODE>` = Node ≥ 20 环境；项目根目录为工作目录。

### L1 · 单元/离线（新增桌面测试）

| 项 | 命令 | 时机 |
|---|---|---|
| 端口粘性 | `<PY> test_desktop_port_sticky.py` | 每次提交 |
| 单实例锁 | `<PY> test_desktop_single_instance.py` | 每次提交 |
| 日志配置 | `<PY> test_desktop_logging.py` | 每次提交 |
| 令牌校验 | `<PY> test_desktop_token.py` | 每次提交 |
| 生命周期 | `<PY> test_desktop_lifecycle.py` | 每次提交 |
| 路径三态 | `<PY> test_desktop_paths.py` | 每次提交 |
| WAL 并发 | `<PY> test_desktop_wal.py` | 每次提交 |

### L2 · 既有回归（32 个测试）

```bash
# 统一入口（B00 建立，输出结构化结果与退出码）
<PY> tools/run_regression.py --baseline docs-local/acceptance/regression-baseline.json

# 等价的手工方式（Git Bash）
for f in test_*.py; do <PY> "$f" || echo "FAILED: $f"; done
```

- 时机：B08 / B12 / B17 / B22 各一轮；**B00 先建基线**。
- 判定：全部通过，且与基线清单逐项一致（无新增失败、无跳过、无遗漏文件）。
- 不通过：按第 2 章硬规则 1 处理。

### L3 · 前端契约

```bash
cd frontend
<NODE> npm run typecheck     # vue-tsc --noEmit
<NODE> npm run lint          # ESLint 9 flat + vue
<NODE> npm run gen:api       # openapi-typescript 重新生成
<NODE> npm run build         # 构建前强制类型检查
git diff --stat src/api/types.ts   # 确认单一事实源，无意外漂移
```

- 时机：每次前端改动（B02 / B10 / B11 / B15 / B21）。

### L4 · 构建冒烟

```bash
# 构建
packaging/venv-build/Scripts/python packaging/build.py --flavor desktop --innosetup

# 冒烟（脚本化，沿用 PACKAGING.md 冒烟清单）
<PY> tools/smoke_desktop.py --setup packaging/out/校园通知助手-桌面版-setup.exe
```

冒烟脚本断言链：安装（静默、无管理员）→ 启动（记录进程树）→ `/api/v1/health` 200 → 8 页面 API 200 → SSE 有增量帧 → 关闭 → 进程退出无残留。

### L5 · 真机场景（人工）

见第 5 章 check-list D-01~D-51，阶段 Gate 与发布 Gate 时全量复验。

### L6 · 资源实测

见第 6 章记录表，B01（首次）与 B21（复测）各一次；超标触发 §3.5。

---

## 4. 自动化验收脚本清单与断言

> 建议统一放项目根目录（与既有 `test_*.py` 一致），冒烟与回归入口放 `tools/`。

| 脚本 | 所属批次 | 断言清单 |
|---|---|---|
| `test_desktop_port_sticky.py` | B04 | ① 首次启动分配端口并写入 `data/runtime.json`；② 二次启动复用同一端口；③ 目标端口被占时自动切换到下一可用端口并写回；④ `runtime.json` 不可写时降级为随机端口且启动不失败 |
| `test_desktop_single_instance.py` | B07 | ① 二次启动不产生第二个后端（health 端口唯一）；② 第二实例进程退出；③ 主实例收到 `activate` 并恢复窗口；④ 锁端口被无关程序占用时走文件锁兜底且启动成功 |
| `test_desktop_logging.py` | B05 | ① `sys.stdout = None` 时启动流程不抛异常；② root logger 写入 `data/logs/app.log`；③ uvicorn logger 不覆盖 root（`propagate=False` 生效）；④ 文件轮转在 5MB 边界生效，保留 3 份；⑤ 未捕获异常经 excepthook 落盘完整 traceback |
| `test_desktop_token.py` | B11 | ① 无 `X-Desktop-Token` 头请求 `/api/v1/*` 返回 401；② 带正确令牌返回 200；③ 开发模式（`python -m desktop`）关闭校验；④ `--browser` 降级模式关闭校验；⑤ 令牌每次启动不同（`secrets.token_urlsafe(32)`） |
| `test_desktop_lifecycle.py` | B08 | ① 关闭窗口后进程存活且 health 200；② 托盘退出后进程消失、无子线程残留；③ 退出序列顺序正确（几何 → server → scheduler → tray → 锁）；④ 有 running 任务时退出被二次确认拦截 |
| `test_desktop_paths.py` | B16 | ① dev / portable / installed 三态解析正确；② 老版本 exe 同级 `data/` 仍可读取（向后兼容）；③ 迁移幂等（重复执行结果一致）；④ 迁移失败时原数据完整保留 |
| `test_desktop_wal.py` | B17 | ① 连接初始化后 `PRAGMA journal_mode` = `wal`；② 读写并发不互斥（写事务进行中读不被阻塞）；③ `test_db_concurrency.py` 仍通过；④ 备份文件包含 `-wal`/`-shm` 处理逻辑；⑤ `PRAGMA integrity_check` = ok |
| `tools/run_regression.py` | B00 | ① 收集全部 `test_*.py` 逐个执行；② 输出通过/失败/跳过清单与耗时；③ 与 `--baseline` 的 JSON 比对并报告差异；④ 任一失败退出码非 0 |
| `tools/smoke_desktop.py` | B12 | ① 静默安装无需管理员；② 启动后无控制台/浏览器进程；③ health 200；④ 8 页面 API 200；⑤ SSE 增量帧；⑥ 关闭退出无残留；⑦ 卸载后程序清、data 保留、自启项清 |

### 4.1 骨架落地状态（2026-09-01）

7 个 `test_desktop_*.py` 与 2 个 tools 脚本**已落地为可运行骨架**：断言契约以代码形式固化，对应批次实现后自动激活（模块/接口一出现，该 case 即从 SKIP 转为真实断言）。

| 脚本 | 批次 | 当前状态 | 激活条件 |
|---|---|---|---|
| `test_desktop_port_sticky.py` | B04 | 骨架（全 SKIP） | `desktop.server` 提供 `resolve_port` / `read_runtime_port` / `write_runtime_port` |
| `test_desktop_logging.py` | B05 | 骨架（全 SKIP） | `desktop.logging_setup` 提供 `setup_logging` / `build_uvicorn_log_config` / `install_excepthooks` |
| `test_desktop_lifecycle.py` | B08 | 骨架（全 SKIP） | `desktop.app` 提供 `DesktopApp` / `has_running_tasks` |
| `test_desktop_single_instance.py` | B07 | 骨架（全 SKIP） | `desktop.single_instance` 提供 `SingleInstanceLock` / `send_activate` |
| `test_desktop_token.py` | B11 | 骨架（全 SKIP） | `api.desktop_token` 提供 `generate_token` / `install_token_middleware` / `token_check_enabled` |
| `test_desktop_paths.py` | B16 | **case 0 已真实通过**，其余 SKIP | `utils.app_paths` 补齐 `get_data_mode` / `resolve_legacy_data_dir` / `migrate_data_dir` |
| `test_desktop_wal.py` | B17 | 骨架（全 SKIP） | `storage.db` 提供 `apply_wal_pragmas`；`desktop.backup` 提供 `create_backup` |
| `tools/run_regression.py` | B00 | **已可用** | 基线 JSON 需 B00.T5 跑一次 `--save-baseline` 生成 |
| `tools/smoke_desktop.py` | B12 | 骨架（dry-run，7 步全 SKIP） | B09 产出 desktop 安装包后填充步骤实现 |

**骨架语义**（由 `tools/_desktop_testkit.py` 保证，勿改）：

- 模块未实现 / 缺少接口 / 断言体未填充 → `[SKIP]`，**退出码 0，不阻断回归**；
- 断言失败或抛异常 → `[FAIL]` / `[ERROR]`，**退出码 1**；
- 所有脚本最后一行必须形如 `结果: 全部通过` / `结果: 全部跳过（原因）` / `结果: N 项失败 -> [...]`，供 `run_regression.py` 解析。

**现在就能跑的验证命令**（应全绿、退出码 0）：

```bash
python tools/run_regression.py --pattern "test_desktop_*.py"
python tools/smoke_desktop.py --setup packaging/out/校园通知助手-桌面版-setup.exe
```

**约定**：所有新增测试脚本必须可在 `console=True` 与 `console=False` 两种形态下被 `run_regression.py` 收集执行，且**不得**依赖 stdout 可见（与 R1 对策一致）。

---

## 5. 人工 check-list（D-01 ~ D-51）

> 判定列填：`通过` / `不通过` / `不适用`（不适用需注明原因）。
> 阶段 Gate 与发布 Gate 需**全量复验**，不接受「上一阶段已验」作为免验理由。

### B01 · 可行性与选型（4 条）

| # | 验收项 | 方法 | 判定 |
|---|---|---|---|
| D-01 | 资源实测三态数据齐备（后端基线 / 壳增量 / 嵌入模型态） | `psutil` 或任务管理器记录私有工作集 | |
| D-02 | SSE 在 WebView2 下逐字流式输出 | 智能问答页发起提问，观察增量渲染 | |
| D-03 | 剪贴板可用 | 数据源中心复制操作 | |
| D-04 | 125% / 150% 缩放无破版 | 系统显示设置切换后逐页查看 | |

### B02 · 前端规范化（2 条）

| # | 验收项 | 方法 | 判定 |
|---|---|---|---|
| D-05 | typecheck / lint / build 三条脚本均可独立执行 | 命令行逐条执行 | |
| D-06 | `schema.ts` 与 `types.ts` 无重复定义 | 人工比对重叠清单 | |

### B03 · 壳骨架（2 条）

| # | 验收项 | 方法 | 判定 |
|---|---|---|---|
| D-07 | `python -m desktop` 可启动且 8 页面可访问 | 启动后逐页点击 | |
| D-08 | 退出后无残留进程 | 任务管理器检查 | |

### B04 · 端口粘性与看门狗（3 条）

| # | 验收项 | 方法 | 判定 |
|---|---|---|---|
| D-09 | 连续两次启动端口一致 | 查看 `data/runtime.json` 与窗口地址 | |
| D-10 | 端口被占时自动切换且主题/会话不丢 | 手动占用端口后启动 | |
| D-11 | 制造后端崩溃后自动恢复 | kill 后端线程，观察 15s 内 health 恢复 | |

### B05 · 日志与崩溃（3 条）

| # | 验收项 | 方法 | 判定 |
|---|---|---|---|
| D-12 | 无控制台下异常可落盘 | 触发未捕获异常后查看 `data/logs/app.log` | |
| D-13 | 一键导出日志可用 | 弹窗「导出日志」按钮 | |
| D-14 | uvicorn 日志与 app 日志不重复、不互相覆盖 | 查看日志文件内容 | |

### B06 · 托盘与窗口（3 条）

| # | 验收项 | 方法 | 判定 |
|---|---|---|---|
| D-15 | 托盘菜单五项逐项可用（打开主界面/暂停调度/检查更新/打开日志目录/退出） | 逐项点击 | |
| D-16 | 窗口位置/大小重启后恢复 | 移动并缩放后重启 | |
| D-17 | 点 X 隐藏到托盘，进程常驻 | 关闭后 health 仍 200 | |

### B07 · 单实例（2 条）

| # | 验收项 | 方法 | 判定 |
|---|---|---|---|
| D-18 | 二次启动不双开且主窗口被唤起 | 双击两次快捷方式 | |
| D-19 | 锁端口被占时走文件锁兜底且启动成功 | 手动占用 51999 后启动 | |

### B08 · 退出编排（3 条）

| # | 验收项 | 方法 | 判定 |
|---|---|---|---|
| D-20 | 托盘退出后进程完全消失（无残留线程） | 任务管理器 + 线程数检查 | |
| D-21 | 关机/注销时优雅退出，重启后数据无损坏 | 注销重登后 `PRAGMA integrity_check` | |
| D-22 | 有任务运行时退出有二次确认 | 跑抓取任务时点退出 | |

### B09 · 打包（3 条）

| # | 验收项 | 方法 | 判定 |
|---|---|---|---|
| D-23 | 双击快捷方式启动无终端窗口 | 肉眼 + 进程树无 conhost | |
| D-24 | 安装包静默安装无需管理员 | `/VERYSILENT` 安装 | |
| D-25 | 覆盖升级后配置备份、`data` 保留 | 装旧版→装新版→比对 | |

### B10 · 桌面适配（4 条）

| # | 验收项 | 方法 | 判定 |
|---|---|---|---|
| D-26 | 外链点击走系统默认浏览器，不在 webview 内跳转 | 逐页点击外链（6 页面 10 处） | |
| D-27 | 125% / 150% 缩放无破版 | 切换缩放后 8 页面查看 | |
| D-28 | 主题与 QA 会话连续两次启动后仍保持（验证 K1） | 改主题→退出→重启→再重启 | |
| D-29 | 剪贴板复制可用 | 数据源中心复制 | |

### B11 · 控制面与令牌（2 条）

| # | 验收项 | 方法 | 判定 |
|---|---|---|---|
| D-30 | 无令牌请求被拒（401） | curl 不带令牌访问 `/api/v1/desktop/status` | |
| D-31 | 开发模式与 `--browser` 模式不受令牌影响 | 两种模式下访问 API | |

### B13 · 自启与启动参数（3 条）

| # | 验收项 | 方法 | 判定 |
|---|---|---|---|
| D-32 | 注销重登后自动驻留托盘 | 注销重登 | |
| D-33 | `--minimized` / `--autostart` / `--browser` 三项均生效 | 命令行带参启动 | |
| D-34 | `--browser` 降级可用（webview 故障时兜底） | 带参启动观察浏览器拉起 | |

### B14 · 调度与空闲（3 条）

| # | 验收项 | 方法 | 判定 |
|---|---|---|---|
| D-35 | 空闲 5 分钟后触发重活 | 静置 5 分钟观察调度日志 | |
| D-36 | 检测到输入后 1s 内挂起重活 | 重活运行中动鼠标 | |
| D-37 | 睡眠唤醒后不误判 | 合盖→唤醒后观察 | |

### B15 · 桌面设置页（2 条）

| # | 验收项 | 方法 | 判定 |
|---|---|---|---|
| D-38 | 桌面设置项开关即时生效并持久化 | 切换自启/关闭行为后查注册表与 `settings.json` | |
| D-39 | 重启后设置保持 | 重启应用查看 | |

### B16 · 数据目录（2 条）

| # | 验收项 | 方法 | 判定 |
|---|---|---|---|
| D-40 | 老用户 exe 同级 `data/` 平滑迁移，原数据保留 | 用 v0.1.0 数据目录升级后比对条数 | |
| D-41 | 迁移失败可回退旧目录继续用 | 断电/权限失败模拟 | |

### B17 · WAL 与备份（2 条）

| # | 验收项 | 方法 | 判定 |
|---|---|---|---|
| D-42 | 托盘后台跑调度时前端轮询不卡顿 | 抓取进行中操作前端 | |
| D-43 | 备份可恢复 | 用备份文件还原后启动 | |

### B18 · 更新闭环（2 条）

| # | 验收项 | 方法 | 判定 |
|---|---|---|---|
| D-44 | 发布新 tag 后可完成一次完整升级 | 打测试 tag → 应用内升级 | |
| D-45 | sha256 校验失败时中止并提示 | 篡改产物后触发更新 | |

### B19 · CI 与分发（2 条）

| # | 验收项 | 方法 | 判定 |
|---|---|---|---|
| D-46 | 干净机（无 WebView2）安装后可直接运行 | 虚拟机全新 Win10 安装 | |
| D-47 | 分发页 sha256 与实际产物一致 | 手工比对 | |

### ~~B20 · macOS~~ · 不适用（2 条）

> **已决策（2026-09-01）：v0.2.0 只做 Windows x64**，macOS 不排期（对应批次 B20 已移出计划）。
> 以下两条**标记为不适用**，不计入任何 Gate 的通过率；v0.3.0 若重启 macOS，按原内容恢复。

| # | 验收项 | 状态 |
|---|---|---|
| ~~D-48~~ | ~~DMG 可安装启动~~ | 不适用（仅 Windows） |
| ~~D-49~~ | ~~（如有证书）Gatekeeper 无拦截~~ | 不适用（仅 Windows） |

### B21 · 打磨（2 条）

| # | 验收项 | 方法 | 判定 |
|---|---|---|---|
| D-50 | 通知徽标与已读状态正确 | 产生通知后查看徽标与已读持久化 | |
| D-51 | 内存优化后长跑 24h 无明显增长 | 长跑采样 | |

### 复验矩阵

| Gate | 复验范围 |
|---|---|
| P2 Gate（B12） | D-01 ~ D-31 全量 |
| P3 Gate（B15） | D-32 ~ D-39 |
| P4 Gate（B18） | D-40 ~ D-45 |
| P5 Gate（B19） | D-46 ~ D-47（D-48/D-49 不适用） |
| R-Gate（B22） | **D-01 ~ D-47 全量**（49 条，D-48/D-49 不适用） |

---

## 6. 资源实测记录表（L6，B01 / B21 各一次）

**环境**：OS 版本 ＿＿ ／ WebView2 版本 ＿＿ ／ CPU ＿＿ ／ 内存 ＿＿ ／ 嵌入模式（云端/本地）＿＿

| 态 | 私有工作集 | 启动到 health 200（5 次中位数） | 产物体积 |
|---|---|---|---|
| 后端基线（`python run_app.py`，不开浏览器） | ＿＿ MB | ＿＿ s | — |
| 壳增量（pywebview 起同样后端） | ＿＿ MB（**≤ 60 MB**） | ＿＿ s（**≤ 2 s**） | — |
| 嵌入模型态（本地 bge 加载后） | ＿＿ MB | ＿＿ s | — |
| 总内存（空闲） | ＿＿ MB（**≤ 180 MB**） | — | — |
| 产物 | — | — | `dist-desktop` ＿＿ MB ／ setup.exe ＿＿ MB |

**判定**：三项达标线全部满足 → 通过；任一超标 → 触发 §3.5 切换准则，评估转 Tauri。

> 记录归档到 `docs-local/`（不入库）。

---

## 7. 批次验收记录模板

> 每批一份，存 `docs-local/acceptance/B{xx}-验收记录.md`。

```markdown
# B{xx} {批次名} 验收记录

- 日期：YYYY-MM-DD
- 执行人：
- 环境：OS ／ WebView2 ／ Python ／ Node
- base commit：{hash}  ／  结束 commit：{hash}

## 1. 批次内容
（一句话说明本批交付了什么）

## 2. 提交序列
| # | commit | 说明 |
|---|---|---|
| 1 | {hash} | {简述} |

## 3. 自动验证结果
| 层级 | 命令 | 结果 | 备注 |
|---|---|---|---|
| L1 | `python test_desktop_xxx.py` | 通过 / 失败 | |
| L2 | `python tools/run_regression.py` | 32/32 通过 | 与基线一致：是/否 |
| L3 | `npm run typecheck && lint && build` | 通过 | |
| L4 | `python tools/smoke_desktop.py` | 通过 | |

## 4. 人工 check-list
| # | 验收项 | 判定 | 备注 |
|---|---|---|---|
| D-xx | | 通过/不通过/不适用 | |

## 5. 缺陷清单
| # | 级别 | 描述 | 处理 | 修复批次 |
|---|---|---|---|---|
| 1 | P1 | | 已修/登记/绕行 | |

## 6. 结论
- [ ] 自动项全部通过
- [ ] 本批 D-xx 全部通过
- [ ] 无 P0/P1 遗留
- [ ] 回归与基线一致
→ **Gate 结论：通过 / 不通过**
```

---

## 8. 发布 Gate（R-Gate，v0.2.0）

逐条对应方案 §9.5，任一不满足不得发布：

| # | 条件 | 验证方式 | 结果 |
|---|---|---|---|
| 1 | L1~L4 全绿，L5 check-list **100%** 勾验通过 | 第 3 章命令 + 第 5 章 D-01~D-47 全勾（49 条） | |
| 2 | 32 个既有测试全绿，**无一项因桌面化而跳过** | `tools/run_regression.py` 输出清单与 B00 基线比对 | |
| 3 | 真机连续运行 7 天无 P0 崩溃（崩溃 = 需手动重启才能恢复） | 稳定性观察日志 | |
| 4 | 资源实测达标（壳增量 ≤ 60 MB、空闲总内存 ≤ 180 MB、首屏 ≤ 2s） | 第 6 章记录表（B21 复测） | |
| 5 | 回滚方案就绪：v0.1.0 在线版安装包可获取，桌面版严重故障可回退 | Release 页面核对 | |
| 6 | 文档齐备：USAGE 增桌面版章节（托盘说明、日志导出路径、卸载与数据保留策略）、README 更新、Release Notes | 文档评审 | |

---

## 9. 回归契约清单（既有功能不得回归）

方案 §5.3 的逐条落地：**任何一项回归即视为该批次不通过**。

| 类别 | 功能 | 验证脚本 | 回归轮次 |
|---|---|---|---|
| 接口 | 11 路由模块 / 68 端点 + SSE | `test_api_smoke.py` | 全部 4 轮 |
| 异步任务 | TaskManager + 任务锁幂等 + 崩溃恢复 | `test_tasks.py`、`test_resume.py` | 全部 4 轮 |
| 调度 | 5 个 job（crawl/extract/daily/reminder/config-watch） | `test_scheduler_integration.py` | 全部 4 轮 |
| 抓取 | 增量抓取 + 内容指纹 + 年龄过滤 | `test_incremental_crawl.py`、`test_content_fingerprint.py`、`test_crawl_age_filter.py` | 全部 4 轮 |
| 提取 | 规则预筛 + 中文时间解析 + 重试归因 | `test_fast_path.py`、`test_gongshi_period.py`、`test_retry_attribution.py` | 全部 4 轮 |
| RAG | 混合检索 + 缓存 + 引用 + SSE 错误契约 | `test_hybrid.py`、`test_qa_cache.py`、`test_qa_citation.py`、`test_qa_sse_error.py` | 全部 4 轮 |
| 存储 | SQLite 并发 + 向量一致性 | `test_db_concurrency.py`、`test_vectorstore_singleton.py` | 全部 4 轮 |
| 订阅/提醒 | 规则匹配 + 提醒生成 | `test_subscription.py`、`test_reminder.py` | 全部 4 轮 |
| Token 计量 | 统一调用点记账 | `test_token_usage.py` | 全部 4 轮 |
| 更新检查 | GitHub Releases + 静默失败契约 | 手工：未配 repo 时恒 200 | B12 / B18 |
| 打包 | cloud/full 双 flavor + per-user 安装 + data 卸载保留 + app.yaml.old 备份 | 真机装→升→卸三连 | B12 / B19 |
| 前端 | 8 页面全部功能 + 响应式断点（960/720/540） | 逐页冒烟 + 缩放验证 | B12 / B22 |

> **注意**：B17 开 WAL 后，`test_db_concurrency.py` 与 `test_vectorstore_singleton.py` 为重点观察对象；出现异常先回滚 WAL 再定位。

---

*编制：基于 DESKTOP-UPGRADE.md §9（2026-09-01）｜ 与 DESKTOP-BATCH-PLAN.md 配套使用*
