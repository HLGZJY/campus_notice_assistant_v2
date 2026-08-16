# 开发路线图

> 维护说明：本文档按开发阶段记录已完成里程碑；每个阶段都有对应的详细实现文档（见各节引用）。
> 当前状态：**MVP（M1–M6）+ 短线开发（W1–W4）+ 前后端分离重构（Phase 0–8）均已完成**。

## 里程碑总览

| 阶段 | 内容 | 状态 |
| ---- | ---- | ---- |
| M1–M6 | MVP：抓取 / 提取 / 待办 / RAG 问答 / Streamlit 界面 / 配置 | ✅ 已完成 |
| W1 | 调度运维：调度器、内容指纹、断点续跑、token 计量、Tool Calling | ✅ 已完成 |
| W2 | 检索质量：测试集、纯向量基线、过期三档、混合检索、RAG 污染 | ✅ 已完成 |
| W3 | 订阅 + 提醒：规则引擎、截止提醒、两步式交互 + 全链路演示 | ✅ 已完成 |
| W4 | 埋点 + 终检：events 表、每日体检、7 天自运行终检 | ✅ 已完成 |
| Phase 0–8 | 前后端分离：FastAPI 后端 + Vue3 前端 + 异步任务 + SSE + Docker | ✅ 已完成 |

---

## MVP（M1–M6） ✅

> 详细现状：`docs-local/长线开发/现状盘点与前后端分离前置.md`

### M1：抓取 + 存储 ✅

**目标**：用 newspaper4k 抓取学校通知网站，存入 SQLite

- [x] 安装 newspaper4k，验证对 scuec 网站的提取效果
- [x] 设计 SQLite 表结构（`storage/db.py`）
- [x] 封装 newspaper4k 爬虫（`crawler/base.py`）：
  - [x] `Source.build()` 发现列表页文章链接
  - [x] `Article.download().parse()` 提取详情页
  - [x] 配置中文语言支持（`language='zh'`）
- [x] 实现网页爬虫（`crawler/web_crawler.py`）：从配置读取 list_url、URL 去重、抓取日志记录

**验收**：能抓取 scuec.edu.cn 多个栏目通知；通知存入 SQLite `status=raw`；重复运行不重复抓取。

### M2：结构化提取 ✅

**目标**：用 LLM 从通知正文提取结构化字段

- [x] `NoticeExtraction` Pydantic 模型（`core/models.py`）
- [x] 提取 Agent（`core/extractor.py`，`output_type` + 校验重试 ≤2 次）
- [x] 批量处理 `status=raw`（`extract.py`）+ schema 迁移
- [x] 截止时间双字段 `deadline_raw`（原文）+ `deadline`（ISO，`core/date_utils.py` 重算）
- [x] 黄金集评估（`data/golden_extraction.json` + `evaluate_extraction.py`）

**验收**：黄金集 24/24 满分；截止时间解析准确率 100%；通知类型分类正确。

### M3：待办生成 + 列表 ✅

- [x] `TodoItem` / `TodoList` 模型（`core/models.py`）
- [x] 待办生成 Agent（`core/todo.py`，按需生成 + `template_fallback` 兜底）
- [x] `todos` 表 + 按截止排序 + 状态管理（pending/done/skipped）
- [x] 小界面验证闭环（`ui/todo_app.py`，Streamlit，M5 后被替换）

> **MVP 形态决策**：待办采用"**用户点开通知才生成**"的按需模式（省 LLM 成本、避免过期噪声）。

### M4：RAG 问答 ✅

- [x] 向量索引（`storage/vectorstore.py`，Chroma + langchain 中文切分器）
- [x] 问答 Agent（`core/qa.py`，检索元数据确定性导出来源防引用幻觉）
- [x] 复用 fallback embedding（OpenAIEmbeddings → HuggingFaceEmbeddings）

### M5：Streamlit 界面整合 ✅

- [x] `services/` 服务层封装 M1–M4 能力供 UI 调用
- [x] `app.py` 仪表盘 + `pages/` 多页面（通知浏览 / 待办清单 / 智能问答）
- [x] 提取成功后自动增量更新 Chroma 索引

> 注：M5 的 Streamlit 界面在 Phase 0–8 重构中已由 Vue3 前端（`frontend/`）替换。

### M6：学校配置 + 模型配置 + CRUD 管理 ✅

- [x] YAML 配置体系（`config/schema.py` + `app.yaml` + `schools/*.yaml`），三层 fallback 加载
- [x] 模型按任务独立配置（extraction/qa/todo/embedding）
- [x] `utils/llm.py` / `utils/embedding.py` 从 ConfigStore 读取
- [x] 系统配置页 + 通知 CRUD（删除 / 重新提取 / 批量删除 / 索引重建）

---

## 短线开发（W1–W4） ✅

> 详细实现：`docs-local/短线开发/` 下各阶段文档。

### W1：调度运维

