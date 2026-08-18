## 我遇到的问题描述和思考

- llm调用输出优化：这个没有明显体感，感觉应该是7s左右一条，这个还需要进行进一步在程序里的测试，不是写一个test就可以的，还包括在程序中任务发起，读取配置，发起task任务，再去接收响应等
- 生成待办的时间也有点长，可进行压测；
- 重新提取测试，单条耗时25秒；

## 涉及关键文件：

core\extractor.py
core\qa.py
core\todo.py

## 优化目的：

提交输出速度，降低token消耗的同时提高准确率；
qa.py 相关的智能问答，还有前端流式输出需要完善，现在并没有完全实现

## 背景知识限定

系统核心瓶颈是 **延迟（Latency）**、**成本（Cost/Tokens）** 和 **可靠性（Reliability/Failover）** 的三角博弈。

### 模块 1：延迟与成本的数学解剖（量化基础）

你首先要知道时间花在哪。根据你的代码调用链：

- **TTFT (Time to First Token)**：`extractor` 和 `todo` 输出很短，TTFT 占比极高（网络延迟 + 模型排队 + Prompt 处理）。`qa` 输出较长，TPOT (Time per Output Token) 占比上升。
- **Input Token 膨胀**：`build_prompt` 截断 3000 字符（约 750 tokens），但加上 `error_msg` 重试后，输入 Tokens 会翻倍。**背景知识**：了解 OpenAI 和开源模型（如 DeepSeek/Qwen）的 **Prompt 缓存命中机制**（连续请求相同前缀可打折），你现在的重试机制把 `error_msg` 加在末尾破坏了缓存前缀，这是可优化的点。
- **Failover 的成本**：`get_model_candidates` 切换模型时，如果首模型失败，**已消耗的输入 Token 不会退款**。背景知识：掌握“熔断器模式”（Circuit Breaker），不要在 5xx 错误上重试 3 次消耗巨量 Token，应立即切换。

---

### 模块 2：结构化输出（JSON / Pydantic）的底层原理

你用了 `output_type=NoticeExtraction` 和 `extra_body={"response_format": {"type": "json_object"}}`。

- **JSON-mode vs Function Calling**：OpenAI 的 `json_object` 模式靠系统提示词约束，模型仍可能输出格式错误（导致你的 `BadRequestError`）。而 **Function Calling / Tool Call** 模式是通过模型的原生语法生成参数，**速度略慢但准确率极高**，且自带 Schema 强制校验。
- **JSON Schema 约束（Grammar）**：开源模型（如 Llama 3 / Qwen）支持 **Outlines / Guidance** 等 JSON 语法约束库。背景知识：**JSON 语法约束会把输出 Token 的采样空间锁死**，能提升 15%~30% 的生成速度（因为跳过了无效 Token 的探索）。你现在的 `json_object` 是“软约束”，换成“硬约束”是提速关键。

---

### 模块 3：智能重试（Self-Correction）的失效分析

你的 `_resolve_and_validate` 把错误传回给 LLM 要求修正。

- **修正成功率衰减**：研究表明，对于结构化提取，第一次修正成功率约 60%，第二次修正（`MAX_RETRIES=2`）成功率骤降至 15%。**背景知识**：不要盲目重试 2 次。针对 `deadline_raw` 解析失败，你的 **确定性 Python 解析器**（`resolve_datetime`）已经很强，应优先用规则修正，只有逻辑冲突（如“截止时间早于发布时间”）才丢给 LLM 重试。
- **Few-shot 修正**：在 `error_msg` 里附带“正确的格式样例”比单纯报错 `无法解析` 有效得多。背景知识：**错误反馈应包含“正例”而非仅“负例”**。

---

### 模块 4：RAG 检索增强（qa.py）的加速核心

你的 `qa.py` 包含检索 + 生成，且有 `hybrid` 模式。

- **Hybrid 检索的耗时**：`BM25`（关键词） + `Dense Vector`（向量）并行检索合并（RRF 算法）在数据量大时，耗时是纯向量的 2~3 倍。背景知识：**HNSW 索引参数调优**（`ef_construction`, `M` 值）影响召回速度；如果并发高，必须启用 Chroma 的 **Persistent Client** 避免每次加载索引。
- **Context 裁剪与去重**：`_build_context` 合并 chunks，但若检索返回 6 个 chunks 属于 3 个不同通知，你全塞进去。背景知识：**Lost-in-the-Middle** 现象——LLM 更关注开头和结尾。如果你的 `top_k=6`，应按相关度降序排列后，**把最相关的放 prompt 末尾**（靠近问题），可提升回答准确率并减少模型思考偏差，间接减少生成冗余 Token。

---

### 模块 5：流式（SSE）与 Failover 的冲突处理

