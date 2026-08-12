import { defineStore } from 'pinia'
import { ref } from 'vue'
import { endpoints } from '../api/endpoints'
import { get } from '../api/http'

export interface ReminderItem {
  id: number
  notice_id: number
  notice_title?: string
  notice_source?: string
  due_at: string
  tier: string
  tier_label: string
  remind_on: string
  status: string
  is_today: boolean
}

export const useRemindersStore = defineStore('reminders', () => {
  const pendingCount = ref(0)
  const reminders = ref<ReminderItem[]>([])

  async function fetchPendingCount() {
    pendingCount.value = await get<number>(endpoints.reminders.pendingCount)
  }

  async function fetchReminders(status?: string) {
    reminders.value = await get<ReminderItem[]>(endpoints.reminders.list, { status, limit: 200 })
  }

  return { pendingCount, reminders, fetchPendingCount, fetchReminders }
})
