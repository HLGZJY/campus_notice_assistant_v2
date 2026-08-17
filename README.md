# 校园通知智能助手

> Campus Notice Assistant — 把校园通知变成可执行的待办清单

## 这是什么

一个能自动抓取学校各类通知网站、用 LLM 提取关键信息（截止时间、地点、报名链接、面向对象）、生成个性化待办清单的智能助手。

- **前端**：Vue 3 + Vite + Naive UI（`frontend/`）
- **后端**：FastAPI + uvicorn（`api/`，`/api/v1`），复用 MVP 时代的 services/引擎层
- 本项目源于 `Llama 3.1 本地 RAG` 项目的延伸，从"与单个网页对话"演进到"自动监控多来源通知 + 结构化提取 + 待办管理"。

## 核心能力

- **多来源抓取**：学校官网、学院/部门网站、教务处通知
- **结构化提取**：自动识别通知类型、截止时间、地点、报名链接、面向对象
- **待办生成**：把通知转成可执行的待办项（按需生成，支持编辑/延期/备注）
- **截止提醒**：对截止前 3 天 / 1 天的通知自动生成站内提醒（首页红点 + 待办中心提醒区，可已读/忽略），由调度器扫描生成，不依赖前端
- **智能问答**：基于已抓取的通知回答自然语言问题（混合检索 + SSE 流式）
- **关键词订阅**：纯规则匹配，命中通知自动标记 + 全库回填
- **数据管理**：通知删除/重置/重新提取/批量操作（异步任务化）
- **学校可配置**：通用架构，通过配置文件适配不同学校

## 架构速览

```
frontend/   Vue3 + Naive UI（7 路由）──▶ POST /api/v1/events（埋点）
   │ HTTP /api/v1（openapi.json 契约对齐，openapi-typescript 生成类型）
   ▼
api/        FastAPI 应用工厂 + 9 路由模块 + deps 鉴权占位
   ├── tasks/    TaskManager（asyncio 单 worker，长耗时 202→轮询）
   └── lifespan  拉起 scheduler（APScheduler，5 job）与 TaskManager
   ▼
services/   业务编排层（notice / todo / qa / subscription / reminder /
            config / admin / tracking / health / usage）
   ▼
core/ + storage/ + crawler/ + config/ + utils/   引擎层
```

## 文档导航

| 文档                                           | 内容                         | 给谁看    |
| ---------------------------------------------- | ---------------------------- | --------- |
| [docs/PRD.md](docs/PRD.md)                     | 产品需求、用户故事、功能清单 | 产品/需求 |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)   | 技术架构、模块设计、数据流   | 开发      |
| [docs/DATA-MODEL.md](docs/DATA-MODEL.md)       | 数据表结构、Pydantic 模型    | 开发      |
| [docs/ROADMAP.md](docs/ROADMAP.md)             | 开发路线图、里程碑           | 项目管理  |
| [docs/DEMO.md](docs/DEMO.md)                   | 订阅+提醒全链路演示          | 演示      |
| [docs/RAG-POLLUTION.md](docs/RAG-POLLUTION.md) | RAG 污染防护专项             | 开发      |

> 更细的开发记录见 `docs-local/`（短线开发各阶段 / 长线开发前后端分离）。

## 技术栈

| 层         | 选型                                         | 说明                                    |
| ---------- | -------------------------------------------- | --------------------------------------- |
| 后端框架   | FastAPI + uvicorn                            | `/api/v1`，OpenAPI 契约导出             |
| 前端       | Vue 3 + Vite + Naive UI                      | vue-router + pinia + openapi-typescript |
| LLM        | 可配置（默认阿里云百炼 bailian qwen3.7-max） | OpenAI 兼容接口，按任务选择模型         |
| Embedding  | 本地 `models/bge-small-zh-v1.5`              | 本地轻量模型，中文检索效果好            |
| 向量库     | Chroma                                       | 轻量，嵌入式                            |
| Agent 框架 | OpenAI Agents SDK                            | Capstone 课程要求                       |
| 数据存储   | SQLite（10 张表）                            | 轻量，单文件                            |
| 定时调度   | APScheduler（5 job）                         | 并入后端 lifespan，可 CLI 独立运行      |
| 配置       | YAML + 环境变量                              | `config/app.yaml` / `.env`              |
| 抓取       | newspaper4k                                  | 列表页发现 + 详情页提取                 |