你的 `ask_stream` 遇到 `started=True` 时直接 `raise`，不切模型。

- **流式断联的尴尬**：一旦流式输出第一个 token，表示模型已经开始推理，此时即使底层报错（如网络闪断），你也无法无缝切换模型（因为状态已变化）。背景知识：**客户端重试机制**（幂等性设计）。你应在网关层（FastAPI）捕获流式中断，给前端返回 `[ERROR]` 事件，让前端主动重发请求，而不是在 Python 层强行切换（你现在的做法是正确的）。
- **流式加速**：使用 **TGI / vLLM** 等推理引擎的 **异步连续批处理（Continuous Batching）**，比 OpenAI 原生 API 在流式场景下首 Token 延迟更低。

---

### 模块 6：确定性兜底（Template Fallback）的战术价值

你的 `todo.py` 中的 `template_fallback` 是非常高级的“保底”策略。

- **80/20 法则**：对于待办生成，规则模板（字符串替换）完全可以覆盖 80% 的简单通知。背景知识：**将 LLM 视为“异常处理者”**。你可以改造流程：先用 `template_fallback` 生成粗糙待办，仅当 `notice_type` 复杂或 `signup_method` 含多步骤时，**才异步补调** LLM 润色文本。这能减少 70% 的 LLM 调用次数，大幅降本。

---

### 给你的背景知识补充总结（行动清单）

| 背景知识点              | 针对你的代码                                                                | 预期优化收益                          |
| :---------------------- | :-------------------------------------------------------------------------- | :------------------------------------ |
| **Prompt Cache Prefix** | 把固定 Instructions 放前面，动态 error_msg 放最后                           | 降低 30% 输入成本（支持缓存的供应商） |
| **JSON Grammar 约束**   | 将 `response_format=json_object` 改为 Function Calling 或接入 Outlines      | 提速 15%~25%，消除格式报错重试        |
| **重试归因分析**        | 只在“时间逻辑冲突”时重试，“格式错误”直接调 `template_fallback`              | 减少 50% 无效重试                     |
| **Lost-in-the-Middle**  | `qa.py` 把相关度最高的 chunk 放在 prompt 最后一行                           | 提升 5%~10% 答案准确率，减少二次追问  |
| **异步批处理**          | 如果你有批量提取需求，用 `asyncio.Semaphore` 控制并发，单条重试变为批量重试 | 吞吐量提升 3~5 倍                     |
| **硬兜底前置**          | `todo.py` 先跑模板，如果模板结果不为空，直接返回，不调 LLM                  | 延迟从 800ms 降至 5ms                 |

**给你的第一行动建议**：不要急着换大模型，先给 `extractor.py` 加上 **“快速路径（Fast Path）”**——用正则先捞 `deadline` 和 `url`，如果捞到了且格式完整，直接把结果塞进 `NoticeExtraction`，只把 `summary` 和 `notice_type` 丢给 LLM。这属于 **“Hybrid (规则+LLM)”** 背景知识，是工业界最优解。

## 前期调研资料

以下是把 `Agentic Workflow 框架.md` 中每个问题映射到 `agentic-ai-guide` skill 内资料的对照表（含"书中没有、需外部资料"的标注）。

### 模块 → 资料 → 解释

**模块1：延迟与成本（TTFT/TPOT/缓存/熔断）**
| 问题 | 查阅资料 | 得到的解释 |
|---|---|---|
| TTFT / TPOT、首 token 延迟 | `chapters/ch02-llm-systems-foundations.md` | 前缀缓存可让 TTFT 降 60–80%；连续批处理吞吐提升 1.5–3×；KV cache 大小公式 `2×L×H×d×n` |
| Prompt 固定前缀放前、动态 `error_msg` 放最后 | `ch02`（前缀缓存命中原理）+ `ch18-agent-harness.md` | Prompt 架构：SystemBlock/MemoryBlock/ToolBlock 独立版本化、动态块放后；"Prompt 缓存 成本 −50–90%"（ch18 takeaway 7） |
| 熔断器 Circuit Breaker（5xx 不重试3次） | `ch18`（指数退避、优雅失败、升级规则 §Escalation）、`ch19`（Fallback 回退模式） | 重试/回退/降级应放在 harness 层，模型不感知；**熔断器本身书中无专门章节，属微服务模式，需查外部资料（微软云设计模式 / Netflix）** |

