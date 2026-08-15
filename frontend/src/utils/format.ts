/**
 * 待办展示工具：相对时间 / 状态派生。
 *
 * daysUntil 按「日期差」（忽略时刻）计算，与后端 reminder_service._days_until
 * 口径一致，规避 deadline 落在 00:00 时被误判为已过期的歧义。
 */

export function fmtDate(iso?: string | null): string {
  if (!iso) return '—'
  const s = iso.replace('T', ' ')
  return s.endsWith(' 00:00') ? s.slice(0, 10) : s.slice(0, 16)
}

/** 距截止的天数（按日期差，忽略时刻）。无法解析或无截止返回 null。 */
export function daysUntil(iso?: string | null): number | null {
  if (!iso) return null
  const due = new Date(iso)
  if (Number.isNaN(due.getTime())) return null
  const now = new Date()
  const dueDate = new Date(due.getFullYear(), due.getMonth(), due.getDate())
  const today = new Date(now.getFullYear(), now.getMonth(), now.getDate())
  return Math.round((dueDate.getTime() - today.getTime()) / 86400000)
}

/** 相对截止文案：剩余X天 / 今天截止 / 已逾期X天 / 无截止。 */
export function relativeDueText(iso?: string | null): string {
  const days = daysUntil(iso)
  if (days === null) return '无截止'
  if (days < 0) return `已逾期 ${-days} 天`
  if (days === 0) return '今天截止'
  return `剩余 ${days} 天`
}

export interface TodoStatusMeta {
  label: string
  type: 'success' | 'default' | 'error' | 'warning' | 'info'
}

/** 由 status + due_at 派生展示状态标签。 */
export function todoStatusMeta(status: string, dueAt?: string | null): TodoStatusMeta {
  if (status === 'done') return { label: '已完成', type: 'success' }
  if (status === 'skipped') return { label: '已跳过', type: 'default' }
  const days = daysUntil(dueAt)
  if (days !== null && days < 0) return { label: '逾期', type: 'error' }
  if (days !== null && days <= 3) return { label: '临期', type: 'warning' }
  return { label: '待开始', type: 'info' }
}