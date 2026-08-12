import { defineStore } from 'pinia'
import { ref } from 'vue'
import { endpoints } from '../api/endpoints'
import { get } from '../api/http'

export interface QaIndexStats {
  total_documents: number
  total_tokens?: number
  up_to_date?: boolean
}

export const useQaStore = defineStore('qa', () => {
  const history = ref<{ id?: string; question: string; answer?: string }[]>([])
  const streaming = ref(false)

  async function fetchIndexStats() {
    return await get<QaIndexStats>(endpoints.qa.indexStats)
  }

  async function askStream(payload: { question: string; params?: Record<string, unknown> }, onChunk: (chunk: string) => void, signal?: AbortSignal) {
    // Sends request to QA streaming endpoint and invokes onChunk for each decoded chunk
    streaming.value = true
    try {
      const res = await fetch(endpoints.qa.stream, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
          signal,
        })

        if (!res.ok) {
          const txt = await res.text().catch(() => 'request failed')
          throw new Error(`${res.status}: ${txt}`)
        }

        const reader = res.body?.getReader()
        if (!reader) throw new Error('stream not supported')
        const decoder = new TextDecoder()
        let done = false
        while (!done) {
          const { value, done: d } = await reader.read()
          done = d
          if (value) {
            const text = decoder.decode(value, { stream: !done })
            onChunk(text)
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
