import { defineStore } from 'pinia'
import { ref } from 'vue'
import { endpoints } from '../api/endpoints'
import { get } from '../api/http'
import { fetchQaHistory, deleteQaHistory, clearQaHistory } from '../api/qa'
import type { IndexStatsView, QaHistoryItem, QaSourceRef, QaStreamEvent } from '../api/schema'

const SESSION_KEY = 'qa_session_id'
const HISTORY_CACHE_KEY = 'qa_history_cache'

export interface QaMessage {
  id: string
  question: string
  answer: string
  sources: QaSourceRef[]
  retrievedChunks: number
  error?: string
  currentStage?: string
  currentStageStartedAt?: number
  stageElapsedMs?: number
  cached?: boolean
  createdAt?: string
}

function getSessionId(): string {
  let id = localStorage.getItem(SESSION_KEY)
  if (!id) {
    id = crypto.randomUUID()
    localStorage.setItem(SESSION_KEY, id)
  }
  return id
}

export const useQaStore = defineStore('qa', () => {
  // 当前对话（聊天区）：新对话即清空；切页不销毁（Pinia 全局单例）
  const messages = ref<QaMessage[]>([])
  // 持久化历史（抽屉）：来自后端分页 + localStorage 首屏缓存，与 messages 分离
  const historyItems = ref<QaMessage[]>([])
  const streaming = ref(false)
  const currentStage = ref<string>('')
  const currentStageStartedAt = ref<number>(0)
  // 当前流的 abort 控制器放 store：切页后返回仍可取消
  const activeAbort = ref<AbortController | null>(null)

  async function fetchIndexStats() {
    return await get<IndexStatsView>(endpoints.qa.indexStats)
  }

  function itemToMessage(item: QaHistoryItem): QaMessage {
    return {
      id: `hist-${item.id}`,
      question: item.question_text,
      answer: item.answer_text,
      sources: item.sources ?? [],
      retrievedChunks: item.retrieved_chunks,
      cached: item.status === 'cache_hit' || item.hit_count > 0,
      createdAt: item.created_at,
    }
  }

  async function loadHistory() {
    try {
      const cached = localStorage.getItem(HISTORY_CACHE_KEY)
      if (cached) {
        const items: QaHistoryItem[] = JSON.parse(cached)
        historyItems.value = items.map(itemToMessage)
      }
    } catch { /* ignore */ }

    try {
      const page = await fetchQaHistory(1, 50, getSessionId())
      historyItems.value = (page.items ?? []).map(itemToMessage)
      localStorage.setItem(HISTORY_CACHE_KEY, JSON.stringify(page.items ?? []))
    } catch { /* 静默失败，不打断 UI */ }
  }

  function refreshHistory() {
    return loadHistory()
  }

  function cancel() {
    activeAbort.value?.abort()
    activeAbort.value = null
  }

  function startNewConversation() {
    cancel()
    messages.value = []
    currentStage.value = ''
    currentStageStartedAt.value = 0
    // 当前对话的问答对已由后端在 done 时持久化；刷新抽屉即为“归档到历史”
    loadHistory()
  }

  async function askStream(
    payload: { question: string; params?: Record<string, unknown> },
    onEvent: (evt: QaStreamEvent) => void,
  ) {
    streaming.value = true
    currentStage.value = ''
    currentStageStartedAt.value = Date.now()
    const abort = new AbortController()
    activeAbort.value = abort
    try {
      const params = new URLSearchParams({ question: payload.question })
      params.set('user_session_id', getSessionId())
      if (payload.params) {
        for (const [k, v] of Object.entries(payload.params)) {
          if (v !== undefined && v !== null) params.set(k, String(v))
        }
      }
      const res = await fetch(`${endpoints.qa.stream}?${params.toString()}`, {
        method: 'GET',
        headers: { 'Accept': 'text/event-stream' },
        signal: abort.signal,
      })

      if (!res.ok) {
        const txt = await res.text().catch(() => 'request failed')
        throw new Error(`${res.status}: ${txt}`)
      }
      if (!res.body) throw new Error('stream not supported')

      const reader = res.body.getReader()
      const decoder = new TextDecoder()
      let buffer = ''
      let done = false
      while (!done) {
        const { value, done: d } = await reader.read()
        done = d
        if (value) {
          buffer += decoder.decode(value, { stream: !done })
          let idx: number
          while ((idx = buffer.indexOf('\n\n')) !== -1) {
            const block = buffer.slice(0, idx)
            buffer = buffer.slice(idx + 2)
            for (const line of block.split('\n')) {
              if (!line.startsWith('data:')) continue
              const raw = line.slice(5).trim()
              if (!raw) continue
              let evt: Record<string, any>
              try {
                evt = JSON.parse(raw)
              } catch {
                continue
              }
              if (evt.type === 'status') {
                currentStage.value = evt.stage ?? ''
                currentStageStartedAt.value = Date.now()
                onEvent({
                  type: 'status',
                  stage: evt.stage ?? '',
                  message: evt.message ?? '',
                  elapsed_ms: evt.elapsed_ms ?? 0,
                  similarity: evt.similarity,
                })
              } else if (evt.type === 'delta' && typeof evt.content === 'string') {
                onEvent({ type: 'delta', content: evt.content })
              } else if (evt.type === 'done') {
                onEvent({
                  type: 'done',
                  answer: evt.answer ?? '',
                  sources: evt.sources ?? [],
                  retrieved_chunks: evt.retrieved_chunks ?? 0,
                })
              } else if (evt.type === 'error') {
                onEvent({ type: 'error', message: evt.message ?? 'stream error' })
              }
            }
          }
        }
        if (abort.signal.aborted) {
          throw new DOMException('aborted', 'AbortError')
        }
      }
    } finally {
      streaming.value = false
      currentStage.value = ''
      if (activeAbort.value === abort) activeAbort.value = null
    }
  }

  async function removeHistory(id: string | number) {
    const realId = parseInt(String(id).replace('hist-', ''), 10)
    await deleteQaHistory(realId)
    historyItems.value = historyItems.value.filter((m) => m.id !== `hist-${realId}`)
  }

  async function clearAllHistory() {
    await clearQaHistory(getSessionId())
    historyItems.value = []
    localStorage.removeItem(HISTORY_CACHE_KEY)
  }

  return {
    messages,
    historyItems,
    streaming,
    currentStage,
    currentStageStartedAt,
    fetchIndexStats,
    loadHistory,
    refreshHistory,
    askStream,
    cancel,
    startNewConversation,
    removeHistory,
    clearAllHistory,
  }
})