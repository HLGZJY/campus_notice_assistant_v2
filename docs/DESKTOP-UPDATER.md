# 桌面版更新闭环（B18）· latest.json 与更新器说明

> 对应 docs/DESKTOP-BATCH-PLAN.md §B18 更新闭环 ｜ 决策项 3：**用户确认，不静默**
> 上游：docs/DESKTOP-UPGRADE.md §3.4 更新项（复用 GitHub Releases + update_service，补「下载→校验→安装」）

## 1. 背景与目标

既有 `services/update_service.py` 只做「检查更新」（GitHub Releases API + 版本比对，
**静默失败契约**），不下发安装包。B18 在它之上补齐完整闭环：

```
latest.json（清单，含 sha256）
   └─→ desktop/updater.perform_update()
         下载安装包 → sha256 校验 → 弹窗确认 → 调用安装包 → 退出当前实例
```

- **决策项 3**：用户确认，不静默。校验失败中止并提示（D-45），不静默装。
- **契约保全（B18.T3）**：`services/update_service.py` 未改动；未配 `update.repo`
  时 `/api/v1/update/check` 仍恒 200（静默失败契约原样保留）。
- **R12 降级**：若用户取消 / 下载失败 / 未配置，只中止本次升级，不影响主功能。

## 2. latest.json 格式规范

挂到 GitHub Release 下（与安装包 assets 并列）。桌面更新器独立拉取它，
**sha256 只以它为准**（GitHub Releases API 不返回 asset 的 sha256）：

```json
{
  "version": "0.2.1",
  "notes": "本次更新说明（release body / changelog，可选）",
  "assets": [
    {
      "name": "校园通知助手-桌面版-setup.exe",
      "url": "https://github.com/<owner>/<repo>/releases/latest/download/校园通知助手-桌面版-setup.exe",
      "sha256": "64 位小写十六进制"
    }
  ]
}
```

字段说明：

| 字段 | 必填 | 说明 |
|---|---|---|
| `version` | 是 | 新版本号（对应 VERSION / tag vX.Y.Z） |
| `notes` | 否 | 更新说明（供确认弹窗展示） |
| `assets[].name` | 是 | 安装包文件名（应含「桌面版」以被更新器选中） |
| `assets[].url` | 是 | 下载地址（GitHub 或镜像前缀） |
| `assets[].sha256` | 是 | 安装包 SHA-256 十六进制；空 = 放行（不校验）但建议始终填写 |

更新器 `select_desktop_asset` 选择优先级：
1. 文件名含「桌面版」；
2. 以 `-setup.exe` 结尾且不含「在线版/cloud/web」等其它 flavor 标识；
3. `suffix` 自定义关键词；
4. 兜底取第一个可下载项。

## 3. 生成方法（发布时）

`build.py` 新增 `--latest-json`：desktop flavor + `--innosetup` 时，编译出安装包后
自动生成 `out/latest.json`（含 sha256；`url` 从 `config/app.yaml` 的 `update.repo`
拼 `https://github.com/<repo>/releases/latest/download/<名>`，未配置则留空并提示）：

```bash
packaging/venv-build/Scripts/python packaging/build.py --flavor desktop --innosetup --latest-json
```

## 4. 发布 SOP（每次更新）

1. 改 `VERSION` 到新版本；
2. 构建 + 编译安装包 + 生成 latest.json（见 §3）；
3. 校验 `out/latest.json` 的 `version` 与安装包 sha256 正确；
4. 上传产物到 GitHub Release（`gh release create v<X.Y.Z>`）：
   - 安装包：`校园通知助手-桌面版-setup.exe`
   - 更新清单：`latest.json`（随 tag 一并上传，更新器按 releases/latest/download 拉取）
5. 配置 `config/app.yaml` 的 `update.repo: owner/repo`（桌面版安装环境生效）。

## 5. 更新器行为（desktop/updater.py）

入口：托盘「检查更新」（`desktop/app.py::_check_update`）→ `perform_update()`。

`perform_update` 契约（返回结构化 dict，不抛异常）：

| status | 含义 | 提示 |
|---|---|---|
| `ok` | 已完成确认并触发安装 | 「已进入安装流程」 |
| `cancelled` | 用户取消 / 确认异常 | 不安装 |
| `disabled` | 未配置 update.repo | 「检查更新已禁用」 |
| `error` | 下载失败 / 校验失败 / 启动安装器失败 | 中止并提示原因 |

关键接口：

- `parse_manifest(text)`：解析校验 latest.json（损坏抛 `ValueError`）
- `fetch_manifest(repo, prefix)`：拉取 latest.json（网络，失败抛 `RuntimeError`）
- `select_desktop_asset(assets)`：挑桌面版安装包
- `download_asset(url, dest)`：流式下载安装包
- `sha256_of(path)` / `verify_sha256(path, expected)`：sha256 校验
- `perform_update(...)`：完整闭环，下载/确认/启动/退出均可注入桩（端到端可测）

## 6. 测试与验收

```bash
# 端到端（本地 mock HTTP，离线可跑；6 用例）
packaging/venv-build/Scripts/python test_desktop_updater.py

# 桌面回归
python tools/run_regression.py --pattern "test_desktop_*.py"
```

人工验收（docs/DESKTOP-ACCEPTANCE.md §5 B18）：

- **D-44**：发布新 tag 后，托盘「检查更新」可完成一次完整升级（下载 → 校验 → 确认 → 安装 → 退出）。
- **D-45**：sha256 校验失败时中止并提示（`test_desktop_updater.py` case 4 自动化覆盖）。
