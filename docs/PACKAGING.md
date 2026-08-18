# 打包发布方案（待办，非当前工作）

> **状态：🔴 待办（提前研究，暂不实施）**
> 本方案只是提前调研的产物，**当前项目还有大量优化点未完成**，打包/发布一律排在后面。
> 实施前置条件：功能稳定、界面优化完成、多学校适配等当前路线图事项收尾后再启动。
> 开始实施前需按下方「开始前复核」重新核对一遍（依赖、体积、目录结构可能已变化）。

## 目标与范围

- 目标用户：同学 / 小范围熟人
- 平台：Windows x64
- 形态：Inno Setup → `setup.exe`，安装到 `%LOCALAPPDATA%\CampusNoticeAssistant`
- 更新：检查更新 + 提示下载，走 GitHub Releases（项目仓库公开，A 方案）
- 不做：全自动静默更新、增量更新、多平台

## 关键决策记录

| 项 | 决定 | 理由 |
| ---- | ---- | ---- |
| 打包工具 | PyInstaller onedir + Inno Setup | 带 torch 时 onefile 首次启动慢；onedir 更稳 |
| 安装目录 | `%LOCALAPPDATA%\CampusNoticeAssistant` | Program Files 下运行时写 `.env`/`config` 需管理员权限 |
| Embedding | 双版本（云端瘦身版 + 本地完整版） | 代码已天然支持双模式（`utils/embedding.py`） |
| 版本检测 | `GET api.github.com/repos/{owner}/{repo}/releases/latest` | 免维护 update.json，tag 即版本号，body 即更新日志 |
| 下载加速 | 直连 GitHub + app.yaml 可配镜像前缀 | 国内直连慢，预留切换位 |
| 数据保留 | 安装器/卸载器均不碰 `data/` | 更新覆盖安装不丢用户数据 |

## 双 Embedding 版本组织

代码**已天然支持双模式**：`utils/embedding.py:202` 先试 OpenAI-compatible embedding API，未配 `base_url` 才走本地模型。因此：

- **云端版（默认分发）**：`models.embedding.provider` 指向云端 API 供应商；PyInstaller
  `--exclude-module torch --exclude-module sentence_transformers`，不带 `models/`。
  前提：`HuggingFaceEmbeddings` 是函数内延迟 import（`utils/embedding.py:166`），云端路径不会执行到它。
  → 体积约 150MB。
- **完整版（按需）**：provider 保持 `local`，`--add-data` 带上 `models/`（192MB bge 模型）。
  → 离线 embedding、无 token 费用，但下载 ~2.5GB、首次启动慢。
- **分发策略**：默认发云端版（同学反正要填 LLM API key）；确需离线/省 token 才发完整版。

## 实施步骤（到时按此执行）

1. 前端构建：`cd frontend && npm run build`（产出 `dist/`，约 1.6MB）
2. PyInstaller 双打包：
   - 云端版：排除 torch / sentence_transformers，去掉 `models/`
   - 完整版：全量 + `--add-data models/`
   - 均需带上 `data/`、`config/`、`.env.example`、`VERSION` 文件
3. 冒烟测试：启动 → 自动开浏览器 → 登录页可访问 → 抓一轮数据 → 问答可用
4. Inno Setup 脚本：桌面快捷方式、可选开机自启、卸载保留 `data/`、版本号写 `VERSION`
5. 检查更新功能：`config/app.yaml` 加 `update.repo` 配置 + 下载 URL 前缀（镜像切换位）；
   后端接口静默失败不影响使用；前端「设置→检查更新」+ 启动静默检测，弹窗展示 changelog + 版本 + sha256
6. 发布：公开仓库 → `gh release create v0.x.0` → 真机装一遍验证「提示→下载→覆盖安装→数据保留」

## 发布 SOP（以后每次更新）

```
改版本号 → 跑构建脚本 → gh release create v0.x.0 --notes "更新日志" → 完成
```

`gh release create` 示例：

```
gh release create v0.2.0 ^
  ./out/校园通知助手-云端版-setup.exe ^
  ./out/校园通知助手-完整版-setup.exe ^
  --title "v0.2.0 新功能" ^
  --notes "更新日志写这里"
```

## 已知风险

- PyInstaller 打 torch 的 hook 坑、完整版首次启动慢 → 已隔离在可选支线
- exe 易被杀毒软件（360/Defender）误报 → 首次给同学装需「信任并运行」，完整版更常见
- 国内直连 GitHub 下载慢 → 镜像前缀可切换
- 版本比对需实现（tag 如 `v0.2.0`，与本地 `VERSION` 做数值比较）

## 开始前复核清单

- [ ] `requirements-backend.txt` 依赖是否已变动（能否排除 torch 需重新验证）
- [ ] `utils/embedding.py` 延迟 import 结构是否仍成立
- [ ] 安装目录/数据目录结构是否仍与本文一致
- [ ] 决定 `owner/repo` 具体值，写入 `config/app.yaml`
