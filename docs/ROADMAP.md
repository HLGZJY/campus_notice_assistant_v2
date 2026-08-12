# 开发路线图

## 里程碑总览

| 里程碑 | 内容               | 预计耗时 | 依赖   |
| ------ | ------------------ | -------- | ------ |
| M1     | 抓取 + 存储        | 1 天     | 无     |
| M2     | 结构化提取         | 2 天     | M1     |
| M3     | 待办生成 + 列表    | 1 天     | M2     |
| M4     | RAG 问答           | 1 天     | M2     |
| M5     | Streamlit 界面整合 | 2 天     | M3, M4 |
| M6     | 学校配置 + 文档    | 1 天     | M5     |

> MVP 总计约 8 天（全职学习 20+ 小时/周，约 1.5 周）
> M1 因采用 newspaper4k，从 2 天降到 1 天

---

## M1：抓取 + 存储 ✅

**目标**：用 newspaper4k 抓取学校通知网站，存入 SQLite

**技术方案**：`newspaper4k` 库（Source 发现链接 + Article 提取内容）

**任务**：

- [x] 安装 newspaper4k，验证对 scuec 网站的提取效果
- [x] 设计 SQLite 表结构（`storage/db.py`）
- [x] 封装 newspaper4k 爬虫（`crawler/base.py`）
  - [x] `Source.build()` 发现列表页文章链接
  - [x] `Article.download().parse()` 提取详情页
  - [x] 配置中文语言支持（`language='zh'`）
- [x] 实现网页爬虫（`crawler/web_crawler.py`）
  - [x] 从配置读取 list_url
  - [x] 调用 newspaper4k 抓取
  - [x] URL 去重（newspaper4k `memoize_articles` + SQLite UNIQUE）
- [x] 抓取日志记录

**验收**：

- [x] 能抓取 scuec.edu.cn 至少 2 个栏目的通知（已验证 3 个栏目：竞赛通知、结果公示、教务处管理文件）
- [x] newspaper4k 能正确提取标题、正文（发布日期部分页面无法提取，M2 补充）
- [x] 通知存入 SQLite，`status=raw`
- [x] 重复运行不会重复抓取

**已验证数据源**：

| 数据源                | 列表页               | 抓取数 |
| --------------------- | -------------------- | ------ |
| 创新创业学院-竞赛通知 | `cxcy/scss/jstz.htm` | ✅     |
| 创新创业学院-结果公示 | `cxcy/scss/jggs.htm` | ✅     |
| 教务处-管理文件       | `jwc/glwj.htm`       | 42 条  |

**风险**：学校网站结构可能非标准新闻站，newspaper4k 启发式提取效果需实测验证（已验证通过）

---

## M2：结构化提取 ✅

**目标**：用 LLM 从通知正文提取结构化字段

**任务**：

- [x] 定义 `NoticeExtraction` Pydantic 模型（`core/models.py`）
- [x] 实现提取 Agent（`core/extractor.py`）
- [x] 用 OpenAI Agents SDK 的 `output_type` 约束输出
- [x] 批量处理 `status=raw` 的通知（`extract.py`）
- [x] 更新 SQLite 中的结构化字段（schema 迁移 + `update_extraction`）
- [x] 处理提取失败的情况（extracted/partial/failed 三态）
- [x] 截止时间双字段：`deadline_raw`（原文）+ `deadline`（ISO，`core/date_utils.py` 重算）
- [x] 校验失败自动重试（错误回传 LLM，最多 2 次）
- [x] 黄金集评估（`data/golden_extraction.json` + `evaluate_extraction.py`）

**验收**：

- [x] 对 6 条黄金集真实通知，总体准确率 100%（24/24），关键字段 > 80% 达标
- [x] 截止时间解析为 ISO 8601 准确率 100%，无年份时间按发布日推断年份正确
- [x] 通知类型分类正确（竞赛/报名/政策/新闻等）

> **注意**：本地包用 `core/` 而非 `agents/`，因为 opencode-go 的 LLM 调用依赖
> OpenAI Agents SDK（其包名就是 `agents`），避免重名冲突。`agents/extractor.py`
> 对应 `core/extractor.py`。

---

## M3：待办生成 + 列表 ✅

**目标**：从结构化通知生成待办，按截止时间排序展示

**任务**：

- [x] 定义 `TodoItem` / `TodoList` 模型（`core/models.py`）
- [x] 实现待办生成 Agent（`core/todo.py`，输入 M2 结构化结果）
- [x] 待办存入 `todos` 表（`storage/db.py`）
- [x] 实现按截止时间排序查询（无截止的排在最后）
- [x] 实现待办状态管理（pending/done/skipped）
- [x] 按需生成：`generate_todos_for_notice(notice_id)`，重复点击先删旧 pending 再插入
- [x] 小界面验证闭环（`ui/todo_app.py`，Streamlit）

