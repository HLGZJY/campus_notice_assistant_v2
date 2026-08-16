# 数据模型设计

> 维护说明：本文档对应**当前** SQLite schema（`storage/db.py` SCHEMA + 迁移）。
> 共 **10 张表**：notices / crawl_log / scheduler_log / todos / reminders / token_usage /
> subscriptions / notice_subscription_matches / events / tasks。

## 1. SQLite 表结构

### 1.1 notices（通知表）

```sql
CREATE TABLE notices (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    url TEXT UNIQUE NOT NULL,           -- 通知详情页 URL（去重依据）
    source TEXT NOT NULL,               -- 来源：scuec/cxcy / 演示数据
    title TEXT NOT NULL,                -- 原始标题
    raw_content TEXT,                   -- 原始正文
    published_at TEXT,                  -- 发布时间（ISO 8601）
    crawled_at TEXT NOT NULL,           -- 抓取时间
    status TEXT DEFAULT 'raw',          -- raw/extracted/partial/failed
    -- M2 迁移新增列（_MIGRATIONS ALTER 补齐）
    content_hash TEXT,                  -- 正文内容指纹（SHA-256，变更检测）
    notice_type TEXT,                   -- competition/lecture/registration/...
    target_audience TEXT,               -- 面向对象
    signup_method TEXT,                 -- 报名方式（QQ群/邮箱/扫码描述，自由文本）
    signup_url TEXT,                    -- 报名网页链接（仅当有真实 URL）
    location TEXT,                      -- 地点
    location_type TEXT,                 -- online/offline/hybrid
    deadline TEXT,                      -- 截止时间（ISO 8601，解析器重算）
    deadline_raw TEXT,                  -- 截止时间原文片段（可溯源/校验）
    key_dates_json TEXT,                -- 其他重要时间点（JSON 数组）
    summary TEXT,                       -- 摘要
    extracted_at TEXT,                  -- 提取时间
    -- 阶段 7 迁移新增列（_MIGRATIONS ALTER 补齐）
    extract_skipped_reason TEXT         -- 提取前置过滤跳过原因（NULL=未跳过/候选）
);

CREATE INDEX idx_notices_status ON notices(status);
CREATE INDEX idx_notices_source ON notices(source);
```

> `idx_notices_deadline` / `idx_notices_type` 在 SCHEMA 中未显式建立，由查询按需使用已有索引。

### 1.2 crawl_log（抓取日志）

```sql
CREATE TABLE crawl_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT NOT NULL,
    total_discovered INTEGER,
    total_new INTEGER,
    total_skipped INTEGER,              -- 已存在且正文未变更
    total_changed INTEGER,              -- 已存在但正文已变更（内容指纹，W1.2 迁移新增列）
    total_failed INTEGER,
    errors TEXT,
    crawled_at TEXT NOT NULL
);
```

### 1.3 scheduler_log（调度运行记录，W1）

```sql
CREATE TABLE scheduler_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_name TEXT NOT NULL,             -- crawl / extract / daily / reminder / config-watch
    status TEXT NOT NULL,               -- success / failed
    started_at TEXT NOT NULL,
    finished_at TEXT,
    duration_ms INTEGER,
    failure_count INTEGER DEFAULT 0,    -- 该 job 本次运行时的连续失败次数
    message TEXT,
    details TEXT                        -- JSON 详情
);

CREATE INDEX idx_scheduler_log_job ON scheduler_log(job_name);
CREATE INDEX idx_scheduler_log_started ON scheduler_log(started_at);
```

> **设计要点**：调度器每个 job 运行都会落库，失败不吞异常；重启时打印最近运行记录
> （崩溃恢复，见 `scheduler.py:print_recovery_info`）。

### 1.4 todos（待办表）

```sql
CREATE TABLE todos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    notice_id INTEGER NOT NULL,          -- 关联通知
    action TEXT NOT NULL,                -- 待办内容，如"提交数学建模报名表"
    due_at TEXT,                         -- 截止时间
    priority TEXT DEFAULT 'normal',      -- high/normal/low（核心模型字段）
    status TEXT DEFAULT 'pending',       -- pending/done/skipped
    created_at TEXT NOT NULL,
    completed_at TEXT,
    notes TEXT,                          -- 备注（待办中心编辑/延期后记进展，迁移新增列）
    FOREIGN KEY (notice_id) REFERENCES notices(id)
);

CREATE INDEX idx_todos_notice ON todos(notice_id);
CREATE INDEX idx_todos_status ON todos(status);
CREATE INDEX idx_todos_due ON todos(due_at);
```

### 1.5 token_usage（LLM 计量账本，W1）

