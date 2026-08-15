# 全链路演示（W3 模块 3.3）

> 一条命令演示「发布 → 命中 → 提醒 → 待办 → 用户处理」完整闭环，5 分钟可完成，全程不依赖手工改库、不依赖 LLM/网络。

## 前置条件

- 依赖模块 3.1（订阅）、3.2（截止提醒）。数据库无需手动初始化：`storage/db.py:get_connection`
  首次连接即 `executescript(SCHEMA)` 自动建表并迁移。
- Python 环境已就绪（本项目 `.venv`）。

## 演示流程（5 分钟）

### 第 1 步：一键造数（模拟"发布"）

```bash
python tools/seed_demo_data.py --seed
```

插入 2 条演示通知（`source="演示数据"` 标记）：

| 通知 | 截止 | 命中档位 |
| --- | --- | --- |
| 关于组织参加2026年演示竞赛报名工作的通知 | 今天 + 3 天 | 3d |
| 关于举办2026年演示竞赛校赛的通知 | 今天 + 1 天 | 1d |

同时幂等创建订阅词 **「演示竞赛」** 并对两条通知执行命中回填（模拟"命中"），并直插关联待办（确定性文案，不消耗 LLM）。

> 造数工具可重复执行：演示通知 URL 固定 + 订阅按词查重，重复执行不产生重复数据；且每次执行都会把截止时间刷新为"今天+3 / 今天+1"，保证任何一天演示都落在提醒档位。

### 第 2 步：跑全链路演示（自动验证）

```bash
python tools/demo_reminder.py --demo
```

脚本按顺序执行并**自动校验**：

1. **发布**：插入演示通知（第 1 步已完成，默认先重置旧演示数据）
2. **命中**：校验每条演示通知都命中订阅词「演示竞赛」
3. **提醒**：调用 `scan_reminders()` 生成 3d / 1d 提醒，并**幂等验证**（重复扫描 `created=0`）
4. **待办**：校验演示通知关联的 pending 待办
5. **用户处理**：完成 3d 待办 → 其提醒**自动收敛为已读**；忽略 1d 提醒 → 状态收敛

结束时输出 PASS/FAIL 汇总（失败则退出码非 0），演示数据默认**保留**供第 3 步在 UI 中查看。

### 第 3 步：在 Web 界面中查看效果

前后端分离后需分别启动后端与前端：

```bash
# 终端 1：后端（FastAPI，含调度器与静态挂载）
.venv\Scripts\python.exe -m uvicorn api.main:app --host 0.0.0.0 --port 8000

# 终端 2：前端（Vue3 开发服务器，/api 代理到 8000）
cd frontend
npm run dev   # http://localhost:5173
```

也可以构建前端产物后由后端直接提供静态页面：

```bash
cd frontend && npm run build
.venv\Scripts\python.exe -m uvicorn api.main:app --port 8000
# 访问 http://localhost:8000（SPA fallback 返回 index.html）
```

| 页面 | 路由 | 观察点 |
| --- | --- | --- |
| 🏠 首页 | `/` | 顶部提醒红点「待处理提醒 2 条」（演示数据生成）+ 状态统计卡 |
| 📋 通知浏览 | `/notices` | 演示通知带「🔔 演示竞赛」命中徽标；订阅命中筛选可见 |
| 🔔 订阅管理 | `/subscriptions` | 列表出现「演示竞赛」订阅及其命中数 |
| ✅ 待办中心 | `/todos` | 「截止提醒」区显示 2 条提醒；**「忽略」为两步式**：点击后弹确认框，再点「确认忽略」才执行 |
| 💬 智能问答 | `/qa` | 可对演示通知提问（SSE 流式回答 + 来源引用卡片） |

### 第 4 步：清理演示数据

```bash
python tools/seed_demo_data.py --clean
# 或
python tools/demo_reminder.py --clean
```

只清理 `source="演示数据"` 的通知（级联待办/提醒/命中关系）和「演示竞赛」订阅，**不触碰真实数据**。

## 演示数据隔离与幂等

- **隔离**：所有演示数据以 `source="演示数据"` 标记，清理按来源删除（`delete_notices_by_source` 级联清理待办/提醒/命中）。
- **幂等**：通知以固定 URL 去重、订阅按 keyword 查重，重复 seed 不翻倍；提醒表 `UNIQUE(notice_id, tier, remind_on)` 保证同日扫描不重复。
- **自动化验证**：`python test_demo.py`（幂等 / 级联收敛 / 清理隔离 / 预览只读）+ `python test_reminder.py`（提醒链路）+ `python test_subscription.py`（订阅引擎）。

## 验收对照

| 验收信号 | 达成方式 |
| --- | --- |
| 按演示脚本执行 5 分钟可完整演示全链路 | `tools/demo_reminder.py --demo` 一条命令跑完并自验证 |
| 造数工具可重复执行不污染真实数据 | seed 幂等 + `--clean` 只清演示标记数据 |
| 幂等有自动化测试或脚本验证 | `test_demo.py` + `test_reminder.py` + 演示内置幂等校验 |
| 演示不依赖手工改库 | 全部走脚本 / CLI / API |

## 常见问题

- **重复跑 demo 说提醒数不对？** 默认 `--demo` 会先重置旧演示数据；若用 `--no-reset` 跨天重跑，旧提醒会保留（按 `remind_on` 区分），属正常幂等语义。
- **想换到独立数据库测试？** 两个工具都支持 `--db <path>`，如 `python tools/demo_reminder.py --demo --db data/demo_test.db`，不会碰正式库。