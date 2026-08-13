import { defineStore } from 'pinia'
import { ref } from 'vue'
import { endpoints } from '../api/endpoints'
import { get } from '../api/http'
import type { IndexStatsView, QaSourceRef, QaStreamEvent } from '../api/schema'

export interface QaMessage {
  id: string
  question: string
  answer: string
  sources: QaSourceRef[]
  retrievedChunks: number
  error?: string
}

export const useQaStore = defineStore('qa', () => {
  const history = ref<QaMessage[]>([])
  const streaming = ref(false)

  async function fetchIndexStats() {
    return await get<IndexStatsView>(endpoints.qa.indexStats)
  }

  async function askStream(
    payload: { question: string; params?: Record<string, unknown> },
    onEvent: (evt: QaStreamEvent) => void,
    signal?: AbortSignal,
  ) {
    // 后端契约：GET /qa/ask/stream?question= （SSE，见 api/routes/qa.py）
    streaming.value = true
    try {
      const params = new URLSearchParams({ question: payload.question })
      if (payload.params) {
        for (const [k, v] of Object.entries(payload.params)) {
          if (v !== undefined && v !== null) params.set(k, String(v))
        }
      }
      const res = await fetch(`${endpoints.qa.stream}?${params.toString()}`, {
        method: 'GET',
        headers: { 'Accept': 'text/event-stream' },
        signal,
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
          // 按 SSE 空行边界切出完整事件块
          let idx: number
          while ((idx = buffer.indexOf('\n\n')) !== -1) {
            const block = buffer.slice(0, idx)
            buffer = buffer.slice(idx + 2)
            for (const line of block.split('\n')) {
              if (!line.startsWith('data:')) continue
              const raw = line.slice(5).trim()
              if (!raw) continue
              let evt: { type?: string; content?: string; answer?: string; sources?: QaSourceRef[]; retrieved_chunks?: number; message?: string }
              try {
                evt = JSON.parse(raw)
              } catch {
                continue
              }
              if (evt.type === 'delta' && typeof evt.content === 'string') {
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
        if (signal?.aborted) {
          throw new DOMException('aborted', 'AbortError')
        }
      }
    } finally {
      streaming.value = false
    }
  }

  return { history, streaming, fetchIndexStats, askStream }
})