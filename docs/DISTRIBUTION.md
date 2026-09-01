# 校园通知智能助手 · 桌面版分发页

> 面向最终用户（同学 / 小范围熟人）的下载与校验说明页。
> 维护：每次 Release 后同步更新本页「当前版本」的 **sha256**（与本机 `packaging/out/*.sha256` 一致）。
> 对应 B19.T4 / DESKTOP-UPGRADE.md §5.3（5.4 无证书过渡方案）。

## 下载

| 版本 | 安装包 | 说明 |
|---|---|---|
| 桌面版（推荐） | **`campus-notice-assistant-desktop-setup-v<版本>.exe`** | 原生窗口，无需浏览器；自动检测并安装 WebView2 运行时 |
| 云端版（在线） | `campus-notice-assistant-cloud-setup-v<版本>.exe` | 传统浏览器模式，兼容无 WebView2 的老环境 |

> 所有安装包均来自 GitHub Releases（本仓库公开页），**仅通过官方 Release 页面下载**。
> 安装到当前用户目录（`%LOCALAPPDATA%\CampusNoticeAssistant`），**无需管理员权限**。
> 卸载时程序文件清空，`data/`（通知库 / 向量库 / 日志）**完整保留**。

## 校验安装包（sha256）

安装包在发布时同时生成 **SHA-256 校验值**（写入 Release 说明与各 `.sha256` 文件）。
**强烈建议下载后核对**，确保文件完整、来源可信：

Windows PowerShell：

```powershell
Get-FileHash '下载目录\campus-notice-assistant-desktop-setup-v0.2.0.exe' -Algorithm SHA256
```

Git Bash / Linux：

```bash
sha256sum campus-notice-assistant-desktop-setup-v0.2.0.exe
```

将输出的 64 位十六进制字符串与**本页 / Release 说明中给出的 sha256 逐字比对**。
一致 → 文件完好；不一致 → **请勿运行**，重新下载或报告问题。

### 当前版本（v0.2.0 桌面版）

```
d5a403514ad19673904bc60f9278ffe5783daec0e1923438d214d1e9535b60b7  校园通知助手-桌面版-setup.exe
```

> 以本机 `packaging/out/校园通知助手-桌面版-setup.exe.sha256` 为准；正式发布前如重新构建，覆盖本页值。

## 安全与误报说明

**本项目为个人开发的校园通知聚合助手，安装包未购买代码签名证书（过渡期）。**
因此部分杀毒软件（尤其 360 / Windows Defender / 火绒）可能对未签名 exe 给出
「未知程序 / 建议不要运行」提示，甚至误报为恶意软件。这**不是**病毒，请按以下步骤处理。

### 为什么会误报

- PyInstaller 打包的 exe 是无签名、含大量 Python 运行时文件的单文件程序，易被
  启发式引擎误判；
- 未签名的网络下载文件默认会被 Defender「SmartScreen」拦截；
- 云端版含本地模型读取等行为，误报率相对更高。

### 处置方法

1. **核对 sha256**（见上）：与官方发布值一致即可放心。
2. **SmartScreen 弹「已阻止」**：点「更多信息」→「仍要运行」。
3. **Defender 误报**：Windows 安全中心 → 病毒和威胁防护 → 保护历史记录 →
   找到误报项 → 「操作」→「允许」。或「管理设置」→「排除项」→ 添加安装包/安装目录。
4. **360 / 火绒误报**：在弹出的隔离提示中点「信任」或「恢复文件」，加入白名单。

### 申诉 / 反馈

若确认是误报且希望彻底解决，可通过以下任一途径反馈：

- 在 GitHub 仓库提 Issue，附上：
  - 杀毒软件名称与版本；
  - 报毒的具体文件（`.exe` / `.pyd` / 完整路径）与检测名（如 `Trojan/Heur`）；
  - 该文件的 sha256（便于核验同一产物）。
- 向对应厂商的误报申诉入口提交（Defender 可走 Microsoft 的恶意软件提交流程）。

> 正式发行版（v0.2.0）仍为无证书过渡方案；若未来采购代码签名证书，将显著降低误报率。
