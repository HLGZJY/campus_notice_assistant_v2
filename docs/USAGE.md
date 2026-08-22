# 校园通知智能助手 · 使用说明（打包版）

本说明面向**拿到 `校园通知助手-云端版-setup.exe` 的使用者**，讲清楚：
需要什么模型、去哪拿 API、装好后怎么填。开发/构建相关请看
[`docs/PACKAGING.md`](./PACKAGING.md)。

> 云端版只需 **一个** 阿里云百炼（DashScope）API Key 即可跑通全部功能，
> 不需要本地显卡、不需要下载模型。

---

## 1. 这个程序需要哪些模型能力？

程序有三类功能会调用大模型（LLM），都需要 API：

| 能力 | 作用 | 默认供应商 |
| ---- | ---- | ---- |
| **结构化提取**（extraction） | 把抓取到的网页通知解析成结构化字段（标题/时间/部门/正文…） | 百炼（bailian） |
| **RAG 问答**（qa） | 基于已抓通知做检索问答 | 百炼（bailian） |
| **待办生成**（todo） | 从通知里抽取行动项/截止日期 | 百炼（bailian） |
| **向量嵌入**（embedding） | 把文本转向量，支撑检索/问答 | **云端版 = 百炼 `text-embedding-v4`**；完整版 = 本地 `bge-small-zh-v1.5` |

- **云端版**：embedding 已经由安装包内置配置指向百炼云端，
  你**不需要**下载任何本地模型，只要能联网调用百炼 API 即可。
- **完整版**（可选发布物）：embedding 用本地 `bge-small-zh-v1.5` 模型，
  首次运行需联网从 HuggingFace 镜像下载（已内置 `HF_ENDPOINT` 镜像加速），
  之后离线可用，但 LLM 提取/问答/待办仍需要 API。

> 模型名具体填什么，必须和你在供应商后台**实际开通的模型**一致。
> 默认配置文件里写的是一组示例模型名，若你的账号没有开通这些模型，
> 改 `config/app.yaml` 里的 `models.*.models` 为你可用的模型即可（见第 4 节）。

---

## 2. 去哪里获取 API Key

### 方案 A：阿里云百炼（推荐，默认即用）

1. 打开 <https://dashscope.console.aliyun.com/> 注册并登录（需实名）。
2. 进入「模型广场 / 总览」，**开通**你需要用到的模型服务
   （文本生成类如通义千问系列 + 文本向量 `text-embedding-v4`）。
3. 进入「API-KEY 管理」→ **创建 API Key**。
4. 复制这串 key，下面要用。它对应环境变量名 **`DASHSCOPE_API_KEY`**。

> 百炼的接口是 OpenAI 兼容格式，程序已经把 `base_url` 指向
> `https://dashscope.aliyuncs.com/compatible-mode/v1`，你不用自己配地址。

### 方案 B：其它 OpenAI 兼容供应商（DeepSeek / 硅基流动 / OpenAI 等）

如果你更想用别的供应商，只要它提供 **OpenAI 兼容** 接口即可：

1. 在对应平台注册并拿到 API Key。
2. 在 `config/app.yaml` 里加一个 provider，或直接改现有 provider 的
   `base_url` 与 `api_key_env`；模型名换成该平台提供的（详见第 4 节）。
3. 在 `.env` 里填对应的环境变量（变量名你自己定义，与 `api_key_env` 对齐）。

> embedding 这步比较挑供应商：要用「文本向量」类模型。
> 云端版默认用百炼 `text-embedding-v4`；换供应商时，请确保它提供
> embedding 模型，否则检索/问答会失败。

---

## 3. 安装后怎么填 Key

1. 双击 `校园通知助手-云端版-setup.exe`，按中文向导安装。
   默认装到 `%LOCALAPPDATA%\CampusNoticeAssistant`（**不需要管理员权限**）。

