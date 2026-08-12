# 校园通知智能助手

> Campus Notice Assistant — 把校园通知变成可执行的待办清单

## 这是什么

一个能自动抓取学校各类通知网站、用 LLM 提取关键信息（截止时间、地点、报名链接、面向对象）、生成个性化待办清单的智能助手。

本项目源于 `Llama 3.1 本地 RAG` 项目的延伸，从"与单个网页对话"演进到"自动监控多来源通知 + 结构化提取 + 待办管理"。

## 核心能力

- **多来源抓取**：学校官网、学院/部门网站、教务处通知、微信公众号
- **结构化提取**：自动识别通知类型、截止时间、地点、报名链接、面向对象
- **待办生成**：把通知转成可执行的待办项
- **截止提醒**：对截止前 3 天 / 1 天的待办/通知自动生成站内提醒（首页红点 + 待办中心提醒区，可已读/忽略），由调度器独立进程生成，不依赖 Streamlit
- **智能问答**：基于已抓取的通知回答自然语言问题（RAG）
- **学校可配置**：通用架构，通过配置文件适配不同学校

## MVP 范围

MVP 阶段先用 **中南民族大学 (scuec.edu.cn)** 验证，核心场景是 **结构化提取 + 待办生成 + RAG 问答**。

## 文档导航

| 文档                                         | 内容                         | 给谁看    |
| -------------------------------------------- | ---------------------------- | --------- |
| [docs/PRD.md](docs/PRD.md)                   | 产品需求、用户故事、功能清单 | 产品/需求 |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | 技术架构、模块设计、数据流   | 开发      |
| [docs/DATA-MODEL.md](docs/DATA-MODEL.md)     | 数据表结构、Pydantic 模型    | 开发      |
| [docs/ROADMAP.md](docs/ROADMAP.md)           | 开发路线图、里程碑           | 项目管理  |

## 技术栈

| 层         | 选型                                   | 说明                                |
| ---------- | -------------------------------------- | ----------------------------------- |
| LLM        | 可配置（默认 opencode-go）             | OpenAI 兼容接口，按任务选择模型     |
| Embedding  | 可配置（默认本地 all-MiniLM-L6-v2）    | 本地轻量模型，384 维                |
| 向量库     | Chroma                                 | 轻量，嵌入式                        |
| Agent 框架 | OpenAI Agents SDK                      | Capstone 课程要求                   |
| 前端       | Streamlit                              | MVP 快速验证                        |
| 数据存储   | SQLite                                 | 轻量，单文件                        |
| 配置       | YAML + 环境变量                        | `config/app.yaml` / `.env`          |
| 抓取       | requests + BeautifulSoup               | 通用网页抓取                        |

## 快速开始

```bash
# 1. 安装依赖
pip install -r requirements.txt

# 2. 配置环境变量
cp .env.example .env
# 编辑 .env 填入 API key（如 OPENCODE_API_KEY）

# 3. 确认 / 修改配置（二选一）
# 方式 A：直接编辑 YAML
code config/app.yaml          # 模型 / 供应商 / 活跃学校
code config/schools/scuec.yaml # 数据源
# 方式 B：启动后在「系统配置」页面可视化修改

# 4. 初始化数据库
python -m campus_assistant.init_db

# 5. 抓取通知（首次）
python -m campus_assistant.crawler
# 或： python crawl.py

# 6. 结构化提取（M2）
python extract.py                  # 批量提取 status=raw 的通知
python evaluate_extraction.py      # 用黄金集评估提取准确率

# 7. 待办生成（M3）
python todo.py --notice 2          # 按需生成某通知的待办
python todo.py --list              # 待办清单（按截止升序）
streamlit run ui/todo_app.py       # M3 小界面：点按钮生成待办

# 8. RAG 问答（M4）
python index.py                    # 把已提取通知切分并索引到 Chroma
python qa.py "最近有哪些比赛？"     # 单次问答
python qa.py                        # 交互式问答

# 9. 启动 M5/M6 整合应用
streamlit run app.py
# 多页面应用：仪表盘 / 通知浏览 / 待办清单 / 智能问答 / 订阅管理 / 系统配置 / 服务市场（演示）
# 在"通知浏览"页面可手动触发抓取、提取，提取成功后自动增量更新索引
# 页面行为埋点（页面访问/问答/待办/服务按钮）写入 events 表，可查看服务市场页点击统计（门控 #1）

# 10. 定时调度（W1：无人值守自动抓取→提取→每日体检）
python scheduler.py            # 前台运行
python scheduler.py --once     # 只跑一轮完整闭环后退出（验证用）
python scheduler.py --interval 1  # 覆盖抓取间隔为 1 分钟（快速验证）

# 11. 订阅 + 截止提醒全链路演示（W3，5 分钟，详见 docs/DEMO.md）
python tools/demo_reminder.py --demo   # 发布→命中→提醒→待办→用户处理，自动校验幂等
streamlit run app.py                   # 首页红点 / 订阅命中 / 待办页两步式「忽略」
python tools/demo_reminder.py --clean  # 清理演示数据（只清 source="演示数据"，不碰真实数据）
```

