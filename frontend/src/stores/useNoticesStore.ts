import { defineStore } from 'pinia'
import { ref } from 'vue'
import { endpoints } from '../api/endpoints'
import { get } from '../api/http'

export interface NoticeSummary {
  id: number
  url: string
  source: string
  title: string
  published_at?: string
  crawled_at: string
  status: string
  notice_type?: string
  deadline?: string
  summary?: string
  keywords: string[]
}

export interface NoticeDetail extends NoticeSummary {
  raw_content?: string
  target_audience?: string
  signup_method?: string
  signup_url?: string
  location?: string
  location_type?: string
  deadline_raw?: string
  key_dates: { label?: string; date?: string }[]
  extracted_at?: string
}

export const useNoticesStore = defineStore('notices', () => {
  const list = ref<NoticeSummary[]>([])
  const sources = ref<string[]>([])
  const types = ref<string[]>([])
  const matchedIds = ref<number[]>([])

  async function fetchNotices(params?: Record<string, unknown>) {
    list.value = await get<NoticeSummary[]>(endpoints.notices.list, params)
  }

  async function fetchFilters() {
    sources.value = await get<string[]>(endpoints.notices.sources)
    types.value = await get<string[]>(endpoints.notices.types)
    matchedIds.value = await get<number[]>(endpoints.notices.matchedIds)
  }

  return { list, sources, types, matchedIds, fetchNotices, fetchFilters }
})
