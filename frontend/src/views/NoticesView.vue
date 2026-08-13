<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { useMessage } from 'naive-ui'
import { useNoticesStore } from '../stores/useNoticesStore'
import { useTaskPoll } from '../composables/useTaskPoll'
import { post } from '../api/http'
import { endpoints } from '../api/endpoints'
import { trackEvent, EVENT_TYPES } from '../api/events'
import type { NoticeDetail, NoticeSummary, TaskCreateResult } from '../api/schema'

const message = useMessage()
const notices = useNoticesStore()
const { poll, submitAndPoll } = useTaskPoll()

const filterSource = ref('')
const filterType = ref('')
const filterStatus = ref('')
const filterKeyword = ref('')
const filterMatched = ref(false)
const limit = ref(200)

const matchMap = ref<Record<string, string[]>>({})
const detail = ref<NoticeDetail | null>(null)
const detailOpen = ref(false)
const detailLoading = ref(false)
const generating = ref<number | null>(null)
const loading = ref(false)
const taskRunning = ref(false)

const statusOptions = [
  { label: '全部状态', value: '' },
  { label: '未提取 raw', value: 'raw' },
  { label: '已提取 extracted', value: 'extracted' },
  { label: '部分提取 partial', value: 'partial' },
  { label: '提取失败 failed', value: 'failed' },
]

const statusTagType: Record<string, 'default' | 'info' | 'success' | 'warning' | 'error'> = {
  raw: 'info',
  extracted: 'success',
  partial: 'warning',
  failed: 'error',
}

onMounted(async () => {
  await notices.fetchFilters().catch(() => {})
  await refresh()
})

async function refresh() {
  loading.value = true
  try {
    const params: Record<string, unknown> = { limit: limit.value }
    if (filterSource.value) params.source = filterSource.value
    if (filterType.value) params.notice_type = filterType.value
    if (filterStatus.value) params.status = filterStatus.value
    if (filterKeyword.value) params.keyword = filterKeyword.value
    if (filterMatched.value) params.is_action = true
    await notices.fetchNotices(params)
    await loadMatchMap()
  } catch (e) {
    message.error(e instanceof Error ? e.message : String(e))
  } finally {
    loading.value = false
  }
}

async function loadMatchMap() {
  const ids = notices.list.map((n) => n.id)
  if (!ids.length) {
    matchMap.value = {}
    return
  }
  matchMap.value = await notices.fetchMatchMap(ids).catch(() => ({}))
}

function matchedKeywords(id: number): string[] {
  return matchMap.value[String(id)] ?? []
}

async function crawlAll() {
  taskRunning.value = true
  try {
    const task = await submitAndPoll('crawl_all', {})
    if (task.status === 'success') {
      const summary = task.result?.summary as { new?: number; failed?: number } | undefined
      message.success(`全库抓取完成（新增 ${summary?.new ?? 0}，失败 ${summary?.failed ?? 0}）`)
    } else {
      message.error(task.error || '抓取任务失败')
    }
    await refresh()
  } catch (e) {
    message.error(e instanceof Error ? e.message : String(e))
  } finally {
    taskRunning.value = false
  }
}

async function extractBatch() {
  taskRunning.value = true
  try {
    const task = await submitAndPoll('extract_batch', { limit: 50, auto_index: true })
    const done = task.result?.done as number | undefined
    if (task.status === 'success') {
      message.success(`批量提取完成（处理 ${done ?? 0} 条）`)
    } else {
      message.error(task.error || '批量提取失败')
    }
    await refresh()
  } catch (e) {
    message.error(e instanceof Error ? e.message : String(e))
  } finally {
    taskRunning.value = false
  }
}

async function openDetail(item: NoticeSummary) {
  detailOpen.value = true
  detailLoading.value = true
  detail.value = null
  try {
    detail.value = await notices.fetchDetail(item.id)
  } catch (e) {
    message.error(e instanceof Error ? e.message : String(e))
  } finally {
    detailLoading.value = false
  }
}

async function generateTodos(item: NoticeSummary) {
  generating.value = item.id
  try {
    trackEvent(EVENT_TYPES.TODO_GENERATE, item.id, item.title)
    const res = await post<TaskCreateResult>(endpoints.notices.todos(item.id))
    const task = await poll(res.task_id)
    if (task.status === 'success') {
      message.success(`「${item.title}」待办已生成`)
      await notices.fetchNotices({ limit: limit.value })
    } else {
      message.error(task.error || '待办生成失败')
    }
  } catch (e) {
    message.error(e instanceof Error ? e.message : String(e))
  } finally {
    generating.value = null
  }
}

function fmtDate(iso?: string | null): string {
  if (!iso) return '—'
  return iso.replace('T', ' ').slice(0, 16)
}

function keyDatesText(d: NoticeDetail): string {
  if (!d.key_dates || !d.key_dates.length) return ''
  return d.key_dates
    .map((k) => {
      const label = (k as { label?: string })?.label ?? (k as { date?: string })?.date ?? ''
      const date = (k as { date?: string })?.date ?? ''
      return date ? `${label} ${date}` : String(k)
    })
    .join('；')
}
</script>

