<script setup lang="ts">
import { computed, ref, onMounted } from 'vue'
import { useDialog, useMessage } from 'naive-ui'
import { useNoticesStore } from '../stores/useNoticesStore'
import { useConfigStore } from '../stores/useConfigStore'
import { useTaskPoll } from '../composables/useTaskPoll'
import { post } from '../api/http'
import { endpoints } from '../api/endpoints'
import { trackEvent, EVENT_TYPES } from '../api/events'
import type { ExtractPreviewItem, ExtractPreviewResponse, NoticeBatchFilter, NoticeDetail, NoticeSummary, TaskCreateResult } from '../api/schema'

const message = useMessage()
const dialog = useDialog()
const notices = useNoticesStore()
const configStore = useConfigStore()
const { poll, submitAndPoll } = useTaskPoll()

interface BatchTaskResult {
  deleted_notices?: number
  reset_notices?: number
}

const filterSource = ref('')
const filterType = ref('')
const filterStatus = ref('')
const filterKeyword = ref('')
const filterMatched = ref(false)
const publishedRange = ref<[number, number] | null>(null)
const crawledRange = ref<[number, number] | null>(null)
const oldDays = ref(30)
const sortBy = ref<'published' | 'crawled'>('published')

const matchMap = ref<Record<string, string[]>>({})
const detail = ref<NoticeDetail | null>(null)
const detailOpen = ref(false)
const detailLoading = ref(false)
const generating = ref<number | null>(null)
const reExtracting = ref<number | null>(null)
const loading = ref(false)
const taskRunning = ref(false)
const batchRunning = ref(false)

const crawlDialogOpen = ref(false)
const crawlSources = ref<string[]>([])
const crawlMode = ref('incremental')
const crawlMaxPages = ref<number | null>(null)
const crawlDeepCheck = ref(false)

const previewOpen = ref(false)
const previewLoading = ref(false)
const previewPassed = ref<ExtractPreviewItem[]>([])
const previewSkipped = ref<ExtractPreviewItem[]>([])
const selectedIds = ref<number[]>([])
const taskProgress = ref(0)
const taskProgressVisible = ref(false)

const sourceOptions = computed(() =>
  (configStore.sources?.sources ?? []).map((s) => ({ label: s.name, value: s.name }))
)

const FALLBACK_STATUSES = ['raw', 'extracted', 'partial', 'failed']
const FALLBACK_TYPES = ['competition', 'lecture', 'registration', 'scholarship', 'administrative', 'recruitment', 'policy', 'result', 'news', 'other']

const statusOptions = computed(() => {
  const items = notices.meta?.statuses ?? []
  const seen = new Set(items.map((s) => s.value))
  const opts = items
    .map((s) => ({ label: s.label, value: s.value }))
    .concat(FALLBACK_STATUSES.filter((v) => !seen.has(v)).map((v) => ({ label: v, value: v })))
  return [{ label: '全部状态', value: '' }, ...opts]
})

const typeOptions = computed(() => {
  const items = notices.meta?.notice_types ?? []
  const seen = new Set(items.map((t) => t.value))
  const opts = items
    .map((t) => ({ label: t.label, value: t.value }))
    .concat(FALLBACK_TYPES.filter((v) => !seen.has(v)).map((v) => ({ label: v, value: v })))
  return [{ label: '全部类型', value: '' }, ...opts]
})

const statusTagType: Record<string, 'default' | 'info' | 'success' | 'warning' | 'error'> = {
  raw: 'info',
  extracted: 'success',
  partial: 'warning',
  failed: 'error',
}

onMounted(async () => {
  notices.fetchMeta().catch(() => {})
  configStore.fetchSources().catch(() => {})
  await notices.fetchFilters().catch(() => {})
  await refresh()
})

function statusLabel(v: string): string {
  return notices.meta?.statuses?.find((s) => s.value === v)?.label ?? v
}

function typeLabel(v?: string | null): string {
  if (!v) return '未分类'
  return notices.meta?.notice_types?.find((t) => t.value === v)?.label ?? v
}

function fmtDay(d: Date): string {
  const m = `${d.getMonth() + 1}`.padStart(2, '0')
  const day = `${d.getDate()}`.padStart(2, '0')
  return `${d.getFullYear()}-${m}-${day}`
}

function rangeToParams(r: [number, number] | null): { from?: string; to?: string } {
  if (!r) return {}
  return { from: fmtDay(new Date(r[0])), to: fmtDay(new Date(r[1])) }
}