- [x] APScheduler 独立进程（`scheduler.py`）：crawl / extract / daily / reminder / config-watch 五个 job
- [x] 失败语义：不吞异常，落 `scheduler_log` 表（含连续失败计数）
- [x] 崩溃恢复：从库中恢复运行状态，URL UNIQUE 防重复
- [x] 内容指纹变更检测（`storage/db.py:compute_content_hash`，SHA-256 折叠空白）
- [x] 断点续跑（`extract.py:run_batch` + status 游标 + source 参数）
- [x] token 计量表（`utils/llm.py:record_llm_usage`，四条链路统一记账）
- [x] Tool Calling 演练（`tool_call_drill.py`：max_turns 守卫 + tool_call_id 去重）

### W2：检索质量

- [x] 20 题测试集（`data/`，retrieval eval testset：20 questions / 27 corpus notices）
- [x] 纯向量基线 + 过期三档实验（none/decay/filter，生产默认 none）
- [x] 混合检索（`storage/hybrid.py`：BM25+RRF k=60，jieba 分词，`search_mode="hybrid"`）
- [x] RAG 污染专项（见 `docs/RAG-POLLUTION.md`）

### W3：订阅 + 提醒

- [x] 订阅规则引擎（`services/subscription_service.py`，纯规则子串匹配非 LLM）
- [x] 命中关系表 + 抓取/提取后增量维护 + 全库回填
- [x] 截止提醒（`services/reminder_service.py`：3d/1d 档位，幂等扫描）
- [x] 两步式交互（W3 时代为 `ui/two_step.py`，Phase 0–8 已平移为「后端 preview/confirm API + 前端确认弹窗」）
- [x] 造数工具 + 全链路演示（`tools/seed_demo_data.py` / `tools/demo_reminder.py`，见 `docs/DEMO.md`）

### W4：埋点 + 终检

- [x] events 事件表（`services/tracking_service.py`：5 类事件，写库失败不阻塞主流程）
- [x] 每日体检自动化（`services/health_service.py` + scheduler daily job）
- [x] 7 天自运行终检（`check_db.py --summary` / `data/health/`）已积累完成

---

## 长线开发：前后端分离重构（Phase 0–8） ✅

> 执行依据：`docs-local/长线开发/现状盘点与前后端分离前置.md`（§5.6 映射表 / §5.7 序列化 / §5.8 并发语义）
> 执行计划：`docs-local/长线开发/重构计划.md`

**目标**：将现有功能平移至前后端分离工程（零新增功能），架构为后续新功能 + APP 端预留拓展位。

| 阶段 | 内容 | 状态 |
| ---- | ---- | ---- |
| Phase 0 | 仓库初始化（`campus_notice_assistant_v2`，legacy 历史只读引用） | ✅ |
| Phase 1 | FastAPI 脚手架 + 通知只读模块（`api/`） | ✅ |
| Phase 2 | 待办 / 提醒 / 订阅模块 + 两步式 preview/confirm API | ✅ |
| Phase 3 | 配置模块（`app.yaml` 写入权唯一归后端 API 进程） | ✅ |
| Phase 4 | 异步任务化（tasks 表 + TaskManager，202 → 轮询） | ✅ |
| Phase 5 | 问答 SSE 流式（`GET /api/v1/qa/ask/stream`） | ✅ |
| Phase 6 | scheduler 并入后端进程（lifespan 拉起 + CLI 兼容） | ✅ |
| Phase 7 | 前端（Vue3 + Vite + Naive UI，7 路由平移） | ✅ |
| Phase 8 | 收尾（静态挂载 + SPA fallback + Docker + README） | ✅ |

## 阶段 7 优化：增量抓取 + 提取预筛 ✅

> 执行依据：`docs-local/短线开发/资源优化方向.md`；目标：降低抓取/LLM 资源消耗（全量抓取 → 增量 + 深检，无差别提取 → 规则预筛）。

- [x] 配置扩展：来源级策略（enabled / crawl_mode / max_age_days / fetch_detail / deep_check）+ 全局抓取参数（早停/超时/重试/并发/深检周期）+ 提取前置过滤参数（`config/schema.py`，全带默认值向后兼容）
- [x] 增量抓取核心（`crawler/web_crawler.py`）：已入库不重抓详情、整页已知早停、时效过滤、详情并发 + 指数退避重试、deep_check 指纹深检
- [x] 存储层：`notices.extract_skipped_reason` 列（迁移）+ `mark_prefiltered` / `clear_prefiltered` / `exclude_prefiltered` 游标
- [x] 提取预筛（`services/notice_service.py:prefilter_notice`）：时效 → 长度 → 关键词 → 黑名单 → 时间线索 → 仅订阅命中；跳过项不调 LLM 且不再重复判定
- [x] API / 任务层：`GET/PUT /config/crawl`、`GET/PUT /config/extract`、抓取任务参数透传、`test-source` 返回 `suggested_pattern` 建议正则
- [x] 前端：数据源表单新字段 + 测试链接自动填充 URL 模式 + 「抓取与提取」配置 tab + 抓取/深度抓取对话框 + 「已跳过提取」标签
- [x] 测试：早停/时效/深检/预筛单测（`test_incremental_crawl.py`）+ 既有测试适配（`prefilter=False` / `deep_check=True` / 调度桩 `**kwargs`）+ 实测（6 源全库一轮 ≈ 3.5s，深检 13 条 ≈ 5s）