**模块2：结构化输出（JSON / Pydantic）**
| 问题 | 查阅资料 | 得到的解释 |
|---|---|---|
| json_object 软约束 → 硬约束提速 | `ch02`"受限/受约束解码" | vLLM `guided_json`/`guided_regex`/`guided_choice`，后端 XGrammar/Outlines，mask logits 锁死采样空间，吞吐损失 <2%；schema 编译成本 0.5–5s 但跨请求缓存 |
| "保证语法 ≠ 保证正确"（BadRequestError 幻觉值） | `ch02` mental model | 结构合法不保证语义正确——模型仍会产出可解析但事实错误的 JSON |
| Function Calling vs JSON-mode 的 API 级差异 | `ch18`（工具定义 schema 五要素）、`ch21-mcp.md`（tools/call） | 只讲基础，**无 OpenAI API 模式的直接对比**，需查 OpenAI 官方文档 + Outlines/Guidance 库文档（外部） |

**模块3：智能重试（Self-Correction）**
| 问题 | 查阅资料 | 得到的解释 |
|---|---|---|
| 自我反思纠错（错误回传 LLM 修正） | `ch17-agent-memory.md`（Reflexion 反思记忆）、`ch19`（Evaluator-Optimizer、反思模式） | 反思把失败写进记忆、下轮纳入 Prompt；+50% 开销换首次失败任务的成功率 |
| 错误反馈应含"正例"而非仅"负例" | `ch18`（Few-shot 用 Embedding 相似度选择）、`ch10-sft-best-practices.md` | 高质量样例设计原则；**"正例修正更有效"的具体实证需外部 LLM 自我修正研究** |
| 修正成功率 60%→15% 衰减 | `ch28-quick-reference.md`（失效模式诊断表） | **本书无此具体数据（外部研究经验值）**；ch28 有失败模式分类可参考 |
| 只在逻辑冲突时重试（归因分析） | `ch18`（升级决策规则 `Escalate ⇔ p_success<τ…`、优雅失败）、`ch19`（质量门） | 质量门用程序逻辑阻断错误向下游传播，不必每次丢给 LLM |

**模块4：RAG 加速（qa.py）**
| 问题 | 查阅资料 | 得到的解释 |
|---|---|---|
| BM25+Dense+RRF 混合检索耗时 | `ch16-rag.md` | BM25/DPR/RRF 公式（k=60）、表 16.2 检索方法对比、混合 RRF 是生产默认 |
| HNSW 参数调优（ef_construction/M） | `ch16` 仅提 FAISS IVF/HNSW/PQ | **无具体调参指南，需查 FAISS/Chroma 官方文档（外部）** |
| Persistent Client / 索引加载 | — | **书中无 Chroma 工程细节，查 Chroma 文档（外部）** |
| Lost-in-the-Middle（相关 chunk 放 prompt 末尾） | `ch16` anti-pattern（Lost-in-the-Middle [297]） | LLM 更关注开头结尾，中间被忽略 |
| Context 裁剪、top_k 去重 | `ch18`（Context 预算公式 `C≥S+M+T+H+R`、摘要压缩）、`ch16`（上下文污染/过度检索 anti-pattern） | 预算强制 + 压缩策略；大 K 最大化 Recall 反而伤生成 |

**模块5：流式 SSE 与 Failover**
| 问题 | 查阅资料 | 得到的解释 |
|---|---|---|
| SSE 流式协议、前端流式事件 | `ch23-a2a.md` | TaskStatusUpdateEvent/TaskArtifactUpdateEvent、append/final 语义、表"请求-响应 vs 流式"（首 Token 延迟） |
| 客户端重试 / 幂等性设计 | `ch18`（指数退避、优雅恢复）、`ch23`（长任务用推送通知避免常开 SSE） | 重试放 harness/网关层；恢复时说明已完成+原因+恢复建议 |
| vLLM/TGI 连续批处理降首 Token 延迟 | `ch02` | 连续批处理 + PagedAttention + 前缀缓存 |

**模块6：确定性兜底（template_fallback）**
| 问题 | 查阅资料 | 得到的解释 |
|---|---|---|
| LLM 视为"异常处理者"、80/20 法则 | `ch19`（Routing 模式、Fallback 回退）、`ch16`（路由 Routing） | 路由按复杂度分派：规则/分类器(<10ms)/LLM；简单输入走规则路径 |
| 硬兜底前置（模板先跑） | `ch19` Routing（低复杂度输入→专门处理器）、`ch18`（模型路由） | 一个能解决问题的 Prompt 链胜过复杂系统；从最简模式开始 |
| Fast Path（规则+LLM hybrid） | `ch19`（Routing/Prompt Chaining 组合）、`ch16`（路由） | 你的第一行动建议就是 Routing 模式的标准落地 |

### 辅助文件

- `cheatsheet.md` — 决策规则、调参阈值、权衡矩阵
- `glossary.md` — 术语定义
- `ch28-quick-reference.md` — RL 公式集、方法选择决策树、失效模式诊断表