```sql
CREATE TABLE token_usage (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task TEXT NOT NULL,                  -- extraction / qa / todo / embedding
    model TEXT,
    notice_id INTEGER,                   -- 关联通知（提取任务），可空
    input_tokens INTEGER DEFAULT 0,
    output_tokens INTEGER DEFAULT 0,
    success INTEGER DEFAULT 1,           -- 1 成功 / 0 失败
    retry_count INTEGER DEFAULT 0,       -- 该次调用是第几次尝试（0 表示首调）
    error TEXT,
    created_at TEXT NOT NULL
);

CREATE INDEX idx_token_usage_task ON token_usage(task);
CREATE INDEX idx_token_usage_notice ON token_usage(notice_id);
CREATE INDEX idx_token_usage_created ON token_usage(created_at);
```

> **设计要点**：`utils/llm.py:run_agent` 是唯一 LLM 调用点，四条链路（提取/待办/问答/embedding）
> 统一记账；失败也记录（success=0 + error），供 token 用量页统计。

### 1.6 subscriptions（订阅表，W3 模块 3.1）

```sql
CREATE TABLE subscriptions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    keyword TEXT NOT NULL,              -- 订阅词
    notice_type TEXT,                   -- 关注的通知类型（NULL = 不限类型）
    enabled INTEGER DEFAULT 1,          -- 1 启用 / 0 停用
    created_at TEXT NOT NULL
);

CREATE INDEX idx_subscriptions_enabled ON subscriptions(enabled);
```

### 1.7 notice_subscription_matches（订阅命中关系，W3 模块 3.1）

```sql
CREATE TABLE notice_subscription_matches (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    notice_id INTEGER NOT NULL,          -- 关联通知
    subscription_id INTEGER NOT NULL,    -- 命中的订阅
    matched_at TEXT NOT NULL,
    UNIQUE(notice_id, subscription_id),  -- 幂等：同一对只保留一条
    FOREIGN KEY (notice_id) REFERENCES notices(id),
    FOREIGN KEY (subscription_id) REFERENCES subscriptions(id)
);

CREATE INDEX idx_nsm_notice ON notice_subscription_matches(notice_id);
CREATE INDEX idx_nsm_subscription ON notice_subscription_matches(subscription_id);
```

> **设计要点（确定性匹配引擎）**
> - 匹配为纯规则、不用 LLM：订阅词对通知的 `title` / `summary` 做大小写不敏感的
>   子串匹配；订阅限定了 `notice_type` 时还需类型完全相等。
> - 抓取插入/内容变更后（`crawler/web_crawler.py`）、提取成功后
>   （`services/notice_service.py`）都会调用 `match_notice()` 增量维护命中关系。
> - 新增/修改订阅后调用 `match_all_notices()` 全库回填（异步任务），保证已有通知也被标记。
> - 删除通知 / 来源 / 状态、停用订阅、修改订阅时都会级联清理命中关系，避免陈旧数据。

### 1.8 reminders（截止提醒表，W3 模块 3.2）

```sql
CREATE TABLE reminders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    notice_id INTEGER NOT NULL,          -- 关联通知（恒非空：通知路与待办兜底路都带）
    todo_id INTEGER,                     -- 关联待办（有待办时挂上，可空，仅展示用）
    due_at TEXT NOT NULL,                -- 截止时间（复用 notice.deadline / todo.due_at）
    tier TEXT NOT NULL,                  -- 提醒档位：3d / 1d
    remind_on TEXT NOT NULL,             -- 触发日期 YYYY-MM-DD（幂等键）
    status TEXT DEFAULT 'pending',       -- pending / read / ignored
    created_at TEXT NOT NULL,
    read_at TEXT,
    UNIQUE(notice_id, tier, remind_on),  -- 幂等：同一天同一对象同一档位不重复
    FOREIGN KEY (notice_id) REFERENCES notices(id),
    FOREIGN KEY (todo_id) REFERENCES todos(id)
);

CREATE INDEX idx_reminders_status ON reminders(status);
CREATE INDEX idx_reminders_due ON reminders(due_at);
```

> **设计要点（截止提醒）**
> - 以通知为对象粒度：每条有截止时间的通知只生成一条提醒；存在待处理待办时
>   同时挂上 `todo_id`（UI 可展示待办动作文案）。
> - 幂等键只用恒非空的 `notice_id`（+`tier`+`remind_on`）：SQLite 将 UNIQUE 列中的
>   `NULL` 视为互异，若把可空的 `todo_id` 纳入唯一键，无待办的提醒会失去幂等性。
> - 生成链路不依赖 UI：调度器 reminder job（每日 03:00，API lifespan 或 CLI 拉起）
>   调用 `services/reminder_service.scan_reminders()` 扫描写入，UI 只读。
> - 数据卫生：待办完成/跳过后其待处理提醒自动置 `read`；删除通知 / 来源 / 状态、
>   删除单条待办时级联删除关联提醒。待办 `due_at` 变更（PATCH）时旧临期提醒自动收敛为已读。

