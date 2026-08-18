<script setup lang="ts">
import { nextTick, ref, reactive, watch, onMounted } from 'vue'
import { ChatbubbleEllipsesOutline, LayersOutline, SendOutline, SparklesOutline, TrashOutline } from '@vicons/ionicons5'
import { useQaStore, type QaMessage } from '../stores/useQaStore'
import MarkdownView from '../components/MarkdownView.vue'
import { trackEvent, EVENT_TYPES } from '../api/events'
import type { IndexStatsView } from '../api/schema'

const qa = useQaStore()
const question = ref('')
const indexStats = ref<IndexStatsView | null>(null)
const chatBody = ref<HTMLElement | null>(null)
let abortController: AbortController | null = null

const suggestions = ['最近有哪些奖学金通知？', '有哪些竞赛可以报名？', '帮我总结近一周的关键通知']

onMounted(async () => {
  indexStats.value = await qa.fetchIndexStats().catch(() => null)
})

async function scrollToBottom() {
  await nextTick()
  if (chatBody.value) chatBody.value.scrollTop = chatBody.value.scrollHeight
}

watch(
  () => qa.history.map((m) => m.answer.length),
  () => scrollToBottom(),
)
watch(
  () => qa.streaming,
  () => scrollToBottom(),
)

async function ask() {
  const q = question.value.trim()
  if (!q) return
  const msg = reactive<QaMessage>({
    id: crypto.randomUUID(),
    question: q,
    answer: '',
    sources: [],
    retrievedChunks: 0,
  })
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

function clearHistory() {
  qa.history.length = 0
}
</script>

<template>
  <n-card :bordered="false" class="qa-card">
    <template #header>
      <div class="section-title">
        <n-icon size="18" color="var(--violet)"><ChatbubbleEllipsesOutline /></n-icon>
        智能问答
        <n-tag :bordered="false" round :type="indexStats?.error ? 'error' : 'info'" size="small" class="index-tag">
          <template #icon>
            <n-icon><LayersOutline /></n-icon>
          </template>
          {{ indexStats?.chunks ?? 0 }} chunks
          <template v-if="indexStats?.persist_dir"> · {{ indexStats.persist_dir }}</template>
          <template v-if="indexStats?.error"> · 索引异常</template>
        </n-tag>
      </div>
    </template>

    <div ref="chatBody" class="chat-body">
      <div v-if="qa.history.length === 0" class="chat-empty">
        <div class="chat-empty-icon">
          <n-icon size="44"><ChatbubbleEllipsesOutline /></n-icon>
        </div>
        <div class="chat-empty-title">输入问题，开始与通知库对话</div>
        <div class="chat-empty-sub">基于已入库通知的语义问答，答案附带引用来源</div>
        <div class="chat-suggestions">
          <n-tag
            v-for="s in suggestions"
            :key="s"
            size="medium"
            round
            :bordered="false"
            class="suggestion-tag"
            @click="question = s"
          >
            {{ s }}
          </n-tag>
        </div>
      </div>

      <template v-else>
        <div v-for="msg in qa.history" :key="msg.id" class="msg-pair">
          <div class="msg msg--user">
            <div class="msg-avatar msg-avatar--user">
              <n-icon size="16"><SparklesOutline /></n-icon>
            </div>
            <div class="bubble bubble--user">{{ msg.question }}</div>
          </div>
          <div class="msg msg--assistant">
            <div class="msg-avatar msg-avatar--assistant">
              <n-icon size="16"><ChatbubbleEllipsesOutline /></n-icon>
            </div>
            <div class="bubble bubble--assistant">
              <template v-if="msg.answer">
                <MarkdownView :content="msg.answer" />
              </template>
              <div v-else-if="msg.error" class="answer-error">[错误] {{ msg.error }}</div>
              <div v-else class="thinking">
                <span class="thinking-dot"></span>
                <span class="thinking-dot"></span>
                <span class="thinking-dot"></span>
              </div>

              <div v-if="msg.sources.length" class="sources">
                <div class="sources-header">
                  <n-icon size="14"><LayersOutline /></n-icon>
                  引用来源（{{ msg.retrievedChunks }} chunks）
                </div>
                <div class="source-list">
                  <div v-for="(s, i) in msg.sources" :key="i" class="source-item">
                    <span class="source-idx">{{ i + 1 }}</span>
                    <div class="source-info">
                      <div class="source-title">
                        {{ s.title || '（无标题）' }}
                        <n-tag size="small" :bordered="false" type="info">{{ s.notice_type || '未分类' }}</n-tag>
                      </div>
                      <div class="source-meta">
                        <span v-if="s.deadline">截止 {{ s.deadline }}</span>
                        <n-a v-if="s.url" :href="s.url" target="_blank" rel="noopener">查看原文</n-a>
                      </div>
                    </div>
                  </div>
                </div>
              </div>
            </div>
          </div>
        </div>
      </template>
    </div>

    <div class="chat-input">
      <n-input
        v-model:value="question"
        type="textarea"
        :autosize="{ minRows: 2, maxRows: 4 }"
        placeholder="输入问题，Enter 发送，Shift+Enter 换行"
        @keydown.enter.exact.prevent="ask"
      />
      <div class="chat-input-bar">
        <span class="input-hint muted">Enter 发送</span>
        <n-space align="center" :size="8">
          <n-button v-if="qa.history.length" quaternary size="small" @click="clearHistory">
            <template #icon><n-icon><TrashOutline /></n-icon></template>
            新对话
          </n-button>
          <n-button v-if="qa.streaming" secondary @click="cancel">取消</n-button>
          <n-button type="primary" :loading="qa.streaming" :disabled="!question.trim()" @click="ask">
            <template #icon><n-icon><SendOutline /></n-icon></template>
            发送
          </n-button>
        </n-space>
      </div>
    </div>
  </n-card>
</template>

<style scoped>
.qa-card {
  display: flex;
  flex-direction: column;
  height: calc(100vh - 132px);
  min-height: 480px;
}
.section-title {
  display: flex;
  align-items: center;
  gap: 8px;
}
.index-tag {
  margin-left: 4px;
}
.chat-body {
  flex: 1;
  overflow-y: auto;
  padding: 8px 4px;
  display: flex;
  flex-direction: column;
  gap: 20px;
}
.chat-empty {
  flex: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 8px;
  color: var(--text-3);
  padding: 40px 0;
}
.chat-empty-icon {
  width: 84px;
  height: 84px;
  border-radius: 24px;
  background: var(--primary-soft);
  color: var(--primary);
  display: flex;
  align-items: center;
  justify-content: center;
  margin-bottom: 8px;
}
.chat-empty-title {
  font-size: 16px;
  font-weight: 600;
  color: var(--text-1);
}
.chat-empty-sub {
  font-size: 13px;
}
.chat-suggestions {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
  justify-content: center;
  margin-top: 16px;
}
.suggestion-tag {
  cursor: pointer;
  background: var(--bg-soft);
  transition: background 0.15s ease, transform 0.15s ease;
}
.suggestion-tag:hover {
  background: var(--primary-soft);
  transform: translateY(-1px);
}
.msg-pair {
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.msg {
  display: flex;
  align-items: flex-start;
  gap: 10px;
}
.msg--user {
  flex-direction: row-reverse;
}
.msg-avatar {
  flex-shrink: 0;
  width: 32px;
  height: 32px;
  border-radius: 10px;
  display: flex;
  align-items: center;
  justify-content: center;
  margin-top: 2px;
}
.msg-avatar--user {
  background: var(--gradient-brand);
  color: #fff;
}
.msg-avatar--assistant {
  background: var(--bg-soft);
  color: var(--violet);
}
.bubble {
  width: fit-content;
  min-width: 0;
  max-width: 72%;
  padding: 12px 16px;
  border-radius: 14px;
  font-size: 14px;
  line-height: 1.6;
  word-break: break-word;
}
.bubble--user {
  background: var(--gradient-brand);
  color: #fff;
  border-top-right-radius: 4px;
}
.bubble--assistant {
  background: var(--bg-soft);
  color: var(--text-1);
  border-top-left-radius: 4px;
  border: 1px solid var(--border);
}
.answer-error {
  color: var(--error);
}
.thinking {
  display: flex;
  gap: 4px;
  padding: 6px 0;
}
.thinking-dot {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: var(--text-3);
  animation: thinking-bounce 1.2s ease-in-out infinite;
}
.thinking-dot:nth-child(2) {
  animation-delay: 0.15s;
}
.thinking-dot:nth-child(3) {
  animation-delay: 0.3s;
}
@keyframes thinking-bounce {
  0%,
  60%,
  100% {
    transform: translateY(0);
    opacity: 0.5;
  }
  30% {
    transform: translateY(-4px);
    opacity: 1;
  }
}
.sources {
  margin-top: 14px;
  padding-top: 12px;
  border-top: 1px dashed var(--border);
}
.sources-header {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 12px;
  font-weight: 600;
  color: var(--text-2);
  margin-bottom: 8px;
}
.source-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.source-item {
  display: flex;
  gap: 10px;
  padding: 10px 12px;
  border-radius: 8px;
  background: var(--bg-card);
  border: 1px solid var(--border);
}
.source-idx {
  flex-shrink: 0;
  width: 22px;
  height: 22px;
  border-radius: 50%;
  background: var(--primary-soft);
  color: var(--primary);
  font-size: 12px;
  font-weight: 600;
  display: flex;
  align-items: center;
  justify-content: center;
}
.source-info {
  min-width: 0;
}
.source-title {
  font-size: 13px;
  font-weight: 500;
  color: var(--text-1);
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
}
.source-meta {
  display: flex;
  gap: 12px;
  font-size: 12px;
  color: var(--text-3);
  margin-top: 2px;
}
.chat-input {
  margin-top: 16px;
  padding-top: 16px;
  border-top: 1px solid var(--border);
}
.chat-input-bar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-top: 10px;
}
.input-hint {
  font-size: 12px;
}

@media (max-width: 720px) {
  .bubble {
    max-width: 86%;
  }
}
</style>
