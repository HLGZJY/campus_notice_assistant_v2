<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import {
  NBadge,
  NButton,
  NEmpty,
  NIcon,
  NProgress,
  NSpace,
  NTag,
  NText,
} from 'naive-ui'
import { RefreshOutline, TrashOutline } from '@vicons/ionicons5'
import { useTaskStore } from '../stores/useTaskStore'
import type { TaskView } from '../api/schema'

const store = useTaskStore()

const TASK_TYPE_LABELS: Record<string, string> = {
  crawl_source: '单源抓取',
  crawl_all: '全量抓取',
  extract_batch: '批量提取',
  subscription_add: '新增订阅',
  subscription_update: '更新订阅',
  match_all: '全库重匹配',
  rebuild_index: '重建索引',
  generate_todos: '生成待办',
  re_extract_notice: '重新提取',
  batch_delete: '批量删除',
  batch_reset: '批量重置',
}

function typeLabel(type: string): string {
  return TASK_TYPE_LABELS[type] || type
}

function statusTagType(status: string): 'default' | 'info' | 'success' | 'error' | 'warning' {
  switch (status) {
    case 'queued':
      return 'default'
    case 'running':
      return 'info'
    case 'success':
      return 'success'
    case 'failed':
      return 'error'
    default:
      return 'default'
  }
}

function statusLabel(status: string): string {
  switch (status) {
    case 'queued':
      return '排队'
    case 'running':
      return '运行'
    case 'success':
      return '完成'
    case 'failed':
      return '失败'
    default:
      return status
  }
}

// ---------- 计时器（组件常驻，始终 tick） ----------

const now = ref(Date.now())
let timer: ReturnType<typeof setInterval> | undefined

onMounted(() => {
  timer = setInterval(() => {
    now.value = Date.now()
  }, 1000)
})

onUnmounted(() => {
  if (timer) clearInterval(timer)
})

function formatElapsed(task: TaskView): string {
  const created = new Date(task.created_at).getTime()
  if (isNaN(created)) return ''
  const isTerminal = task.status === 'success' || task.status === 'failed'
  const end = isTerminal ? new Date(task.updated_at).getTime() : now.value
  if (isNaN(end)) return ''
  const secs = Math.max(0, Math.floor((end - created) / 1000))
  const h = Math.floor(secs / 3600)
  const m = Math.floor((secs % 3600) / 60)
  const s = secs % 60
  if (h > 0) return `${h}:${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`
  return `${m}:${String(s).padStart(2, '0')}`
}

// ---------- 参数摘要 ----------

function paramsSummary(task: TaskView): string {
  const p = task.params || {}
  switch (task.type) {
    case 'generate_todos':
    case 're_extract_notice':
      return p.notice_id != null ? `#${p.notice_id}` : ''
    case 'crawl_source':
      return p.source_name ? p.source_name : ''
    case 'crawl_all': {
      const sources = p.sources as string[] | undefined
      return sources && sources.length ? sources.join(', ') : '全部'
    }
    case 'extract_batch': {
      const ids = p.notice_ids as number[] | undefined
      return ids && ids.length ? `${ids.length}篇` : '全量'
    }
    case 'subscription_add':
      return p.keyword ? String(p.keyword) : ''
    case 'subscription_update':
      return p.subscription_id != null ? `#${p.subscription_id}` : ''
    default:
      return ''
  }
}

// ---------- 进度 ----------

function progressPercent(task: TaskView): number {
  if (task.status === 'success') return 100
  if (task.status === 'failed') return 100
  return Math.round((task.progress || 0) * 100)
}

function progressStatus(task: TaskView): 'success' | 'error' | 'warning' | undefined {
  if (task.status === 'success') return 'success'
  if (task.status === 'failed') return 'error'
  return undefined
}

// ---------- token ----------

function tokenSummary(task: TaskView): string {
  const u = task.token_usage
  if (!u) return ''
  return `↑${u.input_tokens} ↓${u.output_tokens} ×${u.calls}`
}

