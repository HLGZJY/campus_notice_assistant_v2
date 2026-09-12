---
title: 系统架构与设计文档
summary: 校园通知智能助手的整体架构视图——分层结构、桌面/Web 双形态运行时、核心模块职责、端到端数据流与关键设计决策
source: 代码实测（api/ core/ services/ storage/ crawler/ desktop/ frontend/）+ docs/ARCHITECTURE.md 提炼
status: active
updated: 2026-09-12
read_when:
  - 需要理解系统整体结构、模块边界与依赖方向
  - 需要了解桌面版（pywebview 壳）与 Web 版的关系
  - 需要梳理抓取→提取→索引→问答→提醒的数据流
  - 新人上手或准备重构前建立全局认知
---

# 系统架构与设计文档

> 本文档是**面向全局的架构总览**，回答「系统由哪些部分组成、它们如何协作、数据怎么流动」。
> 更细的字段级契约见 [DATA-MODEL.md](DATA-MODEL.md)，接口清单见 [ARCHITECTURE.md](ARCHITECTURE.md) §7，
> 桌面改造的批次决策见 [DESKTOP-UPGRADE.md](DESKTOP-UPGRADE.md)。

## 一、系统定位与设计哲学

### 1.1 一句话定位

把散落在学校各网站的通知，经「抓取 → LLM 结构化提取 → 向量索引」转化为**可检索、可订阅、可待办、可提醒**的结构化信息，并以**原生桌面应用**或 **Web 服务**两种形态交付。

### 1.2 三条设计哲学

| 原则 | 含义 | 落地证据 |
| --- | --- | --- |
| **确定性优先** | 能用规则解决的绝不交给 LLM | 提取前置规则预筛、自研中文时间解析器、订阅纯规则匹配、问答来源从检索元数据导出（杜绝幻觉引用） |
| **限制模型犯错空间** | 把 LLM 不确定性包在可靠软件边界内 | `output_type` 硬约束采样 + 校验失败回传重试（≤2 次）+ 模型失败切换 + 三层问答缓存 |
| **评估闭环** | 每个 Agent 环节都有可复现度量 | 黄金集 24/24（提取）、100%（时间解析）、20 题检索集、43 个离线验收脚本 |

### 1.3 架构分层总览

```mermaid
graph TB
    subgraph L1["① 表现层"]
        VW["桌面壳<br/>pywebview 原生窗口"]
        WB["浏览器<br/>系统默认浏览器"]
        UI["Vue 3 SPA<br/>9 页面 · Pinia · 路由懒加载"]
        VW --> UI
        WB --> UI
    end

    subgraph L2["② 接入层"]
        API["FastAPI 应用工厂<br/>/api/v1 · 14 路由模块"]
        TOK["桌面令牌中间件<br/>K7 · health 豁免"]
        TASK["TaskManager<br/>asyncio 单 worker · 202→轮询"]
        SCH["APScheduler<br/>6 job · lifespan 拉起"]
        TOK -.校验.-> API
        API --> TASK
        API --> SCH
    end

    subgraph L3["③ 业务服务层 services/"]
        SVC["13 个服务<br/>notice / todo / qa / subscription / reminder<br/>config / admin / tracking / usage / health<br/>source_center / embedding_model / update"]
    end

    subgraph L4["④ 引擎层"]
        CRW["crawler/<br/>newspaper4k 抓取<br/>内容指纹变更检测"]
        CORE["core/<br/>LLM Agent 提取/待办/问答<br/>中文时间解析器"]
        IDX["storage/hybrid<br/>BM25 + 向量 + RRF 融合"]
        LLMU["utils/llm<br/>统一调用点<br/>失败切换 + token 计量"]
    end

    subgraph L5["⑤ 数据层"]
        SQL[("SQLite<br/>13 张业务表")]
        VDB[("Chroma<br/>向量库")]
        YML[("YAML<br/>app.yaml / schools/")]
    end

    subgraph L6["⑥ 运行时支撑 desktop/"]
        SHELL["壳主控 app.py<br/>单实例·端口粘性·看门狗<br/>托盘·空闲 gate·备份·更新"]
    end

    UI -->|"HTTP /api/v1<br/>REST + SSE"| API
    API --> SVC
    TASK --> SVC
    SCH --> SVC
    SVC --> CRW
    SVC --> CORE
    SVC --> IDX
    CORE --> LLMU
    CRW --> SQL
    CORE --> SQL
    IDX --> VDB
    SVC --> SQL
    SVC --> YML
    SHELL -.托管.-> API
    SHELL -.控制面.-> API
```