async function refresh() {
  loading.value = true
  try {
    const params: Record<string, unknown> = { page: notices.page, page_size: notices.pageSize }
    params.sort_by = sortBy.value
    if (filterSource.value) params.source = filterSource.value
    if (filterType.value) params.notice_type = filterType.value
    if (filterStatus.value) params.status = filterStatus.value
    if (filterKeyword.value) params.keyword = filterKeyword.value
    if (filterMatched.value) params.is_action = true
    const p = rangeToParams(publishedRange.value)
    if (p.from) params.published_from = p.from
    if (p.to) params.published_to = p.to
    const c = rangeToParams(crawledRange.value)
    if (c.from) params.crawled_from = c.from
    if (c.to) params.crawled_to = c.to
    await notices.fetchNotices(params)
    await loadMatchMap()
  } catch (e) {
    message.error(e instanceof Error ? e.message : String(e))
  } finally {
    loading.value = false
  }
}

function buildFilter(): NoticeBatchFilter {
  const f: NoticeBatchFilter = {}
  if (filterSource.value) f.source = filterSource.value
  if (filterType.value) f.notice_type = filterType.value
  if (filterStatus.value) f.status = filterStatus.value
  const p = rangeToParams(publishedRange.value)
  if (p.from) f.published_from = p.from
  if (p.to) f.published_to = p.to
  const c = rangeToParams(crawledRange.value)
  if (c.from) f.crawled_from = c.from
  if (c.to) f.crawled_to = c.to
  return f
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

function openCrawlDialog() {
  crawlSources.value = []
  crawlMode.value = 'incremental'
  crawlMaxPages.value = null
  crawlDeepCheck.value = false
  crawlDialogOpen.value = true
}

async function runCrawl() {
  taskRunning.value = true
  crawlDialogOpen.value = false
  taskProgressVisible.value = true
  taskProgress.value = 0
  try {
    const params: Record<string, unknown> = { mode: crawlMode.value, deep_check: crawlDeepCheck.value }
    if (crawlSources.value.length) params.sources = crawlSources.value
    if (crawlMaxPages.value) params.max_pages = crawlMaxPages.value
    const task = await submitAndPoll('crawl_all', params, (t) => {
      if (typeof t.progress === 'number') taskProgress.value = t.progress
    })
    if (task.status === 'success') {
      const summary = task.result?.summary as { new?: number; failed?: number; deep_check?: boolean } | undefined
      message.success(
        `抓取完成（新增 ${summary?.new ?? 0}，失败 ${summary?.failed ?? 0}）${summary?.deep_check ? '【深度检查】' : ''}`
      )
    } else {
      message.error(task.error || '抓取任务失败')
    }
    await refresh()
  } catch (e) {
    message.error(e instanceof Error ? e.message : String(e))
  } finally {
    taskRunning.value = false
    taskProgressVisible.value = false
  }
}

async function openExtractPreview() {
  previewLoading.value = true
  try {
    const res = await post<ExtractPreviewResponse>(endpoints.notices.extractPreview)
    previewPassed.value = res.passed ?? []
    previewSkipped.value = res.skipped ?? []
    selectedIds.value = (res.passed ?? []).map((p) => p.id)
    previewOpen.value = true
    if (!previewPassed.value.length && !previewSkipped.value.length) {
      message.info('暂无待提取的通知')
    }
  } catch (e) {
    message.error(e instanceof Error ? e.message : String(e))
  } finally {
    previewLoading.value = false
  }
}

async function runExtractSelected() {
  if (!selectedIds.value.length) {
    message.warning('请至少选择一条通知')
    return
  }
  previewOpen.value = false
  taskRunning.value = true
  taskProgressVisible.value = true
  taskProgress.value = 0
  try {
    const task = await submitAndPoll(
      'extract_batch',
      { notice_ids: selectedIds.value, auto_index: true },
      (t) => {
        if (typeof t.progress === 'number') taskProgress.value = t.progress
      }
    )
    const done = task.result?.processed as number | undefined
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
    taskProgressVisible.value = false
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
      await refresh()
    } else {
      message.error(task.error || '待办生成失败')
    }
  } catch (e) {
    message.error(e instanceof Error ? e.message : String(e))
  } finally {
    generating.value = null
  }
}

async function onReset(item: NoticeSummary) {
  dialog.warning({
    title: '重置提取结果',
    content: `确定重置「${item.title}」？将清空提取结果，状态回到「未提取」。`,
    positiveText: '重置',
    negativeText: '取消',
    onPositiveClick: async () => {
      try {
        await notices.resetNotice(item.id)
        message.success(`已重置「${item.title}」`)
        await refresh()
      } catch (e) {
        message.error(e instanceof Error ? e.message : String(e))
      }
    },
  })
}

