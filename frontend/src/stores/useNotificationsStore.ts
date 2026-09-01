import { defineStore } from 'pinia'
import { ref } from 'vue'
import { endpoints } from '../api/endpoints'
import { get, post } from '../api/http'
import type { ReminderItem, ReminderStats, UpdateCheckResult } from '../api/schema'

/**
 * 通知中心（A6，B21.T1）。
 *
 * 聚合两类「通知」：
 *  1. **提醒**（reminders）——主数据源，未读徽标 = pending 数，已读持久化复用
 *     `/reminders/{id}/status`（后端已实现 mark_reminder，零改动）。
 *  2. **系统消息**——更新检查结果 / 桌面壳状态（本地派生，无独立后端存储，
 *     由页面实时拉取 /update/check 与 /desktop/status 拼装）。
 *
 * 已读状态持久化由后端 reminders 表承担（status=pending/read/ignored），
 * 跨重启保持（D-50）。
 */
export interface SystemMessage {
  id: string
  kind: 'update' | 'desktop' | 'info'
  title: string
  detail: string
  level: 'info' | 'success' | 'warning' | 'error'
  timestamp: string
}

export const useNotificationsStore = defineStore('notifications', () => {
  // ---- 提醒（核心通知） ----
  const pendingCount = ref(0)
  const reminders = ref<ReminderItem[]>([])
  const stats = ref<ReminderStats>({ pending: 0, read: 0, ignored: 0, total: 0 })
  const loading = ref(false)

  // ---- 系统消息（本地派生） ----
  const systemMessages = ref<SystemMessage[]>([])
  const updateCheckedAt = ref<string>('')

  async function fetchPendingCount() {
    try {
      pendingCount.value = await get<number>(endpoints.reminders.pendingCount)
    } catch {
      pendingCount.value = 0
    }
  }

  async function fetchStats() {
    try {
      stats.value = await get<ReminderStats>(endpoints.reminders.stats)
    } catch {
      stats.value = { pending: 0, read: 0, ignored: 0, total: 0 }
    }
  }

  async function fetchReminders(status?: string) {
    loading.value = true
    try {
      reminders.value = await get<ReminderItem[]>(endpoints.reminders.list, { status, limit: 200 })
    } finally {
      loading.value = false
    }
  }

  /** 标记某条提醒已读 / 忽略；成功后刷新未读计数与列表。 */
  async function mark(id: number, status: 'read' | 'ignored') {
    await post(endpoints.reminders.status(id), { status })
    await Promise.all([fetchPendingCount(), fetchStats()])
    return status
  }

  /** 重新拉取全部（未读数 + 统计）。 */
  async function refresh() {
    await Promise.all([fetchPendingCount(), fetchStats()])
  }

  /**
   * 派生系统消息：更新检查结果 + 桌面壳状态。
   * 失败静默（无后端 / 未配 repo 时不打扰），返回 true 表示拉取成功。
   */
  async function loadSystemMessages(): Promise<boolean> {
    const messages: SystemMessage[] = []
    const now = new Date().toISOString()
    let ok = false

    // 1. 更新检查
    try {
      const up = await get<UpdateCheckResult>(endpoints.update.check)
      updateCheckedAt.value = up.checked_at || now
      if (up.update_available) {
        messages.push({
          id: 'update-available',
          kind: 'update',
          title: `发现新版本 v${up.latest_version}`,
          detail: up.notes || '点击「检查更新」查看下载入口。',
          level: 'warning',
          timestamp: up.checked_at || now,
        })
      } else {
        messages.push({
          id: 'update-current',
          kind: 'update',
          title: '已是最新版本',
          detail: `当前版本 v${up.current_version}，无需更新。`,
          level: 'success',
          timestamp: up.checked_at || now,
        })
      }
      ok = true
    } catch {
      messages.push({
        id: 'update-unavailable',
        kind: 'update',
        title: '更新检查暂不可用',
        detail: '未配置发布仓库或网络不可达，不影响正常使用。',
        level: 'info',
        timestamp: now,
      })
    }

    // 2. 桌面壳状态（仅桌面形态有实际意义；浏览器降级时 available=false 静默）
    try {
      const st = await get<DesktopStatusLike>(endpoints.desktop.status)
      if (st.available) {
        const paused = st.scheduler?.paused || st.scheduler?.heavy_paused
        messages.push({
          id: 'desktop-status',
          kind: 'desktop',
          title: paused ? '调度已暂停' : '调度运行中',
          detail: paused
            ? '抓取 / 批量提取等重活当前已挂起，恢复后可继续。'
            : '后台调度正常，通知抓取 / 提取 / 每日体检按计划运行。',
          level: paused ? 'warning' : 'success',
          timestamp: now,
        })
      }
    } catch {
      // 非桌面环境或后端不可达：不产生系统消息
    }

    systemMessages.value = messages
    return ok
  }

  return {
    pendingCount,
    reminders,
    stats,
    loading,
    systemMessages,
    updateCheckedAt,
    fetchPendingCount,
    fetchStats,
    fetchReminders,
    mark,
    refresh,
    loadSystemMessages,
  }
})

/** /desktop/status 的最小结构（避免全量引用造成循环依赖）。 */
interface DesktopStatusLike {
  available?: boolean
  scheduler?: { paused?: boolean; heavy_paused?: boolean }
}