> **依赖方向单向向下**：表现层不直连数据层；路由层只做「校验 + 调服务 + 序列化」的薄转发；
> 业务服务层返回统一 dict 契约，上层不感知存储实现。`storage/` 已抽象，可平替 PostgreSQL。

## 二、双形态运行时：桌面版与 Web 版

同一套后端代码（75 个 REST 端点**零改动**）被两种形态复用，差异只在**进程编排**：

```mermaid
graph LR
    subgraph DESK["桌面形态（单进程 · 推荐交付）"]
        direction TB
        D1["desktop_main.py 入口"]
        D2["DesktopApp.run()"]
        D3["单实例锁 → 端口粘性<br/>→ uvicorn daemon 线程"]
        D4["等 /health 200 → pywebview 窗口"]
        D5["托盘 + 空闲 gate + 备份 + 更新"]
        D1 --> D2 --> D3 --> D4
        D2 --> D5
    end

    subgraph WEB["Web 形态（开发 / 排障 / --browser 降级）"]
        direction TB
        W1["uvicorn api.main:app"]
        W2["浏览器访问<br/>localhost:8000"]
        W3["前端 Vite dev<br/>:5173 代理"]
        W1 --> W2
        W1 --> W3
    end
```

| 维度 | 桌面形态 | Web 形态 |
| --- | --- | --- |
| 启动方式 | 双击 exe / 开机自启（`--autostart`） | 命令行 `uvicorn api.main:app` |
| 界面容器 | pywebview（WebView2）原生窗口 | 系统默认浏览器 |
| 后端托管 | `desktop/server.py` daemon 线程 `uvicorn.Server.run()` | 独立进程 / uvicorn 前台 |
| 端口 | **粘性**（`data/runtime.json` 记住上次端口，避免 localStorage 丢失） | 8000 固定 / 可指定 |
| 安全 | 启动令牌 `X-Desktop-Token`（K7 中间件） | 无令牌（默认关闭校验） |
| 关闭语义 | 默认最小化到托盘（可配 `close_action=exit`） | 进程结束即关闭 |
| 崩溃恢复 | 看门狗重建 Server 实例 + 崩溃转储 | 手动重启 |

> **降级兜底**：`--browser` 参数让桌面壳跳过 webview、改用系统浏览器（同时关令牌校验），
> 用于 WebView2 缺失或渲染异常时的排障路径。`--no-tray` 则回滚到「关闭即退出」语义。

### 2.1 桌面壳启动序列

```mermaid
sequenceDiagram
    participant U as 用户
    participant E as 入口 desktop_main
    participant A as DesktopApp
    participant L as SingleInstanceLock
    participant S as ServerManager
    participant W as pywebview

    U->>E: 启动（双击 / 自启）
    E->>A: run()
    A->>A: setup_logging + 崩溃钩子
    alt --browser 降级
        A->>S: 起后端 → 打开系统浏览器 → 阻塞
    else 正常桌面模式
        A->>L: 尝试取单实例锁
        alt 已有实例
            A->>A: 唤起旧窗口 → 本实例退出
        else 主实例
            A->>S: 解析端口（粘性）→ 起 uvicorn daemon 线程
            A->>S: 轮询 /api/v1/health（≤30s）
            A->>A: 启动空闲 gate + 嵌入模型守卫
            A->>W: create_window(js_api=外链桥)
            W->>W: loaded → 注入令牌 + 外链拦截脚本
            A->>A: 创建托盘 + 注册关机钩子
            W->>U: 显示主界面
        end
    end
```