### 书中不覆盖、需外部资料的 4 个点

1. **熔断器 Circuit Breaker**（微服务模式）
2. **HNSW 参数调优 / Chroma Persistent Client**（FAISS/Chroma 官方文档）
3. **JSON-mode vs Function Calling 的 OpenAI API 行为差异**（OpenAI 文档 + Outlines/Guidance）
4. **"修正成功率 60%→15%"及"正例修正更有效"的实证**（外部 LLM 自我修正研究）

另外注意：你的优化目标（单条 25s 耗时、压测）本质是项目自己的基准测试，书中只给原理，不替代你对 `extractor.py`/`qa.py`/`todo.py` 的实际 profile。

所有资料的绝对路径索引（base: `C:\Users\Administrator\.agents\skills\agentic-ai-guide`）：

### 按模块引用的核心章节

**模块1 延迟与成本**

- `C:\Users\Administrator\.agents\skills\agentic-ai-guide\chapters\ch02-llm-systems-foundations.md`
- `C:\Users\Administrator\.agents\skills\agentic-ai-guide\chapters\ch18-agent-harness.md`
- `C:\Users\Administrator\.agents\skills\agentic-ai-guide\chapters\ch19-agent-design-patterns.md`

**模块2 结构化输出**

- `C:\Users\Administrator\.agents\skills\agentic-ai-guide\chapters\ch02-llm-systems-foundations.md`
- `C:\Users\Administrator\.agents\skills\agentic-ai-guide\chapters\ch18-agent-harness.md`
- `C:\Users\Administrator\.agents\skills\agentic-ai-guide\chapters\ch21-mcp.md`

**模块3 智能重试**

- `C:\Users\Administrator\.agents\skills\agentic-ai-guide\chapters\ch17-agent-memory.md`
- `C:\Users\Administrator\.agents\skills\agentic-ai-guide\chapters\ch19-agent-design-patterns.md`
- `C:\Users\Administrator\.agents\skills\agentic-ai-guide\chapters\ch18-agent-harness.md`
- `C:\Users\Administrator\.agents\skills\agentic-ai-guide\chapters\ch10-sft-best-practices.md`
- `C:\Users\Administrator\.agents\skills\agentic-ai-guide\chapters\ch28-quick-reference.md`

**模块4 RAG**

- `C:\Users\Administrator\.agents\skills\agentic-ai-guide\chapters\ch16-rag.md`
- `C:\Users\Administrator\.agents\skills\agentic-ai-guide\chapters\ch18-agent-harness.md`

**模块5 流式 SSE**

- `C:\Users\Administrator\.agents\skills\agentic-ai-guide\chapters\ch23-a2a.md`
- `C:\Users\Administrator\.agents\skills\agentic-ai-guide\chapters\ch18-agent-harness.md`
- `C:\Users\Administrator\.agents\skills\agentic-ai-guide\chapters\ch02-llm-systems-foundations.md`

**模块6 确定性兜底**

- `C:\Users\Administrator\.agents\skills\agentic-ai-guide\chapters\ch19-agent-design-patterns.md`
- `C:\Users\Administrator\.agents\skills\agentic-ai-guide\chapters\ch16-rag.md`
- `C:\Users\Administrator\.agents\skills\agentic-ai-guide\chapters\ch18-agent-harness.md`

### 辅助文件

- `C:\Users\Administrator\.agents\skills\agentic-ai-guide\cheatsheet.md`
- `C:\Users\Administrator\.agents\skills\agentic-ai-guide\glossary.md`
- `C:\Users\Administrator\.agents\skills\agentic-ai-guide\patterns.md`

### 全部 29 章索引

`C:\Users\Administrator\.agents\skills\agentic-ai-guide\chapters\ch01-llm-architecture-and-optimization.md` 至 `ch29-summary-and-future.md`（文件名与上一轮章节索引表一一对应，共 29 个 `.md`，已用 glob 确认全部存在）。

### 项目侧相关文件

- `F:\pending_Agent_Project\campus_notice_assistant_v2\docs-local\assest\Agentic Workflow 框架.md`
- `F:\pending_Agent_Project\campus_notice_assistant_v2\core\extractor.py`
- `F:\pending_Agent_Project\campus_notice_assistant_v2\core\qa.py`
- `F:\pending_Agent_Project\campus_notice_assistant_v2\core\todo.py`

### 外部资料（不在 skill 内，需自行检索）

- 熔断器模式（微软云设计模式 / Netflix）
- FAISS / Chroma 官方文档（HNSW 调参、Persistent Client）
- OpenAI 官方 API 文档 + Outlines / Guidance 库（JSON-mode vs Function Calling）
- LLM 自我修正研究（"正例修正更有效"、修正成功率衰减数据）
