# RAG 污染专项处理策略（模块 2.5）

> 维护说明：本策略在前后端分离重构后保持不变——`storage/vectorstore.py` 的
> `check_consistency` / `remove_notice` / `rebuild` 是唯一数据入口，API 层与 CLI 层都复用它。

## 问题定义：幽灵结果

通知从 SQLite 删除后，如果其向量 chunk 仍残留在 Chroma，问答检索仍会召回该通知，
LLM 会引用已删除/已过期的通知作答（如"2025 年赛事报名截止"），即**幽灵结果（RAG 污染）**。

## 幽灵向量判定基准

- **残留（幽灵）向量**：`notice_id` 在 Chroma 中存在，但已不存在于 SQLite（通知被删除）。
- 判定参照集取 **SQLite 全量通知 ID**（`storage/db.py:get_all_notice_ids`），而非"可索引"
  子集——通知处于 `raw`/`failed` 状态时其向量仍属有效内容，不应被当作残留误删。
  （旧实现用 `get_indexable_notice_ids` 作参照，会在大量 `raw` 通知时误删有效向量。）
- 反向的"缺失向量"（已提取但未索引）只报告不清理，由提取链路的增量索引补齐。

## 检测工具：一致性校验脚本

```bash
python check_vector_consistency.py             # 只读检查；无残留输出 "✅ 向量一致（无残留）"
python check_vector_consistency.py --fix       # 自动清理残留向量
python check_vector_consistency.py --json      # 机器可读输出
python check_vector_consistency.py --persist-dir data/chroma
```

退出码：`0`=一致（无残留）；`1`=存在残留（未清理）或读取失败。
建议在每次删除 / 重建索引后运行一次；调度器每日体检也会自动跑并清理。

## 三层处理策略

1. **删除通知 → 级联删向量（第一道防线）**
   - `services/admin_service.py:delete_notice` 删除 SQLite 通知时调用
     `VectorIndex.remove_notice(notice_id)` 同步删除其全部 chunk；
   - 批量删除（按来源 / 按状态 / 按筛选条件）同样级联清理；
   - 前后端分离后删除入口为 API：单条 `DELETE /api/v1/notices/{id}`，
     批量 `POST /api/v1/notices/batch-delete`（异步任务 → `admin_service.batch_delete_by_filter`），
     均复用同一 `remove_notice`。
   - `remove_notice` 按 `notice_id` 元数据过滤拿到真实 chunk id 后按 id 删除并返回实际数量，
     避免"where delete 语义不确定导致删了但没删掉"。

2. **重建索引 → 自动清残留（第二道防线）**
   - `python index.py --rebuild` 与 `qa_service.rebuild_index` 先 `delete_collection()`
     再从 SQLite 全量重建，任何历史残留自然被清空；
   - API 侧 `POST /api/v1/tasks {type: rebuild_index}` 异步执行同一逻辑。

3. **每日体检 → 自动清理（兜底）**
   - 调度器 daily job 运行一致性检查（`scheduler._check_vector_consistency`，复用
     `storage/vectorstore.check_consistency`），幽灵向量默认自动清理，缺失向量只报告。

## 复现与验收

```bash
python reproduce_pollution.py     # 沙箱（临时拷贝库+Chroma）跑 Q19/Q20 两阶段判定
```

- 阶段①删除前：检索须命中目标通知（证明问题确实指向它）；
- 阶段②删除后：目标通知不得再出现在 Top-K（幽灵结果 = 失败）；
- 结束前在沙箱内跑一致性校验，输出"一致"。

**验收信号**：删除通知后检索不到；`check_vector_consistency.py` 一键跑出"一致"结论。