### 2.2 桌面壳退出序列（K5 统一路径）

四条触发路径（托盘退出 / 窗口关闭 / 系统关机 / 看门狗）全部收敛到 `_do_exit()`，**幂等**：

```
写窗口几何 → server.should_exit=True + join(10s)
  → scheduler.stop()（显式兜底）→ pystray.stop()
  → 释放单实例锁 → 停空闲 gate → 停嵌入守卫 → 停关机钩子 → sys.exit(0)
```

> **关键约束**：`window.destroy()` 也会触发 `closing` 事件，退出时必须先置 `_exiting=True` 放行，
> 否则关闭被取消、`webview.start()` 永不返回，进程卡死。

## 三、核心模块职责

### 3.1 分层模块清单

| 层 | 目录 | 职责 | 关键文件 |
| --- | --- | --- | --- |
| 表现层 | `frontend/src/` | 9 页面 SPA、Pinia 状态、路由守卫埋点、任务轮询 | `views/` `stores/` `composables/useTaskPoll.ts` |
| 契约层 | `frontend/openapi.json` | **单一事实源**，`openapi-typescript` 生成 `types.ts`（零漂移） | `src/api/types.ts` |
| 接入层 | `api/` | 应用工厂、路由转发、异步任务、调度器托管、令牌校验 | `main.py` `tasks/manager.py` `desktop_token.py` |
| 业务层 | `services/` | 13 个服务，返回统一 dict 契约 | `notice_service.py` `qa_service.py` |
| Agent 层 | `core/` | 提取/待办/问答三类 Agent + 中文时间解析 | `extractor.py` `todo.py` `qa.py` `date_utils.py` |
| 抓取层 | `crawler/` | newspaper4k 列表发现 + 详情提取 + 内容指纹 | `web_crawler.py` `base.py` |
| 存储层 | `storage/` | SQLite（13 表）+ Chroma + BM25/RRF | `db.py`(1866 行) `vectorstore.py` `hybrid.py` |
| 配置层 | `config/` | Pydantic 模型 + YAML 三层 fallback + 原子写 | `store.py` `schema.py` `schools/*.yaml` |
| 工具层 | `utils/` | LLM/Embedding 唯一调用点、应用路径解析 | `llm.py` `embedding.py` `app_paths.py` |
| 壳层 | `desktop/` | 单实例、端口、托盘、看门狗、备份、更新、空闲 gate | `app.py` `server.py` `updater.py` |

### 3.2 关键子系统

**① 搜索引擎（`storage/`）** — 双路检索 + 融合

```mermaid
graph LR
    Q["用户 Query"] --> TK["jieba 分词<br/>中文保原词<br/>英文数字小写归一"]
    TK --> BM["BM25 稀疏检索<br/>top-20 候选"]
    Q --> EM["bge-small-zh<br/>本地 embedding"]
    EM --> VC["Chroma 稠密检索<br/>top-20 候选"]
    BM --> RRF["RRF 融合<br/>score = Σ 1/(60+rank)"]
    VC --> RRF
    RRF --> TOP["Top-K chunk"]
    TOP --> DD["按 notice_id 去重编号"]
```

> **BM25 语料与向量库同源**：直接从 Chroma `collection.get()` 拉取全部 chunk 构建，
> 杜绝「重新切分导致两路语料漂移」。过期策略三档（none / decay / filter）两路共用对齐。

**② LLM 调用中枢（`utils/llm.py`）** — 全项目唯一出口

