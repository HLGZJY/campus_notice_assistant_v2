可参考文件：
docs\Agentic Workflow 框架.md

### 🧩 批次 A（基础设施保底与索引加速）-已完成

完成记录：docs-local\assest\llm改进调用完成记录.md

> **服务目标**：根治 25s 耗时中的“稳定病根”（向量库加载 & 数据库并发），提供稳定的低基数环境。

- **A1. Chroma Persistent Client 改造（对应补充 3）**
  - 修改 `storage/vectorstore.py`：将 `VectorIndex` 的初始化从 `EphemeralClient` 或每次 `from_documents` 重建，改为全局单例的 `chromadb.PersistentClient(path="./chroma_data")`。
  - 确保服务启动时只加载一次 HNSW 索引，后续请求复用内存映射。
- **A2. SQLite 异步并发安全加固（对应补充 4）**
  - 修改 `storage/db.py` 的 `get_connection`：添加 `check_same_thread=False` 和 `timeout=30.0` 参数。
  - 在 `extract_batch` 的并发任务中，强制每个协程通过 `contextvars` 或独立函数调用获取专属连接对象，避免全局连接共享冲突。
- **验收标准**：重启服务后，首次 QA 请求仍可能慢（加载索引），但第二次及之后的 QA 请求，检索耗时稳定在 200ms 以内；批量并发提取不再抛出 `SQLite thread` 异常。

---

### 🧩 批次 B（提取与待办的核心路径压缩）-已完成

完成记录：docs-local\assest\llm改进调用完成记录.md

> **服务目标**：大幅削减 LLM 调用次数和单次 Payload 成本（命中 80/20 法则 + 硬约束提速）。

- **B1. Todo 模板优先策略（已取消，2026-08-18 决策）**
  - 决策：不做模板优先，保持 LLM 调用保证待办生成质量（模板降级仍保留为 LLM 失败兜底）。
- **B2. Extractor Fast Path 正则兜底（对应原改动 2）**
  - 在 `core/date_utils.py` 新增 `fast_extract(content)`，用正则预捞 `https?://` 链接和带关键词的截止时间。
  - 在 `extractor._resolve_and_validate` 中集成：当 LLM 给出的 `deadline_raw` 解析失败或 `signup_url` 非法时，直接用 Fast Path 结果覆盖，**不再将此错误加入重试队列**。
- **B3. 结构化输出硬约束（Function Calling 替换 JSON-mode，对应补充 2）**
  - 修改 `extractor.py` 和 `todo.py` 的 `_get_agent`：**移除** `ModelSettings` 中的 `extra_body={"response_format": {"type": "json_object"}}`。
  - 依赖 OpenAI Agents SDK 原生 `output_type` 自动转换为 Function Calling 模式（语法硬约束），利用 logits 锁死采样空间，消除格式错误的 `BadRequestError`。
- **验收标准**：简单通知（带明确截止日期）的提取和待办生成，平均耗时从 7s 降至 **800ms 以内**（其中 Fast Path 命中的直接 <100ms）；日志中 `BadRequestError` 彻底归零。

---

### 🧩 批次 C（智能重试归因与 RAG 上下文优化）-已完成

完成记录：docs-local\assest\llm改进调用完成记录.md

> **服务目标**：提升首次调用准确率，减少无效重试和冗余 Token 消耗，同时让 LLM 关注最关键的上下文。

- **C1. 重试归因分析（对应原改动 1）**
  - 修改 `extractor._resolve_and_validate` 的返回值逻辑：
    - **格式错误**（如“截止时间无法解析”）→ **不再**追加到 `errors` 列表，直接置 `deadline=None`（不触发 LLM 重试）。
    - **逻辑冲突**（如解析出的截止日期早于发布时间 `published_at`）→ 追加到 `errors` 列表（触发 LLM 重试）。
  - 修改 `_call` 中的 `error_msg` 拼接：在报错后追加一行固定“正例”格式样例（如 `【参考格式】deadline_raw: "7月16日17:00", deadline: "2026-07-16T17:00:00"`）。
- **C2. Lost-in-the-Middle 上下文重排（对应原改动 3）**
  - 修改 `qa._build_context`：构建完 `context_parts` 和 `sources` 列表后，**整体反转**两个列表（使最相关的 chunk 紧邻 Prompt 底部的“问题区”）。
  - 同步调整 Prompt 模板，将 `问题：{question}` 挪到 `参考通知：` 内容之后、回答指令之前。
- **验收标准**：提取任务中，因“截止早于发布”触发重试的 case 仍保留（修正率提升），但因“格式错误”触发的无效重试减少 **80%** 以上；QA 回答中引用编号顺序正确，且答案末尾引用的 chunk 确实是 Top-1 相关片段。

---

### 🧩 批次 D（批量吞吐、压测可观测性与流式兜底）-已完成

> **服务目标**：验证优化收益，量化 Token 成本降低，并完善前端交互体验。

> 完成记录：docs-local\assest\llm改进调用完成记录.md

- **D1. 批量并发 Semaphore 化（对应原改动 4）**
  - 修改 `services/notice_service.py` 的 `extract_batch` 和 `cli/extract.py` 的 `run_batch`：引入 `asyncio.Semaphore(concurrency)`，使用 `asyncio.gather` 并行执行，确保单机并发数可配置（默认 3~5）。
  - 进度回调 `progress_cb` 改为每完成一条触发，传递 `(done, total)`。
- **D2. 压测脚本 Token 统计增强（对应补充 5）**
  - 在根目录新增 `benchmark_agent.py`：支持 `--task extraction|todo|qa`、`--samples N`、`--concurrency C` 和 `--dry-run`。
  - **关键补充**：在结果汇总中，除了输出 avg/p50/p95 耗时和重试次数外，**必须记录并输出** `total_input_tokens` 和 `total_output_tokens`（从 Agent 运行的 usage 中提取），并计算单条平均 Token 消耗，输出到控制台表格 + `data/benchmark_<task>_<ts>.json`。
- **D3. 流式 SSE 错误兜底（对应原模块 5 遗留）**
  - 修改 `api/routes/qa.py` 中调用 `ask_stream` 的路由：捕获 `started=True` 后的中途异常，向前端 SSE 通道发送 `{"event": "error", "data": "推理中断，请稍后重试"}` 事件，并正常关闭流（不抛出 500 异常导致连接断开）。
- **验收标准**：`benchmark_agent.py --task extraction --samples 50` 的报告显示，平均耗时相较优化前下降 >60%，且 input_tokens 总量下降 >30%；流式 QA 在网络闪断时，前端能收到 error 事件并提示用户重试，而非一直转圈。

---

### 📌 批次执行与回归约束

1. **执行顺序**：必须 **A → B → C → D** 依次推进（A 为底层环境，D 为最终验证，不可跳级）。
2. **回归保证**：每完成一个批次，必须跑通对应的新增测试文件（如 `test_fast_path.py`、`test_retry_attribution.py`、`test_qa_lost_middle.py`）以及现有的全量回归测试（`test_gongshi_period.py`、`test_model_failover.py` 等），确保契约不变。
3. **回滚策略**：每个批次 Git ，若验收不通过，仅回滚该批次代码，不影响已上线的其他批次。