**验收**：

- [x] 报名类通知能生成"在 X 时间前完成报名"待办（实测 id=2 → "在 2026-09-30 17:00 前完成校赛报名"）
- [x] 待办列表按截止时间升序（过期 pending 标记 `[过期]`）
- [x] 可标记完成（--done / --skip，done 记录 completed_at）
- [x] 政策/新闻/结果公示类通知点击不生成待办（返回 none）

> **MVP 形态决策**：待办采用"**用户点开通知才生成**"的按需模式，而非批量自动生成。
> 理由：现有 19 条真实通知中仅 1 条截止时间在未来，批量生成会产生大量过期噪声；
> 且按需生成省 LLM 成本、把主动权交给用户。`batch_generate()` 保留为可选后门。
> 每条通知最多 1 条主待办（key_dates 多阶段待办后续再扩）；过期待办照常生成、
> 由前端灰显。

---

## M4：RAG 问答 ✅

**目标**：基于已抓取通知回答自然语言问题

**任务**：

- [x] 实现向量索引（`storage/vectorstore.py`）
- [x] 把已提取通知切分并索引到 Chroma
- [x] 实现问答 Agent（`core/qa.py`，沿用 `agents/`→`core/` 重命名约定）
- [x] 检索 Top-K 片段，拼接 Prompt
- [x] 回答时引用来源通知

**验收**：

- [x] 能回答"最近有哪些比赛？"
- [x] 回答包含来源通知标题
- [x] 复用 RAG 项目的 fallback embedding 逻辑（`OpenAIEmbeddings` → `HuggingFaceEmbeddings(all-MiniLM-L6-v2)`）

> 实测：索引 27 条已提取通知，生成 110 个 chunk；问答可正确返回比赛列表并引用来源通知标题。

---

## M5：Streamlit 界面整合 ✅

**目标**：完整的 Web 界面，整合所有功能

**任务**：

- [x] 通知列表页（按类型/时间筛选）
- [x] 通知详情卡片（结构化展示）
- [x] 待办清单页（按截止时间排序）
- [x] 问答页（对话框形式）
- [x] 抓取触发按钮（手动触发）
- [x] 错误提示和加载状态

**实现**：

- 新增 `services/` 服务层，将 M1-M4 脚本能力封装为可复用接口
- 新增 `app.py` 仪表盘首页 + `pages/` 多页面目录
  - `1_📋_通知浏览.py`：筛选、爬取、提取、详情卡片
  - `2_✅_待办清单.py`：待办列表、状态管理、生成入口
  - `3_💬_智能问答.py`：对话式 RAG 问答
- 提取成功后自动增量更新 Chroma 索引

**验收**：

- 界面美观，操作流畅
- 四个核心页面可用
- 手动触发抓取能正常工作

---

## M6：学校配置 + 模型配置 + CRUD 管理 ✅

**目标**：
  1. 通过配置文件适配不同学校信息来源
  2. 模型/供应商可配置，支持前后端修改
  3. 已有通知记录的简单 CRUD 管理
  4. 完善文档

**任务**：

- [x] 设计 YAML 配置格式（`config/schema.py` + `config/app.yaml` + `config/schools/*.yaml`）
- [x] 实现统一配置加载器（`config/store.py`），含三层 fallback 与版本号缓存失效
- [x] 编写 scuec 学校配置文件（`config/schools/scuec.yaml`）
- [x] 模型按任务独立配置（extraction / qa / todo / embedding）
- [x] 重构 `utils/llm.py`、`utils/embedding.py` 从 ConfigStore 读取
- [x] 各 Agent 适配任务级模型配置
- [x] 系统配置页面（模型/数据源/供应商/数据管理）
- [x] 通知删除 + 重新提取 + 批量删除 + 索引重建
- [x] 更新使用与开发文档

**验收**：

- [x] 修改配置文件即可切换中南民族大学的其它官方网页通知
- [x] 通过「系统配置」页面可修改模型、数据源、供应商，保存后二次确认生效
- [x] 通知浏览页支持删除和重新提取
- [x] 文档完整，新人能上手

---

## 后期规划（P1）

| 功能     | 描述                 | 预计 |
| -------- | -------------------- | ---- |
| 定时抓取 | APScheduler 定时任务 | 1 天 |
| 主动提醒 | 即将截止时推送       | 2 天 |
| 用户偏好 | 专业/兴趣订阅        | 2 天 |
| 多学校   | 同时支持多所学校     | 1 天 |
| Docker   | 容器化部署           | 1 天 |