async function onReExtract(item: NoticeSummary) {
  dialog.warning({
    title: '重新提取',
    content: `确定重新提取「${item.title}」？原提取结果将被覆盖。`,
    positiveText: '开始提取',
    negativeText: '取消',
    onPositiveClick: async () => {
      reExtracting.value = item.id
      try {
        const res = await notices.reExtractNotice(item.id)
        const task = await poll(res.task_id)
        if (task.status === 'success') {
          message.success(`「${item.title}」提取完成`)
        } else {
          message.error(task.error || '重新提取失败')
        }
        await refresh()
      } catch (e) {
        message.error(e instanceof Error ? e.message : String(e))
      } finally {
        reExtracting.value = null
      }
    },
  })
}

async function onDelete(item: NoticeSummary) {
  dialog.warning({
    title: '删除通知',
    content: `确定删除「${item.title}」？删除后不可恢复（含关联待办/提醒/订阅命中）。`,
    positiveText: '删除',
    negativeText: '取消',
    onPositiveClick: async () => {
      try {
        await notices.deleteNotice(item.id)
        message.success(`已删除「${item.title}」`)
        await refresh()
      } catch (e) {
        message.error(e instanceof Error ? e.message : String(e))
      }
    },
  })
}

async function runBatch(fn: () => Promise<{ task: { status: string; error?: string | null }; count: number }>, okMsg: (n: number) => string) {
  batchRunning.value = true
  try {
    const r = await fn()
    if (r.task.status === 'success') {
      message.success(okMsg(r.count))
    } else {
      message.error(r.task.error || '批量操作失败')
    }
    await refresh()
  } catch (e) {
    message.error(e instanceof Error ? e.message : String(e))
  } finally {
    batchRunning.value = false
  }
}

function onBatchDelete() {
  dialog.warning({
    title: '批量删除',
    content: '确定删除当前筛选条件下的所有通知？删除后不可恢复（含关联待办/提醒）。',
    positiveText: '删除',
    negativeText: '取消',
    onPositiveClick: async () => {
      await runBatch(async () => {
        const res = await notices.batchDelete(buildFilter())
        const task = await poll(res.task_id)
        const r = task.result as BatchTaskResult | null
        return { task, count: r?.deleted_notices ?? 0 }
      }, (n) => `已删除 ${n} 条通知`)
    },
  })
}

function onBatchReset() {
  dialog.warning({
    title: '批量重置',
    content: '确定重置当前筛选条件下的所有通知？将清空提取结果，状态回到「未提取」。',
    positiveText: '重置',
    negativeText: '取消',
    onPositiveClick: async () => {
      await runBatch(async () => {
        const res = await notices.batchReset({ ...buildFilter(), target_status: 'raw' })
        const task = await poll(res.task_id)
        const r = task.result as BatchTaskResult | null
        return { task, count: r?.reset_notices ?? 0 }
      }, (n) => `已重置 ${n} 条通知`)
    },
  })
}

function onDeleteOld() {
  const cut = new Date()
  cut.setDate(cut.getDate() - oldDays.value)
  const cutDay = fmtDay(cut)
  dialog.warning({
    title: '清理旧数据',
    content: `确定删除 ${oldDays.value} 天前抓取的通知（抓取时间 ≤ ${cutDay}）？删除后不可恢复。`,
    positiveText: '删除',
    negativeText: '取消',
    onPositiveClick: async () => {
      await runBatch(async () => {
        const res = await notices.batchDelete({ crawled_to: cutDay })
        const task = await poll(res.task_id)
        const r = task.result as BatchTaskResult | null
        return { task, count: r?.deleted_notices ?? 0 }
      }, (n) => `已删除 ${n} 条旧通知`)
    },
  })
}

function handlePageChange(p: number) {
  notices.page = p
  refresh()
}