### 1.9 events（事件埋点表，W4 模块 4.1）

```sql
CREATE TABLE events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_type TEXT NOT NULL,          -- 事件类型：page_view / qa_ask / todo_generate / todo_done / service_button_click
    ref_id INTEGER,                    -- 关联对象 id（notice_id / todo_id / 服务 key），可空
    note TEXT,                         -- 备注（页面标识 / 问题文本 / 服务名），可空
    event_at TEXT NOT NULL             -- 事件时间（ISO 8601）
);

CREATE INDEX idx_events_type ON events(event_type);
CREATE INDEX idx_events_at ON events(event_at);
```

> **设计要点（埋点）**
> - **纯追加事件日志**：不建外键、不做级联清理——删除通知/待办不影响历史事件，
>   `ref_id` 只是普通整数，允许悬空引用。
> - **确定性代码、纯本地落库**：`services/tracking_service.track_event()` 每次自开
>   独立短连接写入，整体 try/except 兜底，异常只记日志返回 False，绝不阻塞主流程。
> - **上报入口**：前端 `POST /api/v1/events`（fire-and-forget 吞错）；Vue 路由守卫按
>   路由去重发 `page_view`，替代 Streamlit 的 `st.session_state` 会话去重。
> - **服务按钮点击**（门控 #1）：`frontend/src/views/MarketView.vue` 假「下单」按钮
>   点击记录 `service_button_click`（ref_id=服务 key，note=服务名）。
> - 消费方：W4 周报（汇总打开频率/问答次数/待办点击率/服务点击）。

### 1.10 tasks（异步任务表，Phase 4）

```sql
CREATE TABLE tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    type TEXT NOT NULL,                -- crawl_source / crawl_all / extract_batch /
                                       -- subscription_add / subscription_update / match_all /
                                       -- rebuild_index / generate_todos / re_extract_notice /
                                       -- batch_delete / batch_reset
    params_json TEXT,                  -- 请求参数 JSON
    status TEXT NOT NULL DEFAULT 'queued',  -- queued / running / success / failed
    progress REAL NOT NULL DEFAULT 0,  -- 0.0 ~ 1.0
    result_json TEXT,
    error TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX idx_tasks_status ON tasks(status);
CREATE INDEX idx_tasks_type ON tasks(type);
CREATE INDEX idx_tasks_created ON tasks(created_at);
```

> **设计要点（异步任务，Phase 4）**
> - 所有长耗时操作（抓取/批量提取/订阅回填/全库重匹配/索引重建/待办生成/批量管理）统一走
>   任务模型：`POST /api/v1/tasks` → 202 `task_id` → 前端 `GET /tasks/{id}` 轮询。
> - 执行器：`api/tasks/manager.py`（asyncio 单 worker 串行，规避 SQLite 单写者 /
>   Chroma 单 collection 并发冲突）；阻塞业务函数经 `asyncio.to_thread` 运行。
> - 重启恢复：遗留 queued/running 任务标记 failed（记录保留，用户可重新提交），
>   复用 scheduler_log 的"落库即恢复"语义。
> - `type` 注册表见 `api/tasks/workers.py` 的 `WORKERS`。

## 2. Pydantic 模型

### 2.1 通知提取结果

```python
from pydantic import BaseModel
from typing import Literal, Optional

# 通知类型枚举（10 类）
NoticeType = Literal[
    "competition",   # 竞赛
    "lecture",       # 讲座
    "registration",  # 报名/培训/选课
    "scholarship",   # 奖学金
    "administrative",# 行政事务（放假/注册/缴费）
    "recruitment",   # 招聘/实习
    "policy",        # 政策/资讯
    "result",        # 结果公示
    "news",          # 动态/新闻
    "other",         # 其他
]

class KeyDate(BaseModel):
    """一个重要的日期/时间点（如报名截止、初赛、决赛）。"""
    label: str             # 时间点含义，如"报名截止""初赛"
    date_raw: str          # 原文时间片段，如"5月23日12:00-17:00"
    datetime: str | None   # 规范化 ISO 8601（后处理填充）

class NoticeExtraction(BaseModel):
    """LLM 结构化提取输出"""
    notice_type: NoticeType
    title: str
    target_audience: str | None = None
    signup_method: str | None = None   # 报名方式自由文本（QQ群/邮箱/扫码）
    signup_url: str | None = None      # 报名网页链接（仅当有真实 URL）
    location: str | None = None
    location_type: Literal["online", "offline", "hybrid"] | None = None
    deadline_raw: str | None = None    # 截止时间原文片段（防幻觉/可校验）
    deadline: str | None = None        # ISO 8601（Python 解析器以 deadline_raw 重算为准）
    key_dates: list[KeyDate] = []
    summary: str | None = None
```