> **两步式交互（模块 3.3）**：订阅新增/编辑、重匹配全部通知、提醒「忽略」改为「第一步预览/确认 → 第二步执行」，长时写库操作不再直接绑定在表单提交里（见 `ui/two_step.py`）。

## 调度器（scheduler.py）

独立进程，基于 APScheduler，与 Streamlit / CLI 互不影响。四个 job：

| job | 触发 | 说明 |
| --- | --- | --- |
| crawl | 每 `crawl.interval_minutes`（默认 60，运行中改配置自动热更新） | 抓取所有数据源 |
| extract | 紧跟抓取（晚 20s） | 提取 `status=raw` 的通知，成功后增量索引 |
| daily | 每日 03:00 | 过期清理（默认只报告不删除；`crawl.cleanup_enabled=true` 才删，`expire_days` 为无 deadline 通知的兜底有效期）+ 向量一致性检查（自动清理幽灵向量） |
| reminder | 每日 03:00 | 截止提醒扫描（模块 3.2）：对截止前 3 天 / 1 天的通知生成提醒，幂等（同一天同一对象不重复）；`--no-reminder` 可单独关闭 |

- 失败不吞异常：异常写日志 + 落 `scheduler_log` 表（含连续失败计数），下一周期自动重跑。
- 崩溃恢复：每次运行落库，重启时打印最近运行记录；已抓 URL 由 `notices.url UNIQUE` 去重，kill 后重启不会重复抓取。
- 验证自动抓取：把 `config/app.yaml` 的 `crawl.interval_minutes` 改成 1，重启调度器，观察 `data/logs/scheduler.log` 每分钟一轮抓取。

### Windows 后台运行

- **推荐（任务计划程序）**：`schtasks /create /tn "notice_scheduler" /tr "F:\...\.venv\Scripts\python.exe F:\...\scheduler.py" /sc onlogon /f`，开机自动后台运行；`schtasks /end /tn "notice_scheduler"` 停止。
- **无窗口隐藏启动**：PowerShell `Start-Process -FilePath ".venv\Scripts\python.exe" -ArgumentList "scheduler.py" -WindowStyle Hidden`，日志写在 `data/logs/scheduler.log`。
- **当前会话后台**：`start /B python scheduler.py`（关控制台即停，适合临时测试）。
- 停止：`taskkill /F /IM python.exe`（会停掉所有 python 进程，慎用）或通过任务计划程序停止。

## 配置说明

配置文件位于 `config/`：

- `config/app.yaml`：应用主配置，包含 `active_school`、`models`（按任务配置模型）、`providers`（供应商注册表）、`crawl`（全局抓取参数）。
- `config/schools/<code>.yaml`：学校数据源配置，每个学校一个文件。
- `.env`：存放 API key 等敏感信息，通过 `api_key_env` 被 YAML 引用。

模型配置示例：

```yaml
models:
  extraction:
    provider: opencode-zen
    model: kimi-k2.7-code
  qa:
    provider: opencode-zen
    model: deepseek-v4-pro
  todo:
    provider: opencode-zen
    model: kimi-k2.7-code
  embedding:
    provider: local
    model: all-MiniLM-L6-v2
```

新增供应商只需在 `providers` 下添加条目并配置对应的环境变量名即可。切换模型在「系统配置」页面或 YAML 中修改后保存，Streamlit 会自动重新加载。

## 项目状态

- [x] 概念验证（RAG 与网页对话）— 已在 `Llama 3.1 本地 RAG` 项目完成
- [x] MVP 开发 — 已完成
- [x] 站内截止提醒（首页红点 + 待办中心提醒区，已读/忽略）
- [ ] 多学校适配
- [ ] 站外主动推送（邮件 / 微信 / 桌面通知）

## 关联项目

- [Llama 3.1 本地 RAG](../Llama%203.1%20本地%20RAG%20-%20与任意网页对话，完全离线) — 本项目的前身，验证了 RAG 与网页对话的可行性
