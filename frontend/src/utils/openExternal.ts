/**
 * 外链统一入口（B10.T1 桌面适配）。
 *
 * 目标：所有「跳系统浏览器打开外部链接」的动作收敛到这一处，避免外链在
 * webview 内跳转（R14）。覆盖两类调用点：
 *   1. 显式 `window.open(url, '_blank', ...)`（如 App.vue、ConfigView.vue）；
 *   2. 模板里的 `<a target="_blank">` / naive-ui `<n-a target="_blank">`
 *      （由 initExternalLinkInterceptor 全局统一拦截，避免逐处手改遗漏）。
 *
 * 打开链路（双保险）：
 *   - 桌面壳（pywebview）存在时：优先调用壳注入的 `pywebview.api.open_external`
 *     （由 B10.T2 的 `ExternalLinkBridge` 提供，走系统默认浏览器，不在 webview
 *     内开新窗口）；
 *   - 其余环境（在线版 / 浏览器 / 降级模式）：回退标准 `window.open`，保持
 *     浏览器既有行为不变。
 */

declare global {
  interface Window {
    pywebview?: {
      api?: Record<string, (...args: unknown[]) => unknown>
    }
  }
}

const EXTERNAL_TAG = 'data-cna-external'

/** 判断是否应拦截为外链（打开系统浏览器）的锚点点击。 */
function shouldHandleAsExternal(el: EventTarget | null): boolean {
  if (!(el instanceof Element)) return false
  const anchor = el.closest<HTMLAnchorElement>('a[href]')
  if (!anchor) return false
  // 显式标记（init 时已挂上）或 target=_blank 的链接都算外链
  if (anchor.hasAttribute(EXTERNAL_TAG)) return true
  if (anchor.getAttribute('target') === '_blank') return true
  // 非本机同源 http(s) 链接（如 markdown 渲染出的外链）也走系统浏览器
  const href = anchor.getAttribute('href') || ''
  if (/^https?:\/\//i.test(href)) {
    try {
      const u = new URL(href, window.location.href)
      if (u.origin !== window.location.origin) return true
    } catch {
      // 无法解析时按普通链接处理
    }
  }
  return false
}

/** 打开外部链接（优先走壳桥，失败回退标准 window.open）。 */
export function openExternal(url: string): void {
  if (!url) return
  try {
    const bridge = window.pywebview?.api?.open_external
    if (typeof bridge === 'function') {
      // 壳注入的系统浏览器桥（B10.T2）。pywebview 桥为异步，这里不等待结果。
      void bridge(url)
      return
    }
  } catch {
    // 桥调用异常时忽略，回退标准打开
  }
  // 非桌面壳环境：标准 window.open（在线版 / 浏览器降级模式行为不变）
  window.open(url, '_blank', 'noopener')
}

/**
 * 全局拦截 `<a target="_blank">` 与外部链接点击，统一走 openExternal。
 *
 * 在 App.vue `onMounted` 调用一次即可（事件委托，覆盖所有页面/组件渲染的锚点，
 * 不必逐处手改）。卸载时返回取消函数。
 */
export function initExternalLinkInterceptor(): () => void {
  const onClick = (e: MouseEvent) => {
    // 仅拦截普通左键（不劫持 ctrl/cmd/中键等浏览器自身的新标签行为）
    if (e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return
    const target = e.target as EventTarget | null
    if (!shouldHandleAsExternal(target)) return
    const anchor = (target as Element).closest<HTMLAnchorElement>('a[href]')
    if (!anchor) return
    const href = anchor.getAttribute('href')
    if (!href || href.startsWith('#')) return
    // 阻止 webview 内新窗口，交外链统一入口打开系统浏览器
    e.preventDefault()
    e.stopPropagation()
    openExternal(anchor.href)
  }

  // 预先给所有显式 target=_blank 锚点打上标记，便于 shouldHandleAsExternal 命中
  const mark = () => {
    document.querySelectorAll<HTMLAnchorElement>('a[target="_blank"]').forEach((a) => {
      a.setAttribute(EXTERNAL_TAG, 'true')
    })
  }

  document.addEventListener('click', onClick, true)
  mark()
  return () => {
    document.removeEventListener('click', onClick, true)
  }
}
