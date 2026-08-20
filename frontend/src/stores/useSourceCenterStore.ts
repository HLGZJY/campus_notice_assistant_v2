import { defineStore } from 'pinia'
import { computed, ref } from 'vue'
import { endpoints } from '../api/endpoints'
import { get, post } from '../api/http'
import type {
  SourceCenterAdoptResult,
  SourceCenterItem,
  SourceCenterOverview,
  SourceCenterPreview,
  SourceCenterPreviewByUrlResult,
} from '../api/schema'

/**
 * 数据源中心：公共目录浏览 / 选用 / 移除 / 预览。
 *
 * 选用/移除写入个人数据源（config/schools YAML），与「系统配置-数据源」页
 * 读写同一份配置，自动双向同步。目录条目带 adopted 状态（按 list_url 判重）。
 */
export const useSourceCenterStore = defineStore('sourceCenter', () => {
  const loading = ref(false)
  const overview = ref<SourceCenterOverview>({
    school: '',
    school_code: '',
    tree: [],
    items: [],
    adopted_count: 0,
  })

  // 筛选态（搜索 / 标签 / 组织树）
  const keyword = ref('')
  const tags = ref<string[]>([])
  const orgKey = ref<string | null>(null) // 树节点 key：group:xxx 或 group:xxx:yyy

  // 全部可用标签（由目录数据派生，用于筛选 chips）
  const allTags = computed(() => {
    const set = new Set<string>()
    for (const it of overview.value.items ?? []) for (const t of it.tags ?? []) set.add(t)
    return [...set].sort()
  })

  const filtered = computed<SourceCenterItem[]>(() => {
    const kw = keyword.value.trim().toLowerCase()
    return (overview.value.items ?? []).filter((it) => {
      if (tags.value.length && !tags.value.some((t) => (it.tags ?? []).includes(t))) return false
      if (orgKey.value) {
        // 树 key 约定：一级 group:{group} / 二级 group:{group}:{org} / 三级 item:{id}
        const parts = orgKey.value.split(':')
        if (parts[0] === 'item') {
          if (it.id !== parts[1]) return false
        } else {
          const group = parts[1]
          const org = parts[2]
          if (it.org_group !== group) return false
          if (org && it.org !== org) return false
        }
      }
      if (kw) {
        const haystack = `${it.name} ${it.org} ${it.org_group} ${it.description} ${(it.tags ?? []).join(' ')}`.toLowerCase()
        if (!haystack.includes(kw)) return false
      }
      return true
    })
  })

  async function fetchOverview() {
    loading.value = true
    try {
      overview.value = await get<SourceCenterOverview>(endpoints.sourceCenter.overview)
    } finally {
      loading.value = false
    }
  }

  function applyAdoptResult(result: SourceCenterAdoptResult) {
    const items = overview.value.items ?? []
    const it = items.find((x) => x.id === result.source_id)
    if (it) it.adopted = result.adopted
    overview.value.adopted_count = items.filter((x) => x.adopted).length
  }

  async function adopt(sourceId: string) {
    const result = await post<SourceCenterAdoptResult>(endpoints.sourceCenter.adopt(sourceId))
    applyAdoptResult(result)
    return result
  }

  async function remove(sourceId: string) {
    const result = await post<SourceCenterAdoptResult>(endpoints.sourceCenter.remove(sourceId))
    applyAdoptResult(result)
    return result
  }

  async function preview(sourceId: string, limit = 10) {
    return await get<SourceCenterPreview>(endpoints.sourceCenter.preview(sourceId), { limit })
  }

  async function previewByUrl(url: string, limit = 10) {
    return await post<SourceCenterPreviewByUrlResult>(endpoints.sourceCenter.previewUrl, { url, limit })
  }

  function resetFilters() {
    keyword.value = ''
    tags.value = []
    orgKey.value = null
  }

  return {
    loading,
    overview,
    keyword,
    tags,
    orgKey,
    allTags,
    filtered,
    fetchOverview,
    adopt,
    remove,
    preview,
    previewByUrl,
    resetFilters,
  }
})