> **设计要点**
> - `deadline_raw` + `deadline` 双字段：LLM 只负责从原文中定位时间片段，
>   年份推断与 ISO 规范化由 `core/date_utils.py` 的解析器完成，可校验防幻觉。
> - `signup_method` 为自由文本：真实通知里 QQ 群号、邮箱、扫码占绝大多数。
> - 状态三态：`extracted`（行动型且有行动字段）/ `partial`（政策/新闻/结果公示等
>   非行动型）/ `failed`（LLM 调用本身失败）。
> - 行动型类型 = competition/lecture/registration/scholarship/administrative/recruitment；
>   非行动型 = policy/result/news/other。
> - `extract_skipped_reason`：阶段 7 提取前置过滤跳过原因（正文过短/关键词不中/时间线索缺失等）。
>   状态保持 `raw`，批量提取游标默认排除（`exclude_prefiltered=True`）；
>   「重置提取」或正文变更（update_notice_content）会清空该标记，恢复候选资格。
> - `skip_llm` 省 token 模式：`config.extract.skip_llm=true` 时通知不调 LLM，仅订阅匹配 + 建向量索引，
>   状态置 `partial`（此时 partial = 仅索引未结构化，结构化字段为空）。

### 2.2 待办项

```python
class TodoItem(BaseModel):
    """单条待办"""
    action: str           # 待办内容
    due_at: str | None    # 截止时间
    priority: str = "normal"  # high/normal/low

class TodoList(BaseModel):
    """待办清单"""
    items: list[TodoItem]
```

### 2.3 API 响应模型（`api/schemas.py`）

> 前后端分离后，前端消费的是 `api/schemas.py` 的 pydantic 响应模型（services 返回 dict 直接
> `model_validate`，见盘点 §5.7），不再是 Streamlit 时代的 NoticeCard。示例：

```python
class NoticeSummary(BaseModel):
    """通知列表项（浏览页卡片，避免整条 raw_content 传输）"""
    id: int
    url: str
    source: str
    title: str
    published_at: str | None
    crawled_at: str
    status: str
    notice_type: str | None
    deadline: str | None
    summary: str | None
    keywords: list[str] = []   # 订阅命中词（浏览页徽标用）

class NoticePage(BaseModel):
    """通知列表分页信封（items + total + page + page_size）"""
    items: list[NoticeSummary]
    total: int = 0
    page: int = 1
    page_size: int = 20
```

## 3. 向量库结构（Chroma）

```python
collection = chroma_client.create_collection(
    name="notices",
    metadata={"hnsw:space": "cosine"}
)

# 每条文档：
{
    "id": "notice_{notice_id}_chunk_{chunk_idx}",
    "embedding": [...],  # 384 维（bge-small-zh-v1.5）
    "document": "通知正文片段",
    "metadata": {
        "notice_id": 123,
        "title": "通知标题",
        "notice_type": "competition",
        "source": "scuec/cxcy"
    }
}
```

> Embedding 模型：本地 `models/bge-small-zh-v1.5`（中文检索效果优于原 all-MiniLM-L6-v2）。

## 4. ER 关系

```mermaid
erDiagram
    notices ||--o{ todos : "生成"
    notices ||--o{ crawl_log : "记录"
    notices ||--o{ notice_subscription_matches : "命中"
    subscriptions ||--o{ notice_subscription_matches : "订阅命中"
    notices ||--o{ reminders : "触发"
    todos ||--o{ reminders : "挂接"
    notices {
        int id PK
        string url UK
        string title
        string notice_type
        string deadline
        string status
        string content_hash
    }
    todos {
        int id PK
        int notice_id FK
        string action
        string due_at
        string priority
        string status
        string notes
    }
    subscriptions {
        int id PK
        string keyword
        string notice_type
        int enabled
    }
    notice_subscription_matches {
        int id PK
        int notice_id FK
        int subscription_id FK
        string matched_at
    }
    reminders {
        int id PK
        int notice_id FK
        int todo_id FK
        string due_at
        string tier
        string remind_on
        string status
    }
    crawl_log {
        int id PK
        string source
        int total_discovered
        int total_new
        int total_skipped
        int total_changed
        int total_failed
    }
    events {
        int id PK
        string event_type
        int ref_id
        string note
        string event_at
    }
    token_usage {
        int id PK
        string task
        int input_tokens
        int output_tokens
        int success
    }
    scheduler_log {
        int id PK
        string job_name
        string status
        int failure_count
    }
    tasks {
        int id PK
        string type
        string status
        float progress
    }
```