const sortedTasks = computed(() =>
  [...store.tasks].sort((a, b) => b.id - a.id),
)
</script>

<template>
  <div class="task-panel">
    <div class="task-panel-header">
      <span class="task-panel-title">
        任务列表
        <n-badge
          v-if="store.runningCount > 0"
          :value="store.runningCount"
          :max="99"
          type="info"
          :offset="[4, -2]"
        />
      </span>
      <n-space :size="2">
        <n-button quaternary circle size="tiny" title="刷新" @click="store.fetchList()">
          <template #icon>
            <n-icon size="14"><RefreshOutline /></n-icon>
          </template>
        </n-button>
        <n-button quaternary circle size="tiny" title="清理已完成" @click="store.clearFinished()">
          <template #icon>
            <n-icon size="14"><TrashOutline /></n-icon>
          </template>
        </n-button>
      </n-space>
    </div>

    <div class="task-panel-body">
      <n-empty v-if="sortedTasks.length === 0" description="暂无任务" size="small" />
      <div v-else class="task-list">
        <div v-for="task in sortedTasks" :key="task.id" class="task-item">
          <div class="task-item-top">
            <span class="task-type">{{ typeLabel(task.type) }}</span>
            <n-tag :type="statusTagType(task.status)" size="tiny" round>
              {{ statusLabel(task.status) }}
            </n-tag>
          </div>
          <div class="task-item-meta">
            <span>#{{ task.id }}</span>
            <span v-if="paramsSummary(task)" class="task-params">{{ paramsSummary(task) }}</span>
          </div>
          <n-progress
            v-if="task.status !== 'failed'"
            :percentage="progressPercent(task)"
            :status="progressStatus(task)"
            :show-indicator="false"
            :height="4"
            :border-radius="2"
          />
          <div class="task-item-bottom">
            <span class="task-elapsed">{{ formatElapsed(task) }}</span>
            <span v-if="tokenSummary(task)" class="task-token">{{ tokenSummary(task) }}</span>
          </div>
          <div v-if="task.status === 'failed' && task.error" class="task-error">
            <n-text type="error" depth="secondary">{{ task.error }}</n-text>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.task-panel {
  border-top: 1px solid var(--border, rgba(255, 255, 255, 0.09));
  display: flex;
  flex-direction: column;
  max-height: 50vh;
  flex-shrink: 0;
}
.task-panel-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 10px 16px 6px;
}
.task-panel-title {
  font-size: 12px;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.5px;
  color: var(--text-3, #999);
}
.task-panel-body {
  overflow-y: auto;
  padding: 0 12px 10px;
  flex: 1;
  min-height: 0;
}
.task-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.task-item {
  padding: 8px 10px;
  border-radius: 6px;
  background: var(--card-color, rgba(255, 255, 255, 0.03));
  border: 1px solid var(--divider-color, rgba(255, 255, 255, 0.06));
}
.task-item-top {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 6px;
}
.task-type {
  font-size: 13px;
  font-weight: 600;
  color: var(--text-1, #fff);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  flex: 1;
  min-width: 0;
}
.task-item-meta {
  font-size: 11px;
  color: var(--text-3, #888);
  display: flex;
  gap: 6px;
  margin-top: 3px;
}
.task-params {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  flex: 1;
  min-width: 0;
}
.task-item-bottom {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 6px;
  margin-top: 5px;
  font-size: 13px;
  color: var(--text-3, #888);
}
.task-elapsed {
  font-variant-numeric: tabular-nums;
  font-weight: 500;
  font-size: 13px;
  color: var(--text-2, #ccc);
}
.task-token {
  color: var(--text-2, #aaa);
  font-variant-numeric: tabular-nums;
  font-size: 13px;
}
.task-error {
  font-size: 11px;
  margin-top: 3px;
  word-break: break-all;
  overflow: hidden;
  text-overflow: ellipsis;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
}
</style>
