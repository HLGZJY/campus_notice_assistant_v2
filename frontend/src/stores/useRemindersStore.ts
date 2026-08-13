import { defineStore } from 'pinia'
import { ref } from 'vue'
import { endpoints } from '../api/endpoints'
import { get, post } from '../api/http'
import type { ReminderItem, ReminderStats } from '../api/schema'

export const useRemindersStore = defineStore('reminders', () => {
  const pendingCount = ref(0)
  const reminders = ref<ReminderItem[]>([])
  const stats = ref<ReminderStats>({ pending: 0, read: 0, ignored: 0, total: 0 })

  async function fetchPendingCount() {
    pendingCount.value = await get<number>(endpoints.reminders.pendingCount)
  }

  async function fetchReminders(status?: string) {
    reminders.value = await get<ReminderItem[]>(endpoints.reminders.list, { status, limit: 200 })
  }

  async function fetchStats() {
    stats.value = await get<ReminderStats>(endpoints.reminders.stats)
  }

  async function mark(id: number, status: string) {
    await post(endpoints.reminders.status(id), { status })
    await fetchPendingCount()
    await fetchReminders('pending')
  }

  return { pendingCount, reminders, stats, fetchPendingCount, fetchReminders, fetchStats, mark }
})