function handlePageSizeChange(size: number) {
  notices.pageSize = size
  notices.page = 1
  refresh()
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
          <n-input-number v-model:value="oldDays" :min="1" :max="3650" style="width: 90px">
            <template #suffix>天</template>
          </n-input-number>
          <n-button size="small" type="error" secondary :loading="batchRunning" @click="onDeleteOld">
            清理 N 天前
          </n-button>
          <n-button size="small" type="error" secondary :loading="batchRunning" @click="onBatchDelete">
            批量删除当前筛选
          </n-button>
          <n-button size="small" type="warning" secondary :loading="batchRunning" @click="onBatchReset">
            批量重置当前筛选
          </n-button>
          <n-button size="small" type="primary" secondary :loading="taskRunning" @click="openCrawlDialog">
            抓取
          </n-button>
          <n-button size="small" type="primary" secondary :loading="taskRunning" @click="openExtractPreview">
            批量提取
          </n-button>
          <n-progress
            v-if="taskProgressVisible"
            type="line"
            :percentage="Math.round(taskProgress * 100)"
            :show-indicator="false"
            style="width: 110px"
          />
        </n-space>
      </template>

      <n-form inline @submit.prevent="refresh">
        <n-form-item label="数据源">
          <n-select
            v-model:value="filterSource"
            clearable
            placeholder="全部来源"
            :options="notices.sources.map((s) => ({ label: s, value: s }))"
            style="width: 150px"
          />
        </n-form-item>
        <n-form-item label="类型">
          <n-select v-model:value="filterType" clearable :options="typeOptions" style="width: 150px" />
        </n-form-item>
        <n-form-item label="状态">
          <n-select v-model:value="filterStatus" :options="statusOptions" style="width: 130px" />
        </n-form-item>
        <n-form-item label="关键词">
          <n-input v-model:value="filterKeyword" placeholder="标题模糊匹配" style="width: 150px" clearable />
        </n-form-item>
        <n-form-item label="发布时间">
          <n-date-picker v-model:value="publishedRange" type="daterange" clearable style="width: 250px" />
        </n-form-item>
        <n-form-item label="抓取时间">
          <n-date-picker v-model:value="crawledRange" type="daterange" clearable style="width: 250px" />
        </n-form-item>
        <n-form-item label="排序">
          <n-select
            v-model:value="sortBy"
            :options="[
              { label: '发布时间 ↓', value: 'published' },
              { label: '抓取时间 ↓', value: 'crawled' },
            ]"
            @update:value="refresh"
            style="width: 130px"
          />
        </n-form-item>
        <n-form-item>
          <n-checkbox v-model:checked="filterMatched">只看行动型</n-checkbox>
        </n-form-item>
        <n-form-item>
          <n-button type="primary" attr-type="submit" :loading="loading">查询</n-button>
        </n-form-item>
      </n-form>

      <n-spin :show="loading">
        <div v-if="notices.list.length === 0">暂无通知</div>
        <n-list v-else>
          <n-list-item v-for="item in notices.list" :key="item.id">
            <template #default>
              <n-space vertical size="small">
                <n-space align="center" size="small">
                  <n-tag size="small" :bordered="false" :type="statusTagType[item.status] ?? 'default'">
                    {{ statusLabel(item.status) }}
                  </n-tag>
                  <n-tag size="small" :bordered="false" type="info">{{ typeLabel(item.notice_type) }}</n-tag>
                  <a href="#" @click.prevent="openDetail(item)">{{ item.title }}</a>
                  <template v-for="kw in matchedKeywords(item.id)" :key="`${item.id}-${kw}`">
                    <n-tag size="small" :bordered="false" type="warning" round>订阅命中 · {{ kw }}</n-tag>
                  </template>
                  <n-tooltip v-if="item.extract_skipped_reason" trigger="hover">
                    <template #trigger>
                      <n-tag size="small" :bordered="false" type="default">已跳过提取</n-tag>
                    </template>
                    {{ item.extract_skipped_reason }}
                  </n-tooltip>
                </n-space>
                <div>
                  {{ item.source }} · {{ fmtDate(item.published_at ?? item.crawled_at) }}
                  <span v-if="item.deadline">，截止 {{ fmtDate(item.deadline) }}</span>
                </div>
              </n-space>
            </template>
            <template #suffix>
              <n-space>
                <n-button size="small" :loading="generating === item.id" @click="generateTodos(item)">
                  生成待办
                </n-button>
                <n-button size="small" :loading="reExtracting === item.id" @click="onReExtract(item)">
                  重新提取
                </n-button>
                <n-button size="small" @click="onReset(item)">重置</n-button>
                <n-button size="small" type="error" secondary @click="onDelete(item)">删除</n-button>
              </n-space>
            </template>
          </n-list-item>
        </n-list>
      </n-spin>

      <n-space justify="end" style="margin-top: 16px">
        <n-pagination
          :page="notices.page"
          :page-size="notices.pageSize"
          :item-count="notices.total"
          :page-sizes="[10, 20, 50, 100]"
          show-size-picker
          @update:page="handlePageChange"
          @update:page-size="handlePageSizeChange"
        />
      </n-space>
    </n-card>

    <n-modal
      v-model:show="crawlDialogOpen"
      preset="card"
      title="抓取通知"
      style="width: 520px"
      :bordered="false"
    >
      <n-form label-placement="left" label-width="110">
        <n-form-item label="数据源">
          <n-select
            v-model:value="crawlSources"
            multiple
            clearable
            :options="sourceOptions"
            placeholder="全部启用来源"
            style="width: 100%"
          />
          <template #feedback>不选 = 抓取全部启用来源；停用来源始终跳过</template>
        </n-form-item>
        <n-form-item label="模式">
          <n-select
            v-model:value="crawlMode"
            :options="[
              { label: '增量抓取（推荐）', value: 'incremental' },
              { label: '全量抓取（重抓全部详情）', value: 'full' },
              { label: '仅列表', value: 'list_only' },
            ]"
            style="width: 260px"
          />
        </n-form-item>
        <n-form-item label="最大页数">
          <n-input-number v-model:value="crawlMaxPages" :min="1" clearable placeholder="默认" style="width: 120px" />
        </n-form-item>
        <n-form-item label="深度检查">
          <n-switch v-model:value="crawlDeepCheck" />
          <span style="margin-left: 8px; color: #999; font-size: 12px">重抓已入库详情页比对内容变更</span>
        </n-form-item>
      </n-form>
      <template #footer>
        <n-space justify="end">
          <n-button @click="crawlDialogOpen = false">取消</n-button>
          <n-button type="primary" :loading="taskRunning" @click="runCrawl">开始抓取</n-button>
        </n-space>
      </template>
    </n-modal>

    <n-modal
      v-model:show="previewOpen"
      preset="card"
      title="提取预览"
      style="width: 680px"
      :bordered="false"
    >
      <n-spin :show="previewLoading">
        <n-space vertical size="large">
          <div v-if="previewPassed.length">
            <div style="margin-bottom: 8px">将提取 {{ previewPassed.length }} 条（可取消勾选）：</div>
            <n-scrollbar style="max-height: 240px">
              <n-checkbox-group v-model:value="selectedIds">
                <n-space vertical size="small">
                  <n-checkbox v-for="p in previewPassed" :key="p.id" :value="p.id">
                    {{ p.title }}
                    <span style="color: #999">（{{ p.source }} · {{ fmtDate(p.published_at) }}）</span>
                  </n-checkbox>
                </n-space>
              </n-checkbox-group>
            </n-scrollbar>
          </div>
          <div v-if="previewSkipped.length">
            <div style="margin-bottom: 8px">预筛跳过 {{ previewSkipped.length }} 条（不调 LLM）：</div>
            <n-scrollbar style="max-height: 200px">
              <n-list size="small">
                <n-list-item v-for="s in previewSkipped" :key="s.id">
                  <n-space align="center" size="small">
                    <n-tag size="small" :bordered="false" type="default">跳过</n-tag>
                    <span style="color: #999">{{ s.title }}</span>
                    <n-tooltip trigger="hover">
                      <template #trigger>
                        <n-text depth="3" style="cursor: help">原因</n-text>
                      </template>
                      {{ s.reason }}
                    </n-tooltip>
                  </n-space>
                </n-list-item>
              </n-list>
            </n-scrollbar>
          </div>
          <div v-if="!previewPassed.length && !previewSkipped.length">暂无待提取的通知</div>
        </n-space>
      </n-spin>
      <template #footer>
        <n-space justify="end">
          <n-button @click="previewOpen = false">取消</n-button>
          <n-button
            type="primary"
            :disabled="!selectedIds.length"
            :loading="taskRunning"
            @click="runExtractSelected"
          >
            提取选中 {{ selectedIds.length }} 条
          </n-button>
        </n-space>
      </template>
    </n-modal>

    <n-drawer v-model:show="detailOpen" :width="560">
      <n-drawer-content v-if="detail" :title="detail.title" closable>
        <n-space vertical size="large">
          <n-descriptions :column="1" label-placement="left" size="small">
            <n-descriptions-item label="来源">{{ detail.source }}</n-descriptions-item>
            <n-descriptions-item label="发布时间">{{ fmtDate(detail.published_at) }}</n-descriptions-item>
            <n-descriptions-item label="抓取时间">{{ fmtDate(detail.crawled_at) }}</n-descriptions-item>
            <n-descriptions-item label="类型">{{ typeLabel(detail.notice_type) }}</n-descriptions-item>
            <n-descriptions-item label="状态">{{ statusLabel(detail.status) }}</n-descriptions-item>
            <n-descriptions-item v-if="detail.extract_skipped_reason" label="跳过提取原因">{{ detail.extract_skipped_reason }}</n-descriptions-item>
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