<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { useQaStore, type QaMessage } from '../stores/useQaStore'
import { trackEvent, EVENT_TYPES } from '../api/events'
import type { IndexStatsView } from '../api/schema'

const qa = useQaStore()
const question = ref('')
const indexStats = ref<IndexStatsView | null>(null)
let abortController: AbortController | null = null

onMounted(async () => {
  indexStats.value = await qa.fetchIndexStats().catch(() => null)
})

async function ask() {
  const q = question.value.trim()
  if (!q) return
  const msg: QaMessage = {
    id: crypto.randomUUID(),
    question: q,
    answer: '',
    sources: [],
    retrievedChunks: 0,
  }
  qa.history.push(msg)
  question.value = ''
  abortController = new AbortController()
  trackEvent(EVENT_TYPES.QA_ASK, undefined, q)
  try {
    await qa.askStream(
      { question: q },
      (evt) => {
        if (evt.type === 'delta') {
          msg.answer += evt.content
        } else if (evt.type === 'done') {
          msg.answer = evt.answer
          msg.sources = evt.sources
          msg.retrievedChunks = evt.retrieved_chunks
          delete msg.error
        } else if (evt.type === 'error') {
          msg.error = evt.message
        }
      },
      abortController.signal,
    )
  } catch (e) {
    if ((e as { name?: string })?.name !== 'AbortError') {
      msg.error = e instanceof Error ? e.message : String(e)
    }
  } finally {
    abortController = null
  }
}

function cancel() {
  abortController?.abort()
  abortController = null
}
</script>

<template>
  <n-space vertical size="large">
    <n-card title="智能问答">
      <template #header-extra>
        <n-tag :bordered="false" :type="indexStats?.error ? 'error' : 'info'" round>
          {{ indexStats?.chunks ?? 0 }} chunks
          <template v-if="indexStats?.persist_dir"> · {{ indexStats.persist_dir }}</template>
          <template v-if="indexStats?.error"> · 索引异常</template>
        </n-tag>
      </template>
      <n-input
        v-model:value="question"
        type="textarea"
        :autosize="{ minRows: 2, maxRows: 4 }"
        placeholder="输入问题，回车发送"
        @keydown.enter.exact.prevent="ask"
        @keydown.enter.exact="ask"
      />
      <n-space style="margin-top: 12px">
        <n-button type="primary" :loading="qa.streaming" :disabled="!question.trim()" @click="ask">
          提问
        </n-button>
        <n-button secondary :disabled="!qa.streaming" @click="cancel">取消</n-button>
      </n-space>
    </n-card>

    <n-card title="对话历史">
      <div v-if="qa.history.length === 0" style="color: #888">暂无对话，输入问题开始。</div>
      <n-space v-else vertical size="large">
        <template v-for="msg in qa.history" :key="msg.id">
          <div>
            <n-tag type="primary" :bordered="false" round>问</n-tag>
            <span style="margin-left: 8px">{{ msg.question }}</span>
          </div>
          <n-card size="small" :bordered="msg.error ? false : true">
            <pre v-if="msg.answer" style="white-space: pre-wrap; word-break: break-word">{{ msg.answer }}</pre>
            <div v-else-if="qa.streaming">思考中…</div>
            <div v-if="msg.error" style="color: #d03050; margin-top: 4px">[错误] {{ msg.error }}</div>
            <div v-if="msg.sources.length" style="margin-top: 12px">
              <div style="font-weight: 600; margin-bottom: 8px">
                引用来源（{{ msg.retrievedChunks }} chunks）
              </div>
              <n-space vertical size="small">
                <n-card
                  v-for="(s, i) in msg.sources"
                  :key="i"
                  size="small"
                  :bordered="false"
                  style="background: #fafafa"
                >
                  <div>
                    <b>[{{ i + 1 }}]</b> {{ s.title || '（无标题）' }}
                    <n-tag size="small" :bordered="false" type="info" style="margin-left: 6px">
                      {{ s.notice_type || '未分类' }}
                    </n-tag>
                  </div>
                  <div v-if="s.deadline" style="font-size: 12px; color: #888; margin-top: 2px">
                    截止 {{ s.deadline }}
                  </div>
                  <n-a
                    v-if="s.url"
                    :href="s.url"
                    target="_blank"
                    rel="noopener"
                    style="font-size: 12px"
                  >
                    {{ s.url }}
                  </n-a>
                </n-card>
              </n-space>
            </div>
          </n-card>
        </template>
      </n-space>
    </n-card>
  </n-space>
</template>