---

### Phase 0：仓库初始化 ✅

- 新仓库 `campus_notice_assistant_v2`；旧仓库封存只读（`legacy/main`）。
- 挑选拷贝引擎代码 + 数据，`.gitignore` 排除 `.env` / `data/notices.db` / `data/chroma/` 等。

### Phase 1–3：后端模块 ✅

- `api/main.py`：应用工厂、`include_router(prefix="/api/v1")`、lifespan 拉起 TaskManager + scheduler、CORS 白名单。
- `api/deps.py`：鉴权占位（`CAMPUS_API_KEY` 环境变量，默认关闭；未来 JWT 替换点）。
- `api/schemas.py`：pydantic 响应模型，services 返回 dict 直接 `model_validate`（§5.7 无转换器）。
- 路由模块：`notices` / `todos` / `reminders` / `subscriptions` / `config` / `qa` / `events` / `tasks` / `scheduler`。

### Phase 4：异步任务化 ✅

- `tasks` 表（queued/running/success/failed + progress）+ `api/tasks/manager.py`（单 worker 串行，规避 SQLite 单写者 / Chroma 单 collection 并发冲突）。
- 长耗时操作全部走「提交任务 → 202 task_id → 轮询」：单源/全部抓取、批量提取、订阅新增/编辑回填、全库重匹配、索引重建、待办生成、单条重提取、批量删除/重置。
- 重启恢复：遗留 queued/running 标记 failed（可重新提交）。

### Phase 5：问答 SSE 流式 ✅

- `GET /api/v1/qa/ask/stream?question=`：`StreamingResponse`，事件负载 `delta` / `done`（含 sources / retrieved_chunks）/ `error`。
- `core/qa.py` 的 `QAResult` 是 services 返回 dict 约定的唯一例外，路由层做 `as_source` 序列化（§5.7）。

### Phase 6：scheduler 并入 ✅

- `scheduler.py` 抽出 `start_scheduler(config)` 可导入函数，`api/main.py` lifespan 拉起（单进程，符合 §5.8 写入权唯一）。
- `--no-*` 开关 → `config/app.yaml` 的 `scheduler.enabled / enable_daily / enable_extract / enable_reminder / enable_health`。

### Phase 7：前端 Vue3 ✅

> 详细落地：`docs-local/短线开发/阶段7进度.md`

- 技术栈：Vue 3 + Vite + Naive UI + vue-router + pinia + openapi-typescript（Node 20 兼容版本：Vite 5.4 / TS 5.6 / vue-tsc 2.2）。
- 契约对齐：`frontend/openapi.json` → `npm run gen:api` → `src/api/types.ts`；`src/api/schema.ts` 集中 `export type … = components['schemas'][…]` 别名。
- 7 路由：`/`（首页）、`/notices`、`/todos`、`/qa`、`/config`、`/subscriptions`、`/market`。
- 埋点：前端只发 `POST /api/v1/events`，逻辑留后端 tracking_service。

### Phase 8：收尾 ✅

- 后端静态挂载 `frontend/dist` + SPA fallback（所有非 API 路由返回 index.html）。
- Multi-stage Dockerfile（Node 构建 frontend → Python 运行环境拷贝 dist）。
- 依赖拆分：`requirements-backend.txt`（运行镜像最小包）/ `requirements-dev.txt` / `requirements.txt`（引擎/开发）。

---

## 当前架构速览

```
frontend/   Vue3 + Naive UI（7 路由）──▶ POST /api/v1/events（埋点）
   │
   │ HTTP /api/v1（openapi.json 契约对齐）
   ▼
api/        FastAPI 应用工厂 + 9 个路由模块 + deps 鉴权占位
   ├── tasks/    TaskManager（asyncio 单 worker + 202 轮询 + 重启恢复）
   ├── lifespan  拉起 scheduler（APScheduler，5 job）与 TaskManager
   ▼
services/   业务编排层（notice / todo / qa / subscription / reminder /
            config / admin / tracking / health / usage）
   ▼
core/ + storage/ + crawler/ + config/ + utils/   引擎层（不变）
```

---

## 后期规划（P1）

| 功能 | 描述 | 预计 |
| ---- | ---- | ---- |
| 多学校适配 | 同时支持多所学校 | 1 天 |
| 站外主动推送 | 邮件 / 微信 / 桌面通知 | 2 天 |
| 多用户 + 鉴权 | 账号系统、个人待办（deps.py 预留替换点） | — |
| APP 端 | 复用同一 services/API 层 | — |