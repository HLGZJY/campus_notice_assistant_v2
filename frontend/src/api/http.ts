export class ApiError extends Error {
  status: number
  constructor(status: number, message: string) {
    super(message)
    this.status = status
  }
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
  const res = await fetch(target, options)
  if (!res.ok) await handle(res)
  return (await res.json()) as T
}

export async function post<T = unknown>(url: string, body?: unknown, options?: RequestInit): Promise<T> {
  const init: RequestInit = Object.assign({
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: body ? JSON.stringify(body) : undefined,
  }, options || {})
  const res = await fetch(url, init)
  if (!res.ok) await handle(res)
  return (await res.json()) as T
}

export async function put<T = unknown>(url: string, body?: unknown, options?: RequestInit): Promise<T> {
  const init: RequestInit = Object.assign({
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: body ? JSON.stringify(body) : undefined,
  }, options || {})
  const res = await fetch(url, init)
  if (!res.ok) await handle(res)
  return (await res.json()) as T
}

export async function patch<T = unknown>(url: string, body?: unknown, options?: RequestInit): Promise<T> {
  const init: RequestInit = Object.assign({
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: body ? JSON.stringify(body) : undefined,
  }, options || {})
  const res = await fetch(url, init)
  if (!res.ok) await handle(res)
  return (await res.json()) as T
}

export async function del<T = unknown>(url: string, options?: RequestInit): Promise<T> {
  const init: RequestInit = Object.assign({ method: 'DELETE' }, options || {})
  const res = await fetch(url, init)
  if (!res.ok) await handle(res)
  return (await res.json()) as T
}