2. 进入安装目录（文件资源管理器地址栏粘贴
   `%LOCALAPPDATA%\CampusNoticeAssistant` 回车），你会看到：
   ```
   CampusNoticeAssistant.exe   # 主程序（双击启动）
   .env.example                # 配置模板
   config\app.yaml             # 模型/供应商配置
   data\                      # 数据（通知库/向量库/日志，卸载保留）
   ```

3. **复制 `.env.example` 为 `.env`**（同一个目录里），用记事本打开 `.env`，
   把 key 填进去：
   ```env
   # 阿里云百炼（默认供应商）
   DASHSCOPE_API_KEY="你的百炼APIKey"
   ```
   保存。

4. 回到目录，**双击 `CampusNoticeAssistant.exe`** 启动。
   - 程序会自动找一个空闲端口（默认 8000 起）并打开浏览器
     <http://127.0.0.1:8000>。
   - 首次启动会按 `config/app.yaml` 的抓取计划自动抓一轮通知。

> 没有填 key 或 key 无效时，抓取和问答会**优雅降级**（日志里报模型调用失败，
> 不会崩溃）。填好 key 重启即可生效。

---

## 4. 进阶：换成自己的供应商 / 模型

编辑安装目录下的 `config\app.yaml`：

```yaml
providers:
  bailian:                       # 默认供应商，对应 .env 里的 DASHSCOPE_API_KEY
    name: bailian
    display_name: ''
    base_url: https://dashscope.aliyuncs.com/compatible-mode/v1
    api_key_env: DASHSCOPE_API_KEY   # 必须与 .env 里的变量名一致
    models: []
    type: bailian

models:
  extraction:                   # 结构化提取用的模型
    provider: bailian
    models:
      - qwen-max                # ← 改成你账号实际开通的模型名
  qa:                           # 问答
    provider: bailian
    models: [qwen-max]
  todo:                        # 待办
    provider: bailian
    models: [qwen-max]
  embedding:
    provider: bailian           # 云端版用百炼向量模型
    models: [text-embedding-v4]
```

要点：
- `provider` 必须匹配 `providers` 下的某个 key。
- `models.*.models` 里每一项是**该供应商真实提供的模型名**，可写多个（按顺序回退）。
- `api_key_env` 必须和 `.env` 里的变量名完全一致（大小写敏感）。
- 改完保存，**重启 exe** 生效（配置 60 秒热更新，但重启最稳妥）。

---

## 5. 网络与防火墙

- 程序需要**能访问 LLM 供应商的 API 域名**（如 `dashscope.aliyuncs.com`）。
- 抓取通知需要能访问**学校官网**（默认 `active_school: scuec`，即中南民族大学）。
- 杀毒软件（360 / Defender）可能首次拦截 exe，选择「信任并运行」即可。

---

## 6. 怎么确认配置对了

- 启动后在浏览器打开 **设置 → 检查更新 / 模型配置** 页，查看各能力状态。
- 看日志：`data\logs\scheduler.log` 和主程序控制台窗口。
  提取失败的提示形如 `模型 xxx 失败` —— 通常是 key 无效或模型名不对。
- 健康检查：浏览器访问 `http://127.0.0.1:8000/api/v1/health`
  返回 `{"status":"ok","db":"ok"}` 说明服务与数据库正常。

---

## 7. 常见问题

**Q：一定要百炼吗？**
A：默认是，但你可以用任意 OpenAI 兼容供应商（见第 4 节）。
embedding 这一步需要供应商提供「文本向量」模型。

**Q：完整版和云端版有什么区别？**
A：云端版 embedding 走百炼 API，不占本地空间、体积小（安装包约 85MB）；
完整版把 embedding 模型打包进本地（首次需下载），可离线做向量检索，但体积大很多。

**Q：我的 key 会泄露吗？**
A：key 只存在你本机安装目录的 `.env` 文件里，不会上传。
`config/app.yaml` 只存变量名不存 key。

**Q：重装/升级会丢数据吗？**
A：`data/` 目录（通知库、向量库、日志）在覆盖安装和卸载时都**保留**。
你改过的 `config/app.yaml` 在升级时会被备份为 `config/app.yaml.old`，
新配置覆盖安装，需要时可从 `.old` 找回。
