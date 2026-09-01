export class ApiError extends Error {
  status: number
  constructor(status: number, message: string) {
    super(message)
    this.status = status
  }
}

/**
 * 桌面启动令牌（K7）：前端统一加 X-Desktop-Token 头。
 * 令牌由桌面壳注入 window.__CNA_TOKEN__；浏览器 dev 模式无此全局 → 返回 undefined，
 * 此时后端中间件校验关闭（DESKTOP_TOKEN_CHECK=0），不影响开发流程。
 */
export function desktopToken(): string | undefined {
  const w = window as unknown as { __CNA_TOKEN__?: string }
  return w.__CNA_TOKEN__
}

/** 合并桌面令牌头到既有 headers（不覆盖调用方显式设置的同名头）。 */
function withDesktopToken(init: RequestInit): RequestInit {
  const token = desktopToken()
  if (!token) return init
  const headers = new Headers(init.headers)
  if (!headers.has('X-Desktop-Token')) headers.set('X-Desktop-Token', token)
  return { ...init, headers }
}

/**
 * 供 SSE / 其它直接 fetch 的调用点使用：返回 `{ 'X-Desktop-Token': token }`，
 * 有令牌时注入，无令牌（浏览器 dev）返回空对象。
 */
export function desktopTokenHeaders(): Record<string, string> {
  const token = desktopToken()
  return token ? { 'X-Desktop-Token': token } : {}
}

/**
 * 等待桌面启动令牌注入（K7，B22 修复）。
 * 桌面壳在 webview 页面 loaded 事件后注入 window.__CNA_TOKEN__；Vue 首屏
 * 请求（onMounted 的静默检查/轮询）可能早于注入，触发 401「无效的桌面令牌」。
 * 此处短等令牌就绪（最多 ~2.5s）再重试一次，彻底消除注入时序竞争。
 * 浏览器 dev / --browser 降级（无 __CNA_TOKEN__）不等待，直接返回。
 */
async function ensureTokenReady(timeoutMs = 2500): Promise<boolean> {
  if (desktopToken()) return true // 已有令牌，无需等
  // 判定当前是否桌面壳环境：pywebview 桥存在（非纯浏览器）
  const w = window as unknown as { pywebview?: unknown }
  if (!w.pywebview) return false // 浏览器 dev / 非桌面：无令牌可等，直接放行
  const deadline = Date.now() + timeoutMs
  while (Date.now() < deadline) {
    await new Promise((r) => setTimeout(r, 80))
    if (desktopToken()) return true
  }
  return !!desktopToken()
}

/** 统一请求：先确保令牌就绪，再 fetch；遇 401（无效令牌）等待令牌后重试一次。 */
async function request(input: string, init: RequestInit): Promise<Response> {
  await ensureTokenReady()
  let res = await fetch(input, withDesktopToken(init))
  if (res.status === 401) {
    // 401 = 无效的桌面令牌：首屏 onMounted 请求可能早于壳的 loaded 令牌注入。
    // 无论当前是否已取到令牌都等一次再重试——若令牌在请求发出后才就绪，
    // 重试即成功；浏览器直连桌面后端（无令牌可等）保持 401（本场景不合理）。
    await ensureTokenReady(3000)
    if (desktopToken()) res = await fetch(input, withDesktopToken(init))
  }
  return res
}

async function handle(res: Response) {
  const text = await res.text().catch(() => '请求失败')
  let message = text
  try {
    const body = JSON.parse(text)
    message = body.detail || body.message || body.error || text
  } catch {
    // not JSON
  }
  throw new ApiError(res.status, message)
}

export function qs(params: Record<string, unknown>) {
  const sp = new URLSearchParams()
  for (const [k, v] of Object.entries(params)) {
    if (v === undefined || v === null) continue
    sp.set(k, String(v))
  }
  return sp.toString()
}

export async function get<T = unknown>(url: string, params?: Record<string, unknown>, options?: RequestInit): Promise<T> {
  const target = params ? `${url}?${qs(params)}` : url
  const res = await request(target, options || {})
  if (!res.ok) await handle(res)
  return (await res.json()) as T
}

export async function post<T = unknown>(url: string, body?: unknown, options?: RequestInit): Promise<T> {
  const init: RequestInit = Object.assign({
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: body ? JSON.stringify(body) : undefined,
  }, options || {})
  const res = await request(url, init)
  if (!res.ok) await handle(res)
  return (await res.json()) as T
}

export async function put<T = unknown>(url: string, body?: unknown, options?: RequestInit): Promise<T> {
  const init: RequestInit = Object.assign({
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: body ? JSON.stringify(body) : undefined,
  }, options || {})
  const res = await request(url, init)
  if (!res.ok) await handle(res)
  return (await res.json()) as T
}

export async function patch<T = unknown>(url: string, body?: unknown, options?: RequestInit): Promise<T> {
  const init: RequestInit = Object.assign({
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: body ? JSON.stringify(body) : undefined,
  }, options || {})
  const res = await request(url, init)
  if (!res.ok) await handle(res)
  return (await res.json()) as T
}

export async function del<T = unknown>(url: string, params?: Record<string, unknown>, options?: RequestInit): Promise<T> {
  const target = params ? `${url}?${qs(params)}` : url
  const init: RequestInit = Object.assign({ method: 'DELETE' }, options || {})
  const res = await request(target, init)
  if (!res.ok) await handle(res)
  return (await res.json()) as T
}
