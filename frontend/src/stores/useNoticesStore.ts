import { defineStore } from 'pinia'
import { ref } from 'vue'
import { endpoints } from '../api/endpoints'
import { del, get, post } from '../api/http'
import type {
  MatchMapResult,
  NoticeBatchFilter,
  NoticeBatchRequest,
  NoticeDetail,
  NoticeMeta,
  NoticeMutationResult,
  NoticePage,
  NoticeSummary,
  StatusCounts,
  TaskCreateResult,
} from '../api/schema'

export const useNoticesStore = defineStore('notices', () => {
  const list = ref<NoticeSummary[]>([])
  const total = ref(0)
  const page = ref(1)
  const pageSize = ref(10)
  const sources = ref<string[]>([])
  const types = ref<string[]>([])
  const matchedIds = ref<number[]>([])
  const statusCounts = ref<StatusCounts>({ raw: 0, extracted: 0, partial: 0, failed: 0 })
  const meta = ref<NoticeMeta | null>(null)

  async function fetchNotices(params?: Record<string, unknown>) {
    const res = await get<NoticePage>(endpoints.notices.list, params)
    list.value = res.items
    total.value = res.total
    page.value = res.page
    pageSize.value = res.page_size
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

  async function fetchMeta() {
    meta.value = await get<NoticeMeta>(endpoints.notices.meta)
  }

  async function fetchMatchMap(ids: number[]): Promise<MatchMapResult> {
    return await post<MatchMapResult>(endpoints.notices.matchMap, { notice_ids: ids })
  }

  async function deleteNotice(id: number): Promise<NoticeMutationResult> {
    return await del<NoticeMutationResult>(endpoints.notices.delete(id))
  }

  async function resetNotice(id: number): Promise<NoticeMutationResult> {
    return await post<NoticeMutationResult>(endpoints.notices.reset(id))
  }

  async function reExtractNotice(id: number): Promise<TaskCreateResult> {
    return await post<TaskCreateResult>(endpoints.notices.reExtract(id))
  }

  async function batchDelete(filter: NoticeBatchFilter): Promise<TaskCreateResult> {
    return await post<TaskCreateResult>(endpoints.notices.batchDelete, filter)
  }

  async function batchReset(req: NoticeBatchRequest): Promise<TaskCreateResult> {
    return await post<TaskCreateResult>(endpoints.notices.batchReset, req)
  }

  return {
    list,
    total,
    page,
    pageSize,
    sources,
    types,
    matchedIds,
    statusCounts,
    meta,
    fetchNotices,
    fetchDetail,
    fetchFilters,
    fetchStatusCounts,
    fetchMeta,
    fetchMatchMap,
    deleteNotice,
    resetNotice,
    reExtractNotice,
    batchDelete,
    batchReset,
  }
})