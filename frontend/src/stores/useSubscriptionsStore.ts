import { defineStore } from 'pinia'
import { ref } from 'vue'
import { endpoints } from '../api/endpoints'
import { get, post } from '../api/http'

export interface SubscriptionItem {
  id: number
  name: string
  filter?: Record<string, unknown>
  active: boolean
  last_matched?: number
  preview?: unknown
}

export interface SubscriptionStats {
  total: number
  active: number
  matched_last_hour?: number
}

export const useSubscriptionsStore = defineStore('subscriptions', () => {
  const list = ref<SubscriptionItem[]>([])
  const stats = ref<SubscriptionStats>({ total: 0, active: 0 })
  const loading = ref(false)

  async function fetchList(params?: Record<string, unknown>) {
    loading.value = true
    try {
      list.value = await get<SubscriptionItem[]>(endpoints.subscriptions.list, params)
    } finally {
      loading.value = false
    }
  }

  async function fetchStats() {
    stats.value = await get<SubscriptionStats>(endpoints.subscriptions.stats)
  }

  async function preview(params: Record<string, unknown>) {
    // preview likely accepts the subscription filter and returns a small result set
    return await post<unknown>(endpoints.subscriptions.preview, params)
  }

  async function toggle(id: number) {
    return await post(endpoints.subscriptions.toggle(id))
  }

  async function fetchDetail(id: number) {
    return await get<SubscriptionItem>(endpoints.subscriptions.detail(id))
  }

  return { list, stats, loading, fetchList, fetchStats, preview, toggle, fetchDetail }
})