## 快速开始

### 后端

```bash
# 1. 安装后端依赖（引擎 + 开发依赖；运行镜像最小包见 requirements-backend.txt）
pip install -r requirements.txt

# 2. 配置环境变量
cp .env.example .env
# 编辑 .env 填入 API key（如 DASHSCOPE_API_KEY，供应商与 app.yaml 的 api_key_env 对应）

# 3. 确认 / 修改配置（二选一）
# 方式 A：直接编辑 YAML
code config/app.yaml          # 模型 / 供应商 / 活跃学校 / 调度开关
code config/schools/scuec.yaml # 数据源
# 方式 B：启动后在「系统配置」页面可视化修改

# 4. 启动后端（数据库首次连接自动建表，无需手动初始化）
.venv\Scripts\python.exe -m uvicorn api.main:app --host 0.0.0.0 --port 8000
```

### 前端

```bash
cd frontend
npm install

# 开发模式（/api 代理到 127.0.0.1:8000）
npm run dev            # http://localhost:5173

# 或构建产物后由后端直接提供（SPA fallback 返回 index.html）
npm run build
# 访问 http://localhost:8000
```

### 常用 CLI / 脚本（复用同一 services 层）

```bash
# 抓取 / 提取 / 索引 / 问答 / 待办（M1–M4 入口，运维/调试用）
python crawl.py                        # 抓取全部数据源
python crawl.py --source 教务处-通知公告   # 只抓指定来源
python extract.py                      # 批量提取 status=raw 的通知（带前置过滤）
python extract.py --no-prefilter       # 关闭提取前置过滤（全部调 LLM）
python extract.py --status failed      # 重试提取失败的通知
python index.py                        # 把已提取通知切分并索引到 Chroma
python qa.py "最近有哪些比赛？"         # 单次问答
python todo.py --list                  # 待办清单（按截止升序）

# 调度器（独立运行；API lifespan 已自动拉起，二选一）
python scheduler.py                    # 前台运行
python scheduler.py --once             # 只跑一轮完整闭环后退出（验证用）
python scheduler.py --interval 1       # 覆盖抓取间隔为 1 分钟（快速验证）

# 订阅 + 截止提醒全链路演示（W3，5 分钟，详见 docs/DEMO.md）
python tools/demo_reminder.py --demo   # 发布→命中→提醒→待办→用户处理，自动校验幂等
python tools/demo_reminder.py --clean  # 清理演示数据（只清 source="演示数据"，不碰真实数据）

# 评估 / 检查
python evaluate_extraction.py          # 用黄金集评估提取准确率
python check_vector_consistency.py     # 向量一致性检查（RAG 污染防护）
python check_db.py --summary           # 每日体检汇总
```

> **两步式交互**：订阅新增/编辑、重匹配全部通知、提醒「忽略」均为「第一步预览 → 第二步确认执行」，
> 长时写库操作经异步任务（202 → `GET /tasks/{id}` 轮询）执行，前端用 `useTaskPoll` 展示进度。

## 调度器（scheduler.py）

基于 APScheduler，由 API lifespan 拉起（`scheduler.enabled` 控制）或 CLI 独立运行。五个 job：

