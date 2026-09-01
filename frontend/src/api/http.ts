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
  const res = await fetch(target, withDesktopToken(options || {}))
  if (!res.ok) await handle(res)
  return (await res.json()) as T
}

export async function post<T = unknown>(url: string, body?: unknown, options?: RequestInit): Promise<T> {
  const init: RequestInit = Object.assign({
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: body ? JSON.stringify(body) : undefined,
  }, options || {})
  const res = await fetch(url, withDesktopToken(init))
  if (!res.ok) await handle(res)
  return (await res.json()) as T
}

export async function put<T = unknown>(url: string, body?: unknown, options?: RequestInit): Promise<T> {
  const init: RequestInit = Object.assign({
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: body ? JSON.stringify(body) : undefined,
  }, options || {})
  const res = await fetch(url, withDesktopToken(init))
  if (!res.ok) await handle(res)
  return (await res.json()) as T
}

export async function patch<T = unknown>(url: string, body?: unknown, options?: RequestInit): Promise<T> {
  const init: RequestInit = Object.assign({
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: body ? JSON.stringify(body) : undefined,
  }, options || {})
  const res = await fetch(url, withDesktopToken(init))
  if (!res.ok) await handle(res)
  return (await res.json()) as T
}

export async function del<T = unknown>(url: string, params?: Record<string, unknown>, options?: RequestInit): Promise<T> {
  const target = params ? `${url}?${qs(params)}` : url
  const init: RequestInit = Object.assign({ method: 'DELETE' }, options || {})
  const res = await fetch(target, withDesktopToken(init))
  if (!res.ok) await handle(res)
  return (await res.json()) as T
}
