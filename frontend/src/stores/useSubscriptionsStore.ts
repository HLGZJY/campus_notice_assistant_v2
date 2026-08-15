import { defineStore } from 'pinia'
import { ref } from 'vue'
import { endpoints } from '../api/endpoints'
import { del, get, post, put } from '../api/http'
import type {
  NoticePage,
  SubscriptionCreateRequest,
  SubscriptionItem,
  SubscriptionMutationResult,
  SubscriptionPreview,
  SubscriptionPreviewRequest,
  SubscriptionStats,
  SubscriptionUpdateRequest,
  TaskCreateResult,
} from '../api/schema'

export const useSubscriptionsStore = defineStore('subscriptions', () => {
  const list = ref<SubscriptionItem[]>([])
  const stats = ref<SubscriptionStats>({ total: 0, enabled: 0, matches: 0, total_notices: 0 })
  const loading = ref(false)

  async function fetchList() {
    loading.value = true
    try {
      list.value = await get<SubscriptionItem[]>(endpoints.subscriptions.list)
    } finally {
      loading.value = false
    }
  }

  async function fetchStats() {
    stats.value = await get<SubscriptionStats>(endpoints.subscriptions.stats)
  }

  async function preview(body: SubscriptionPreviewRequest) {
    return await post<SubscriptionPreview>(endpoints.subscriptions.preview, body)
  }

  async function fetchMatchedNotices(id: number, page = 1, pageSize = 20) {
    return await get<NoticePage>(endpoints.subscriptions.notices(id), {
      page,
      page_size: pageSize,
    })
  }

  async function create(body: SubscriptionCreateRequest) {
    return await post<TaskCreateResult>(endpoints.subscriptions.list, body)
  }

  async function update(id: number, body: SubscriptionUpdateRequest) {
    return await put<TaskCreateResult>(endpoints.subscriptions.detail(id), body)
  }

  async function toggle(id: number, enabled: boolean) {
    return await post<TaskCreateResult>(endpoints.subscriptions.toggle(id), { enabled })
  }

  async function remove(id: number) {
    return await del<SubscriptionMutationResult>(endpoints.subscriptions.detail(id))
  }

  async function matchAll() {
    return await post<TaskCreateResult>(endpoints.subscriptions.matchAll)
  }

  return {
    list,
    stats,
    loading,
    fetchList,
    fetchStats,
    preview,
    fetchMatchedNotices,
    create,
    update,
    toggle,
    remove,
    matchAll,
  }
})