<template>
  <n-space vertical size="large">
    <n-card title="通知列表">
      <template #header-extra>
        <n-space>
          <n-button size="small" type="primary" secondary :loading="taskRunning" @click="crawlAll">
            抓取全部
          </n-button>
          <n-button size="small" type="primary" secondary :loading="taskRunning" @click="extractBatch">
            批量提取
          </n-button>
        </n-space>
      </template>

      <n-form inline @submit.prevent="refresh">
        <n-form-item label="数据源">
          <n-select
            v-model:value="filterSource"
            clearable
            placeholder="全部来源"
            :options="notices.sources.map((s) => ({ label: s, value: s }))"
            style="width: 160px"
          />
        </n-form-item>
        <n-form-item label="类型">
          <n-select
            v-model:value="filterType"
            clearable
            placeholder="全部类型"
            :options="notices.types.map((t) => ({ label: t, value: t }))"
            style="width: 160px"
          />
        </n-form-item>
        <n-form-item label="状态">
          <n-select v-model:value="filterStatus" :options="statusOptions" style="width: 160px" />
        </n-form-item>
        <n-form-item label="关键词">
          <n-input v-model:value="filterKeyword" placeholder="标题模糊匹配" style="width: 160px" clearable />
        </n-form-item>
        <n-form-item>
          <n-checkbox v-model:checked="filterMatched">只看行动型</n-checkbox>
        </n-form-item>
        <n-form-item>
          <n-input-number v-model:value="limit" :min="1" :max="2000" style="width: 110px" />
        </n-form-item>
        <n-form-item>
          <n-button type="primary" attr-type="submit" :loading="loading">查询</n-button>
        </n-form-item>
      </n-form>

      <n-spin :show="loading">
        <div v-if="notices.list.length === 0">暂无通知</div>
        <n-list v-else>
          <n-list-item v-for="item in notices.list" :key="item.id">
            <template #title>
              <n-space align="center" size="small">
                <n-tag size="small" :bordered="false" :type="statusTagType[item.status] ?? 'default'">
                  {{ item.status }}
                </n-tag>
                <n-tag size="small" :bordered="false" type="info">{{ item.notice_type || '未分类' }}</n-tag>
                <a href="#" @click.prevent="openDetail(item)">{{ item.title }}</a>
                <template v-for="kw in matchedKeywords(item.id)" :key="`${item.id}-${kw}`">
                  <n-tag size="small" :bordered="false" type="warning" round>订阅命中 · {{ kw }}</n-tag>
                </template>
              </n-space>
            </template>
            <template #desc>
              {{ item.source }} · {{ fmtDate(item.published_at ?? item.crawled_at) }}
              <span v-if="item.deadline">，截止 {{ fmtDate(item.deadline) }}</span>
            </template>
            <template #extra>
              <n-button size="small" :loading="generating === item.id" @click="generateTodos(item)">
                生成待办
              </n-button>
            </template>
          </n-list-item>
        </n-list>
      </n-spin>
    </n-card>

    <n-drawer v-model:show="detailOpen" :width="560">
      <n-drawer-content v-if="detail" :title="detail.title" closable>
        <n-space vertical size="large">
          <n-descriptions :column="1" label-placement="left" size="small">
            <n-descriptions-item label="来源">{{ detail.source }}</n-descriptions-item>
            <n-descriptions-item label="发布时间">{{ fmtDate(detail.published_at) }}</n-descriptions-item>
            <n-descriptions-item label="抓取时间">{{ fmtDate(detail.crawled_at) }}</n-descriptions-item>
            <n-descriptions-item label="类型">{{ detail.notice_type || '—' }}</n-descriptions-item>
            <n-descriptions-item label="状态">{{ detail.status }}</n-descriptions-item>
            <n-descriptions-item label="目标受众">{{ detail.target_audience || '—' }}</n-descriptions-item>
            <n-descriptions-item label="报名方式">{{ detail.signup_method || '—' }}</n-descriptions-item>
            <n-descriptions-item v-if="detail.signup_url" label="报名链接">
              <n-a :href="detail.signup_url" target="_blank" rel="noopener">{{ detail.signup_url }}</n-a>
            </n-descriptions-item>
            <n-descriptions-item label="地点">{{ detail.location || '—' }}</n-descriptions-item>
            <n-descriptions-item label="截止时间">{{ fmtDate(detail.deadline) }}</n-descriptions-item>
            <n-descriptions-item v-if="keyDatesText(detail)" label="关键日期">{{ keyDatesText(detail) }}</n-descriptions-item>
            <n-descriptions-item label="原文链接">
              <n-a :href="detail.url" target="_blank" rel="noopener">{{ detail.url }}</n-a>
            </n-descriptions-item>
          </n-descriptions>
          <n-card title="摘要" v-if="detail.summary">
            <div>{{ detail.summary }}</div>
          </n-card>
          <n-card title="正文">
            <pre style="white-space: pre-wrap; word-break: break-word">{{ detail.raw_content || '（无正文）' }}</pre>
          </n-card>
        </n-space>
      </n-drawer-content>
      <n-drawer-content v-else>
        <n-spin :show="detailLoading" />
      </n-drawer-content>
    </n-drawer>
  </n-space>
</template>