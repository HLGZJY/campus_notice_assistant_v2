/**
 * 剪贴板降级链（B10.T3 桌面适配）。
 *
 * 在 WebView2 / 某些受限环境下 ``navigator.clipboard`` 可能不可用（R3：
 * WebView2 剪贴板行为差异）。统一走降级链，保证「复制」在桌面壳里始终可用：
 *
 *   1. ``navigator.clipboard.writeText``（现代、异步，首选）；
 *   2. 失败 → 创建临时 textarea + ``document.execCommand('copy')``（经典兜底）；
 *   3. 仍失败 → 返回 false，由调用方决定最后兜底（如提示用户手动复制）。
 *
 * 返回 Promise<boolean>：是否成功写入剪贴板。
 */
export async function copyToClipboard(text: string): Promise<boolean> {
  if (!text) return false
  // 1) 现代异步 API
  try {
    if (navigator.clipboard && typeof navigator.clipboard.writeText === 'function') {
      await navigator.clipboard.writeText(text)
      return true
    }
  } catch {
    // 权限拒绝 / 非安全上下文 / WebView 不支持 → 走降级
  }
  // 2) 经典 execCommand 兜底
  return execCommandCopy(text)
}

/** 用临时 textarea + execCommand('copy') 复制（旧式同步兜底）。 */
function execCommandCopy(text: string): boolean {
  const textarea = document.createElement('textarea')
  textarea.value = text
  // 移到视口外，避免闪烁；保留可读性（不用 display:none，部分浏览器会拒绝复制）
  textarea.style.position = 'fixed'
  textarea.style.top = '-9999px'
  textarea.style.left = '-9999px'
  textarea.setAttribute('readonly', '') // iOS 需要 readonly 才不弹键盘
  document.body.appendChild(textarea)
  // 选中内容
  const selection = document.getSelection()
  const prevRange = selection && selection.rangeCount > 0 ? selection.getRangeAt(0) : null
  textarea.select()
  textarea.setSelectionRange(0, textarea.value.length)
  let ok = false
  try {
    ok = document.execCommand('copy')
  } catch {
    ok = false
  }
  // 还原选中状态
  try {
    if (selection && prevRange) {
      selection.removeAllRanges()
      selection.addRange(prevRange)
    }
  } catch {
    // 忽略
  }
  document.body.removeChild(textarea)
  return ok
}
