<script setup lang="ts">
import { nextTick, ref, reactive, watch, computed, onMounted, onUnmounted } from 'vue'
import { ChatbubbleEllipsesOutline, LayersOutline, SendOutline, SparklesOutline, TrashOutline } from '@vicons/ionicons5'
import { useQaStore, type QaMessage } from '../stores/useQaStore'
import MarkdownView from '../components/MarkdownView.vue'
import { trackEvent, EVENT_TYPES } from '../api/events'
import type { IndexStatsView } from '../api/schema'

const qa = useQaStore()
const question = ref('')
const indexStats = ref<IndexStatsView | null>(null)
const chatBody = ref<HTMLElement | null>(null)
const historyOpen = ref(false)
const detailOpen = ref(false)
const detailMsg = ref<QaMessage | null>(null)

const suggestions = ['最近有哪些奖学金通知？', '有哪些竞赛可以报名？', '帮我总结近一周的关键通知']

const STAGE_LABELS: Record<string, string> = {
  retrieval: '检索中',
  thinking: '思考中',
  generating: '生成回复中',
  cache_hit: '命中缓存',
  '': '处理中',
}
function stageLabel(stage: string) {
  return STAGE_LABELS[stage] ?? '处理中'
}

const now = ref(Date.now())
let timer: ReturnType<typeof setInterval> | undefined
onMounted(async () => {
  timer = setInterval(() => { now.value = Date.now() }, 1000)
  indexStats.value = await qa.fetchIndexStats().catch(() => null)
  qa.loadHistory()
})
onUnmounted(() => { if (timer) clearInterval(timer) })

const stageElapsed = computed(() => {
  if (!qa.currentStageStartedAt) return ''
  const secs = Math.max(0, Math.floor((now.value - qa.currentStageStartedAt) / 1000))
  if (secs < 60) return `${secs}s`
  const m = Math.floor(secs / 60)
  const s = secs % 60
  return `${m}:${String(s).padStart(2, '0')}`
})

async function scrollToBottom() {
  await nextTick()
  if (chatBody.value) chatBody.value.scrollTop = chatBody.value.scrollHeight
}

watch(
  () => qa.messages.map((m) => m.answer.length),
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
  qa.messages.push(msg)
  question.value = ''
  trackEvent(EVENT_TYPES.QA_ASK, undefined, q)
  try {
    await qa.askStream(
      { question: q },
      (evt) => {
        if (evt.type === 'status') {
          msg.currentStage = evt.stage
          msg.currentStageStartedAt = Date.now()
          if (evt.stage === 'cache_hit') {
            msg.cached = true
          }
        } else if (evt.type === 'delta') {
          msg.answer += evt.content
        } else if (evt.type === 'done') {
          msg.answer = evt.answer
          msg.sources = evt.sources
          msg.retrievedChunks = evt.retrieved_chunks
          msg.currentStage = undefined
          delete msg.error
          qa.refreshHistory()
        } else if (evt.type === 'error') {
          msg.error = evt.message
        }
      },
    )
  } catch (e) {
    if ((e as { name?: string })?.name !== 'AbortError') {
      msg.error = e instanceof Error ? e.message : String(e)
    }
  }
}

function cancel() {
  qa.cancel()
}

function newConversation() {
  qa.startNewConversation()
}

function openDetail(msg: QaMessage) {
  detailMsg.value = msg
  detailOpen.value = true
}
</script>

<template>
  <n-card :bordered="false" class="qa-card">
    <template #header>
      <div class="section-title">
        <div class="title-left">
          <n-icon size="18" color="var(--violet)"><ChatbubbleEllipsesOutline /></n-icon>
          <span class="title-text">智能问答</span>
          <n-tag :bordered="false" round :type="indexStats?.error ? 'error' : 'info'" size="small" class="index-tag">
            <template #icon>
              <n-icon><LayersOutline /></n-icon>
            </template>
            {{ indexStats?.chunks ?? 0 }} chunks
            <template v-if="indexStats?.persist_dir"> · {{ indexStats.persist_dir }}</template>
            <template v-if="indexStats?.error"> · 索引异常</template>
          </n-tag>
        </div>
        <n-button quaternary size="small" class="history-btn" @click="historyOpen = true">
          <template #icon><n-icon><LayersOutline /></n-icon></template>
          历史 ({{ qa.historyItems.length }})
        </n-button>
      </div>
    </template>

    <div ref="chatBody" class="chat-body">
      <div v-if="qa.messages.length === 0" class="chat-empty">
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
        <div v-for="msg in qa.messages" :key="msg.id" class="msg-pair" :id="`qa-msg-${msg.id}`">
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
              <div v-if="msg.cached" class="cache-badge">命中缓存</div>
              <template v-if="msg.answer">
                <MarkdownView :content="msg.answer" />
              </template>
              <div v-else-if="msg.error" class="answer-error">[错误] {{ msg.error }}</div>
              <div v-else class="stage-indicator">
                <span class="stage-dot"></span>
                <span class="stage-text">{{ stageLabel(msg.currentStage ?? qa.currentStage) }}</span>
                <span v-if="stageElapsed" class="stage-elapsed">{{ stageElapsed }}</span>
                <span v-if="msg.cached" class="stage-cached">缓存</span>
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
          <n-button v-if="qa.messages.length" quaternary size="small" @click="newConversation">
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

    <n-drawer v-model:show="historyOpen" :width="420" placement="left">
      <n-drawer-content title="问答历史" closable>
        <template #header-extra>
          <n-button quaternary size="small" @click="qa.clearAllHistory">
            <template #icon><n-icon><TrashOutline /></n-icon></template>
            清空
          </n-button>
        </template>
        <n-empty v-if="qa.historyItems.length === 0" description="暂无历史记录" />
        <div v-else class="history-list">
          <div
            v-for="msg in qa.historyItems"
            :key="msg.id"
            class="history-item"
            @click="openDetail(msg)"
          >
            <div class="history-q">{{ msg.question }}</div>
            <div class="history-meta">
              <n-tag v-if="msg.cached" size="small" type="warning" :bordered="false">缓存</n-tag>
              <span class="muted">{{ msg.answer.slice(0, 60) }}{{ msg.answer.length > 60 ? '...' : '' }}</span>
            </div>
            <n-button
              class="history-del"
              quaternary
              size="tiny"
              @click.stop="qa.removeHistory(msg.id)"
            >
              <template #icon><n-icon><TrashOutline /></n-icon></template>
            </n-button>
          </div>
        </div>
      </n-drawer-content>
    </n-drawer>

    <n-drawer v-model:show="detailOpen" :width="640" placement="right">
      <n-drawer-content title="历史详情" closable>
        <template v-if="detailMsg">
          <div class="detail-block">
            <div class="detail-label">问题</div>
            <div class="detail-question">{{ detailMsg.question }}</div>
          </div>
          <div class="detail-block">
            <div class="detail-label">
              回答
              <n-tag v-if="detailMsg.cached" size="small" type="warning" :bordered="false">命中缓存</n-tag>
            </div>
            <div class="detail-answer">
              <MarkdownView :content="detailMsg.answer" />
            </div>
          </div>
          <div v-if="detailMsg.sources.length" class="detail-block">
            <div class="detail-label">引用来源（{{ detailMsg.retrievedChunks }} chunks）</div>
            <div class="source-list">
              <div v-for="(s, i) in detailMsg.sources" :key="i" class="source-item">
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
        </template>
      </n-drawer-content>
    </n-drawer>
  </n-card>