```mermaid
graph TB
    A1["提取 Agent"] --> RUN["run_agent / run_agent_stream"]
    A2["待办 Agent"] --> RUN
    A3["问答 Agent"] --> RUN
    RUN --> CAND["取任务级模型候选列表<br/>extraction / qa / todo"]
    CAND --> TRY["按序尝试"]
    TRY -->|失败| JUDGE{"is_failover_worthy?"}
    JUDGE -->|429/5xx/网络| NEXT["切换下一模型"]
    JUDGE -->|400/401/403| FAIL["直接失败（切模型无用）"]
    NEXT --> TRY
    TRY -->|成功| REC["record_llm_usage<br/>成功失败都记账"]
    FAIL --> REC
```

**③ 异步任务系统（`api/tasks/`）** — 长耗时操作不阻塞 Web

```mermaid
sequenceDiagram
    participant F as 前端
    participant R as 路由 /tasks
    participant M as TaskManager
    participant W as Worker 线程

    F->>R: POST /tasks {type, params}（如 crawl_all）
    R->>M: submit(type, params)
    M->>M: compute_lock_key → 幂等去重
    M-->>R: task_id
    R-->>F: 202 Accepted
    loop 前端轮询
        F->>R: GET /tasks/{id}
        R-->>F: {status, progress, result}
    end
    M->>W: asyncio.to_thread(worker_fn, task, progress_cb)
    W->>W: 业务执行 + 进度回调写库
    W-->>M: 完成 / 异常
```

> **为什么单 worker 串行**：SQLite 单写者 + 配置写权唯一 + Chroma 单 collection，
> 串行是**天然免费的并发保护**。任务锁 `(type, lock_key)` 保证重复点击返回同一 task_id（202 而非 409）。

**④ 问答链路（`core/qa.py` + `services/qa_service.py`）** — 三层缓存 + SSE 流式

```mermaid
flowchart TD
    Q["用户提问"] --> L1{"L1 精确 hash 命中?"}
    L1 -->|命中| HIT["直接返回缓存答案"]
    L1 -->|未命中| L2{"L2 语义 cosine 相似?"}
    L2 -->|命中| HIT
    L2 -->|未命中| RET["混合检索 Top-K"]
    RET --> PROMPT["按 notice_id 去重编号拼 Prompt"]
    PROMPT --> LLM["LLM 流式生成"]
    LLM --> SRC["来源从检索元数据确定性导出"]
    SRC --> WRITE["L3 UPSERT 写缓存 + LRU 淘汰"]
    WRITE --> SSE["SSE 逐 token 下发"]
```

## 四、端到端数据流

### 4.1 主链路：抓取 → 提取 → 索引

```mermaid
sequenceDiagram
    participant SCH as 调度器 crawl job
    participant CRW as WebCrawler
    participant DB as SQLite
    participant EXT as 提取 Agent
    participant HS as HybridIndex

    SCH->>CRW: 触发抓取（按来源 list_url）
    CRW->>CRW: 列表页发现链接（时效过滤）
    CRW->>DB: 查已抓 URL（增量早停：整页已知即停翻页）
    alt 新 URL
        CRW->>CRW: 抓详情页（重试 + 指数退避）
        CRW->>DB: 存原始通知 status=raw + content_hash
    else 深检轮（每 N 轮一次）
        CRW->>CRW: 重抓比对指纹
        CRW->>DB: 变更 → 更新正文 + 重置 raw
    end
    SCH->>EXT: extract job（抓取后 20s）
    EXT->>EXT: 规则预筛（时效/长度/关键词/时间线索/订阅）
    alt 预筛通过
        EXT->>EXT: LLM 结构化提取 + 校验重试
        EXT->>EXT: 中文时间解析器重算 deadline
        EXT->>DB: status=extracted/partial/failed
        EXT->>HS: 增量索引 + 订阅命中匹配
    else 预筛跳过
        EXT->>DB: 落 extract_skipped_reason（不调 LLM，省 token）
    end
```

**成本控制三层节流**：① 增量抓取（不重抓已入库详情）② 提取规则预筛（不通过不调 LLM）③ 模型失败切换。
全链路 token 消耗统一经 `utils/llm.py` 记账，可审计、可聚合、可做预算。

### 4.2 用户交互链路