| job          | 触发                                                           | 说明                                                                                          |
| ------------ | -------------------------------------------------------------- | --------------------------------------------------------------------------------------------- |
| crawl        | 每 `crawl.interval_minutes`（默认 60，运行中改配置自动热更新） | 抓取所有数据源（增量模式：已入库不重抓，整页已知立即早停；每 `deep_check_interval_cycles` 轮自动深检一轮内容变更） |
| extract      | 紧跟抓取（晚 20s）                                             | 提取 `status=raw` 的通知（先按 `extract` 前置过滤规则预筛，跳过项不调 LLM），成功后增量索引  |
| daily        | 每日 03:00                                                     | 过期清理（默认只报告不删除；`cleanup_enabled=true` 才删）+ 向量一致性检查（自动清理幽灵向量） |
| reminder     | 每日 03:00                                                     | 截止提醒扫描：对截止前 3 天 / 1 天的通知生成提醒，幂等                                        |
| config-watch | 每 60s                                                         | 监控配置变更，热更新抓取间隔等                                                                |

- 失败不吞异常：异常写日志 + 落 `scheduler_log` 表（含连续失败计数），下一周期自动重跑。
- 崩溃恢复：每次运行落库，重启时打印最近运行记录；已抓 URL 由 `notices.url UNIQUE` 去重，kill 后重启不会重复抓取。
- `config/app.yaml` 的 `scheduler.enabled / enable_daily / enable_extract / enable_reminder / enable_health` 对应 CLI 的 `--no-*` 开关。
- 验证自动抓取：把 `crawl.interval_minutes` 改成 1，重启，观察 `data/logs/scheduler.log` 每分钟一轮抓取。

### 增量抓取与提取预筛（阶段 7）

- **增量抓取（默认）**：每轮只抓「新 URL」的详情页；已入库通知不再重抓，列表页出现「整页全部已知」立即停止翻页。
  首轮全量入库后，常规轮次单来源耗时从分钟级降到秒级（实测 6 源全库一轮 ≈ 3.5s）。
- **深度变更检测**：内容更新检测改为两种方式——每 `crawl.deep_check_interval_cycles` 轮调度器自动深检一轮
  （默认 24 轮 ≈ 每日一次），或前端「深度抓取」按钮手动触发；深检会重抓已入库详情页比对内容指纹，
  有变更则重置为待提取。手动深检 = 抓取对话框打开「深度检查」开关后执行。
- **来源级策略**：每个数据源可配置 `enabled`（停用后定时/全量抓取跳过）、`crawl_mode`（incremental / full / list_only）、
  `max_age_days`（只抓最近 N 天）、`fetch_detail`（关闭则仅收录标题/链接）、`deep_check`（是否参与周期深检）。
- **手动抓取**：通知列表「抓取」按钮打开对话框——数据源多选（不选 = 全部启用来源，停用来源始终跳过）、
  模式 / 最大页数 / 深度检查开关；勾选只抓选中的来源（不再抓未选来源）。
- **提取前置过滤**：批量提取前按 `config.extract` 规则预筛（时效 → 正文长度 → 关键词白名单 → 标题黑名单 →
  时间线索 → 仅订阅命中），不通过的通知**不调 LLM**，落 `extract_skipped_reason` 并保持 raw 状态，
  后续轮次不再重复判定；「重置」或正文变更会清除该标记恢复候选资格。时效按**发布时间**计算
  （发布时间缺失时回退抓取时间）。
- **提取前预览**：通知列表「批量提取」先弹预览（`POST /notices/extract-preview`，dry-run 预筛），
  展示将提取/跳过明细及跳过原因，可取消勾选后只提取选中的通知（提交 `notice_ids`）。
- **跳过 LLM 提取**：`config.extract.skip_llm=true` 时不调 LLM，通知仅入库 + 建向量索引（状态置「部分提取」），
  最省 Token 模式；问答（RAG）不受影响（索引的一直是全文）。
- **任务进度**：抓取/批量提取任务运行时按钮区显示实时进度条（后端任务系统原生支持 progress 上报）。

### Windows 后台运行（独立 CLI 方式）