</template>

<style scoped>
.qa-card {
  display: flex;
  flex-direction: column;
  height: calc(100vh - 132px);
  min-height: 480px;
}
.qa-card :deep(.n-card-header) {
  flex-shrink: 0;
}
.qa-card :deep(.n-card-content) {
  flex: 1;
  min-height: 0;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}
.section-title {
  display: flex;
  align-items: center;
  gap: 8px;
  width: 100%;
}
.title-left {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;        /* 允许整个左侧标题区收缩 */
  flex: 1;             /* 占满剩余空间，空间不足时优先收缩 */
}
.title-text {
  flex-shrink: 0;      /* 标题文字保持完整 */
  white-space: nowrap;
}
.index-tag {
  margin-left: 4px;
  min-width: 0;        /* 允许 tag 收缩 */
  flex-shrink: 1;      /* 空间不足时让位给历史按钮和标题 */
  overflow: hidden;
  white-space: nowrap;
  text-overflow: ellipsis;
}
.index-tag :deep(.n-tag__content) {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.chat-body {
  flex: 1;
  min-height: 0;
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
.cache-badge {
  display: inline-block;
  padding: 2px 10px;
  margin-bottom: 8px;
  border-radius: 999px;
  background: var(--warning-soft, rgba(240, 160, 32, 0.12));
  color: var(--warning);
  font-size: 12px;
  font-weight: 600;
}
.stage-indicator {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 6px 0;
  font-size: 13px;
  color: var(--text-2);
}
.stage-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: var(--primary);
  animation: thinking-bounce 1.2s ease-in-out infinite;
}
.stage-text {
  font-weight: 500;
}
.stage-elapsed {
  font-size: 12px;
  color: var(--text-3);
  font-variant-numeric: tabular-nums;
}
.stage-cached {
  font-size: 12px;
  color: var(--warning);
  font-weight: 600;
}
.history-btn {
  margin-left: auto;   /* 顶到右侧 */
  flex-shrink: 0;      /* 保持原有宽度，不被挤压 */
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
  flex-shrink: 0;
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
.history-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.history-item {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 10px 12px;
  border-radius: 8px;
  background: var(--bg-card);
  border: 1px solid var(--border);
  cursor: pointer;
  transition: background 0.15s ease, border-color 0.15s ease;
}
.history-item:hover {
  background: var(--primary-soft);
  border-color: var(--primary-border, var(--border));
}
.history-q {
  flex: 1;
  min-width: 0;
  font-size: 13px;
  font-weight: 500;
  color: var(--text-1);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.history-meta {
  flex: 2;
  min-width: 0;
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 12px;
  color: var(--text-3);
}
.history-meta .muted {
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.history-del {
  flex-shrink: 0;
}
.detail-block {
  margin-bottom: 16px;
}
.detail-label {
  font-size: 12px;
  font-weight: 600;
  color: var(--text-3);
  text-transform: uppercase;
  letter-spacing: 0.04em;
  margin-bottom: 8px;
  display: flex;
  align-items: center;
  gap: 8px;
}
.detail-question {
  font-size: 15px;
  font-weight: 600;
  color: var(--text-1);
  padding: 12px 14px;
  border-radius: 8px;
  background: var(--bg-soft);
  border: 1px solid var(--border);
}
.detail-answer {
  padding: 12px 14px;
  border-radius: 8px;
  background: var(--bg-soft);
  border: 1px solid var(--border);
}

</style>