```mermaid
graph TB
    U["用户操作"] --> ACT{"动作类型"}
    ACT -->|浏览通知| N["GET /notices<br/>分页信封 + 命中徽标"]
    ACT -->|创建待办| T["POST /notices/{id}/todos<br/>→ 异步任务 202"]
    ACT -->|智能问答| QA["GET /qa/ask/stream<br/>→ SSE 逐 token"]
    ACT -->|创建订阅| S["POST /subscriptions/preview<br/>→ 确认 → 提交任务"]
    ACT -->|改配置| C["PUT /config/*<br/>→ 原子写 + 热更新"]
    N --> TRK["埋点 POST /events<br/>失败不阻塞主流程"]
    T --> TRK
    QA --> TRK
    S --> TRK
    C --> TRK
```

### 4.3 自动化链路（调度器 6 job）

```mermaid
graph LR
    subgraph JOBS["APScheduler 6 个 job"]
        J1["crawl<br/>每 N 分钟（热更新）"]
        J2["extract<br/>抓取后 20s"]
        J3["daily<br/>每日 03:00"]
        J4["reminder<br/>每日 03:00"]
        J5["config-watch<br/>每 60s"]
        J6["backup<br/>每日检查·按周判频"]
    end
    J1 --> R1["抓取→入库→触发 J2"]
    J2 --> R2["提取→索引→订阅匹配"]
    J3 --> R3["过期清理 + 向量一致性 + 每日体检"]
    J4 --> R4["截止前 3天/1天 生成提醒（幂等）"]
    J5 --> R5["间隔变更热更新 J1/J2"]
    J6 --> R6["SQLite backup API 一致性快照<br/>保留 3 份"]
```

> **幂等与恢复**：所有 job 运行记录落 `scheduler_log`（含连续失败计数），重启可恢复；
> `notices.url UNIQUE` 保证崩溃后已抓 URL 不重复抓取。暂停支持两级——全局 `pause()`
> 与仅挂起重活 `pause_heavy()`（空闲 gate 用，不影响凌晨体检/提醒）。

## 五、关键设计决策

### 5.1 数据一致性

| 问题 | 方案 |
| --- | --- |
| **RAG 污染（幽灵结果）** | 三层防线：① 删通知时按 `notice_id` **级联删向量**（按真实 chunk id 删，规避 where 语义不确定）② 重建索引先 `delete_collection()` ③ 每日体检自动一致性校验清理 |
| **配置并发写** | 写入权**唯一归后端 API 进程**；调度器/CLI 只读，`force_reload` 后重载 |
| **配置损坏** | 三层 fallback（`app.yaml` → `.bak` → 内置默认）+ 三步原子写（`.tmp → .bak → os.replace`） |
| **WAL 未 checkpoint** | 备份必须用 SQLite `backup()` API 而非裸拷贝主库（裸拷会漏 WAL 中未落盘数据） |
| **事件循环被阻塞** | async 中的同步阻塞（requests / PyTorch / Chroma 写）一律 `asyncio.to_thread` 包装 |

### 5.2 为什么这样选

| 决策 | 选择 | 理由 | 不选 X |
| --- | --- | --- | --- |
| 后端框架 | FastAPI + uvicorn | 异步原生，契合 SSE / 异步任务；自动生成 OpenAPI | Flask/Django 同步 WSGI 对长连接弱 |
| Agent 框架 | OpenAI Agents SDK | `output_type` 硬约束采样空间，适配字段提取 | LangGraph 对本任务边界清晰场景是过度设计 |
| 向量库 | Chroma（嵌入式） | 单机万级 chunk 零运维 | Milvus/Weaviate 重型分布式过度设计 |
| 检索 | BM25 + 向量 + RRF | 中文专名（比赛缩写/英文混排）稀疏召回互补 | 纯向量对中文专名召回不足 |
| 数据存储 | SQLite 13 表 | 单文件零依赖，单写者模型足够 | PostgreSQL 个人规模增加运维成本 |
| 桌面壳 | pywebview + 自研胶水层 | 后端 68 端点零改动，壳增量仅 44.6MB | Electron 体积大；Tauri 需重写前端集成 |
| 前后端契约 | openapi-typescript | openapi.json 唯一事实源，类型零漂移 | 手写类型易漂移 |