- **推荐（任务计划程序）**：`schtasks /create /tn "notice_scheduler" /tr "F:\...\.venv\Scripts\python.exe F:\...\scheduler.py" /sc onlogon /f`，开机自动后台运行；`schtasks /end /tn "notice_scheduler"` 停止。
- **无窗口隐藏启动**：PowerShell `Start-Process -FilePath ".venv\Scripts\python.exe" -ArgumentList "scheduler.py" -WindowStyle Hidden`，日志写在 `data/logs/scheduler.log`。
- **当前会话后台**：`start /B python scheduler.py`（关控制台即停，适合临时测试）。
- 停止：`taskkill /F /IM python.exe`（会停掉所有 python 进程，慎用）或通过任务计划程序停止。

## 配置说明

配置文件位于 `config/`：

- `config/app.yaml`：应用主配置，包含 `active_school`、`models`（按任务配置模型）、`providers`（供应商注册表）、`crawl`（全局抓取参数）、`extract`（提取前置过滤参数）、`scheduler`（调度开关）。
- `config/schools/<code>.yaml`：学校数据源配置，每个学校一个文件（含来源级抓取策略）。
- `.env`：存放 API key 等敏感信息，通过 `api_key_env` 被 YAML 引用。

模型配置示例（当前默认）。`models.<task>.models` 为有序候选列表：先尝试在前，同供应商内失败自动切换下一个（缓解免费模型配额不足）：

```yaml
models:
  extraction:
    provider: bailian
    models: [qwen3.7-flash, qwen3.7-max]
  qa:
    provider: bailian
    models: [qwen3.7-flash, qwen3.7-max]
  todo:
    provider: bailian
    models: [qwen3.7-flash, qwen3.7-max]
  embedding:
    provider: local
    models: [models/bge-small-zh-v1.5]
```

新增供应商只需在 `providers` 下添加条目（含可选模型列表 `models`，作为「系统配置」页模型下拉的候选数据源）、实例名 `display_name` 与类型 `type`（留空按 base_url 自动推断）并配置对应的环境变量名即可；API key 也可在「系统配置 → 供应商」页面直接输入，后端会自动写入 `.env` 并同步环境变量（免重启生效）。切换模型在「系统配置」页面或 YAML 中修改后保存生效；`app.yaml` 写入权唯一归后端 API 进程（调度器/CLI 只读）。旧版单 `model:` 字段会自动迁移为 `models: [xxx]`；`name` 为供应商唯一标识（任务模型引用它），不可在页面改名（如需改名编辑 YAML）。

## 项目状态

- [x] 概念验证（RAG 与网页对话）— 已在 `Llama 3.1 本地 RAG` 项目完成
- [x] MVP 开发（M1–M6：抓取/提取/待办/问答/配置）
- [x] 短线开发（W1–W4：调度运维 / 检索质量 / 订阅提醒 / 埋点体检）
- [x] 前后端分离（Phase 0–8：FastAPI + Vue3 + 异步任务 + SSE + Docker）
- [ ] 多学校适配
- [ ] 站外主动推送（邮件 / 微信 / 桌面通知）
- [ ] 多用户 + 鉴权（`api/deps.py` 已预留替换点）

## 关联项目

- [Local Lllama-3.1 with RAG](https://github.com/Shubhamsaboo/awesome-llm-apps.git) — 本项目的前身，验证了 RAG 与网页对话的可行性

---

## Docker（Multi-stage）

`Dockerfile` 先在 Node 镜像构建 `frontend/`，再在 Python 镜像安装 `requirements-backend.txt` 并拷贝静态产物。

```bash
# Build
docker build -t campus-notice:phase8 .
# Run
docker run -p 8000:8000 campus-notice:phase8
# 健康检查 / 访问
curl http://localhost:8000/api/v1/health   # {"status":"ok","version":"1.0.0",...}
# 浏览器打开 http://localhost:8000（SPA 静态挂载）
```

> 若需在镜像内启用完整引擎功能（Chroma、LangChain、重 ML 依赖），把对应包加入
> `requirements-backend.txt` 后重新构建；`requirements-backend.txt` 已含 extractor/crawler 依赖。
