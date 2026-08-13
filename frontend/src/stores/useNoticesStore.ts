import { defineStore } from 'pinia'
import { ref } from 'vue'
import { endpoints } from '../api/endpoints'
import { get, post } from '../api/http'
import type { MatchMapResult, NoticeDetail, NoticeSummary, StatusCounts } from '../api/schema'

export const useNoticesStore = defineStore('notices', () => {
  const list = ref<NoticeSummary[]>([])
  const sources = ref<string[]>([])
  const types = ref<string[]>([])
  const matchedIds = ref<number[]>([])
  const statusCounts = ref<StatusCounts>({ raw: 0, extracted: 0, partial: 0, failed: 0 })

  async function fetchNotices(params?: Record<string, unknown>) {
    list.value = await get<NoticeSummary[]>(endpoints.notices.list, params)
  }

  async function fetchDetail(id: number) {
    return await get<NoticeDetail>(endpoints.notices.detail(id))
  }

  async function fetchFilters() {
    sources.value = await get<string[]>(endpoints.notices.sources)
    types.value = await get<string[]>(endpoints.notices.types)
    matchedIds.value = await get<number[]>(endpoints.notices.matchedIds)
  }

  async function fetchStatusCounts() {
    statusCounts.value = await get<StatusCounts>(endpoints.notices.statusCounts)
  }

  async function fetchMatchMap(ids: number[]): Promise<MatchMapResult> {
    return await post<MatchMapResult>(endpoints.notices.matchMap, { notice_ids: ids })
  }

  return {
    list,
    sources,
    types,
    matchedIds,
    statusCounts,
    fetchNotices,
    fetchDetail,
    fetchFilters,
    fetchStatusCounts,
    fetchMatchMap,
  }
})