### 5.3 可扩展性预留

| 方向 | 现状 | 扩展点 |
| --- | --- | --- |
| 多学校适配 | `config/schools/<code>.yaml` | 新增学校零代码，改 `active_school` |
| 多用户鉴权 | `api/deps.py` 鉴权占位 | 换 JWT/OAuth，业务路由零改动 |
| 数据库平替 | `storage/` 已抽象 | 换 PostgreSQL 只改 storage 实现 |
| 站外推送 | 提醒/订阅数据模型就绪 | 扩展邮件 / 微信 / 桌面通知 |
| 多端接入 | 契约驱动 + 令牌中间件 | APP 端复用同一 `/api/v1` |

## 六、部署与数据目录

### 6.1 数据目录三态（`utils/app_paths.py`）

| 模式 | 触发条件 | 数据位置 |
| --- | --- | --- |
| `dev` | 源码运行 | 项目根 `data/` |
| `portable` | 便携版（exe 同级有 `data/` 或标记） | exe 同级 `data/` |
| `installed` | 安装版 | `%LOCALAPPDATA%` 下应用目录 |

> 三态决定 SQLite、Chroma、日志、备份、WebView2 user-data 的落点；迁移向导只做**只读复制**，不删源。

### 6.2 数据资产

| 资产 | 位置 | 说明 |
| --- | --- | --- |
| 业务库 | `data/notices.db` | SQLite 13 表，WAL + NORMAL 同步 |
| 向量库 | Chroma 持久化目录 | 与 SQLite 通知 ID 集合须一致 |
| 日志 | `data/logs/` | 滚动日志 + `crash/` 崩溃转储 |
| 备份 | `data/backups/` | `backup()` API 快照，保留 3 份，`.last_backup` 记时间戳 |
| 运行时 | `data/runtime.json` | 端口粘性 |
| 设置 | settings.json | 窗口几何、自启、关闭行为、空闲阈值 |

## 七、测试与质量

| 维度 | 内容 |
| --- | --- |
| 离线验收 | 43 个 `test_*.py`（爬虫/检索/任务/缓存/并发/崩溃恢复/桌面壳） |
| 评估脚本 | `evaluate_extraction.py`（24/24）、`evaluate_retrieval.py`（20 题）、`evaluate_hybrid.py`、`evaluate_todo.py` |
| 一致性 | `check_vector_consistency.py`（退出码 0=一致）、`reproduce_pollution.py`（污染复现） |
| 测试约定 | **临时库一律 `tempfile.mkdtemp()`**，绝不用 `data/`（沙箱删除被拦截 → 残留致 UNIQUE 失败） |
| 性能基线 | 6 源增量一轮 ≈ 3.5s；2 条并发提取 3.21s（串行 5.88s） |

## 八、文档地图

| 文档 | 定位 |
| --- | --- |
| **本文档 SYSTEM-DESIGN.md** | 全局架构视图：分层、模块、数据流、决策 |
| [ARCHITECTURE.md](ARCHITECTURE.md) | 模块划分细节 + API 端点清单 + 配置项全表 |
| [DATA-MODEL.md](DATA-MODEL.md) | 13 张表结构 + Pydantic 模型 |
| [PRD.md](PRD.md) | 产品需求、用户故事、交付状态 |
| [RAG-POLLUTION.md](RAG-POLLUTION.md) | RAG 污染防护专项 |
| [DESKTOP-UPGRADE.md](DESKTOP-UPGRADE.md) | 桌面化改造批次决策（K1–K9 关键点） |
| [DISTRIBUTION.md](DISTRIBUTION.md) | 分发、签名、WebView2 检测 |
| [ROADMAP.md](ROADMAP.md) | 开发路线图与里程碑 |
