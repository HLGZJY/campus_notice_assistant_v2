<script setup lang="ts">
import { ref, onMounted, computed } from 'vue'
import { useDialog, useMessage } from 'naive-ui'
import { useSubscriptionsStore } from '../stores/useSubscriptionsStore'
import { useNoticesStore } from '../stores/useNoticesStore'
import { useTaskPoll } from '../composables/useTaskPoll'
import type {
  NoticeDetail,
  NoticeSummary,
  SubscriptionItem,
  SubscriptionPreview,
} from '../api/schema'

const message = useMessage()
const dialog = useDialog()
const subs = useSubscriptionsStore()
const notices = useNoticesStore()
const { poll } = useTaskPoll()

const showModal = ref(false)
const editingId = ref<number | null>(null)
const keyword = ref('')
const noticeType = ref<string | null>(null)
const enabled = ref(true)
const preview = ref<SubscriptionPreview | null>(null)
const previewLoading = ref(false)
const submitting = ref(false)
const matchingAll = ref(false)

// 内联展开的命中明细（按订阅缓存分页结果）
interface MatchedState {
  items: NoticeSummary[]
  total: number
  page: number
  pageSize: number
}
const expandedId = ref<number | null>(null)
const matchedCache = ref<Record<number, MatchedState>>({})
const matchedLoading = ref<Record<number, boolean>>({})
const matchedError = ref<Record<number, string>>({})

// 命中通知详情抽屉
const detailOpen = ref(false)
const detailLoading = ref(false)
const detail = ref<NoticeDetail | null>(null)

const isEditing = computed(() => editingId.value !== null)

onMounted(async () => {
  await Promise.all([
    subs.fetchList().catch(() => {}),
    subs.fetchStats().catch(() => {}),
    notices.fetchMeta().catch(() => {}),
    notices.fetchFilters().catch(() => {}),
  ])
})

function typeOptions() {
  return notices.types.map((t) => ({ label: t, value: t }))
}

function typeLabel(v?: string | null): string {
  if (!v) return '未分类'
  return notices.meta?.notice_types?.find((t) => t.value === v)?.label ?? v
}

function fmtDate(iso?: string | null): string {
  if (!iso) return '—'
  return iso.replace('T', ' ').slice(0, 16)
}

function openCreate() {
  editingId.value = null
  keyword.value = ''
  noticeType.value = null
  enabled.value = true
  preview.value = null
  showModal.value = true
}

function openEdit(s: SubscriptionItem) {
  editingId.value = s.id
  keyword.value = s.keyword
  noticeType.value = s.notice_type ?? null
  enabled.value = s.enabled === 1
  preview.value = null
  showModal.value = true
}

async function runPreview() {
  if (!keyword.value.trim()) {
    message.warning('请填写关键词')
    return
  }
  previewLoading.value = true
  try {
    preview.value = await subs.preview({
      keyword: keyword.value.trim(),
      notice_type: noticeType.value,
      enabled: enabled.value,
      sample_limit: 5,
    })
  } catch (e) {
    message.error(e instanceof Error ? e.message : String(e))
  } finally {
    previewLoading.value = false
  }
}

async function confirm() {
  if (!keyword.value.trim()) {
    message.warning('请填写关键词')
    return
  }
  submitting.value = true
  try {
    let result
    if (isEditing.value) {
      result = await subs.update(editingId.value as number, {
        keyword: keyword.value.trim(),
        notice_type: noticeType.value,
        enabled: enabled.value,
      })
    } else {
      result = await subs.create({ keyword: keyword.value.trim(), notice_type: noticeType.value, enabled: enabled.value })
    }
    const task = await poll(result.task_id)
    if (task.status === 'success') {
      const backfill = (task.result as Record<string, unknown> | null | undefined)?.backfill as
        | { matched_notices?: number }
        | undefined
      const n = backfill?.matched_notices
      if (isEditing.value) {
        message.success(n != null ? `订阅已更新，命中 ${n} 条通知` : '订阅已更新')
      } else {
        message.success(n != null ? `订阅已创建，命中 ${n} 条通知` : '订阅已创建')
      }
      await subs.fetchList()
      await subs.fetchStats()
      if (editingId.value != null) {
        delete matchedCache.value[editingId.value]
      }
      showModal.value = false
    } else {
      message.error(task.error || '操作失败')
    }
  } catch (e) {
    message.error(e instanceof Error ? e.message : String(e))
  } finally {
    submitting.value = false
  }
}

async function toggleSubscription(s: SubscriptionItem) {
  const next = s.enabled === 1 ? false : true
  try {
    const result = await subs.toggle(s.id, next)
    const task = await poll(result.task_id)
    if (task.status === 'success') {
      await subs.fetchList()
      await subs.fetchStats()
    } else {
      message.error(task.error || '切换失败')
    }
  } catch (e) {
    message.error(e instanceof Error ? e.message : String(e))
  }
}

function onDelete(s: SubscriptionItem) {
  dialog.warning({
    title: '删除订阅',
    content: `确定删除订阅「${s.keyword}」？将移除该订阅及其 ${s.match_count} 条命中关系（通知本身不受影响），此操作不可恢复。`,
    positiveText: '删除',
    negativeText: '取消',
    onPositiveClick: async () => {
      try {
        const res = await subs.remove(s.id)
        if (res.ok) {
          const cleaned = res.deleted || 0
          message.success(`已删除订阅「${s.keyword}」${cleaned > 0 ? `（清理 ${cleaned} 条命中）` : ''}`)
          await subs.fetchList()
          await subs.fetchStats()
        } else {
          message.error(res.error || '删除失败')
        }
      } catch (e) {
        message.error(e instanceof Error ? e.message : String(e))
      }
    },
  })
}

async function matchAll() {
  matchingAll.value = true
  try {
    const result = await subs.matchAll()
    const task = await poll(result.task_id)
    if (task.status === 'success') {
      const summary = task.result as { matched_notices?: number } | null | undefined
      message.success(`全库重匹配完成${summary?.matched_notices != null ? `（命中 ${summary.matched_notices} 条通知）` : ''}`)
    } else {
      message.error(task.error || '重匹配失败')
    }
    await subs.fetchList()
    await subs.fetchStats()
  } catch (e) {
    message.error(e instanceof Error ? e.message : String(e))
  } finally {
    matchingAll.value = false
  }
}

// ---------- 命中明细内联展开 ----------

function matchedState(s: SubscriptionItem): MatchedState | undefined {
  return matchedCache.value[s.id]
}

async function toggleExpand(s: SubscriptionItem) {
  if (expandedId.value === s.id) {
    expandedId.value = null
    return
  }
  expandedId.value = s.id
  if (!matchedCache.value[s.id]) {
    await loadMatched(s.id, 1)
  }
}

async function loadMatched(id: number, page: number) {
  if (matchedLoading.value[id]) return
  matchedLoading.value[id] = true
  matchedError.value[id] = ''
  try {
    const res = await subs.fetchMatchedNotices(id, page, 10)
    matchedCache.value[id] = {
      items: res.items,
      total: res.total,
      page: res.page,
      pageSize: res.page_size,
    }
  } catch (e) {
    matchedError.value[id] = e instanceof Error ? e.message : String(e)
  } finally {
    matchedLoading.value[id] = false
  }
}

function handleMatchedPage(s: SubscriptionItem, p: number) {
  loadMatched(s.id, p)
}

// ---------- 命中通知详情抽屉 ----------

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

async function openDetailById(id: number) {
  detailOpen.value = true
  detailLoading.value = true
  detail.value = null
  try {
    detail.value = await notices.fetchDetail(id)
  } catch (e) {
    message.error(e instanceof Error ? e.message : String(e))
  } finally {
    detailLoading.value = false
  }
}
</script>

<template>
  <n-space vertical size="large">
    <n-card title="订阅管理">
      <template #header-extra>
        <n-space align="center" size="small">
          <n-tooltip trigger="hover" placement="bottom">
            <template #trigger>
              <n-tag :bordered="false" round>全库 {{ subs.stats.total_notices }} 条通知</n-tag>
            </template>
            统计口径：命中基于全库所有历史通知（含已读），与首页“未读”无关。
          </n-tooltip>
          <n-tooltip trigger="hover" placement="bottom">
            <template #trigger>
              <n-button size="small" secondary :loading="matchingAll" @click="matchAll">全库重匹配</n-button>
            </template>
            按当前订阅词重新扫描全部通知，重算命中关系（数据量大时较慢）。
          </n-tooltip>
          <n-button type="primary" @click="openCreate">新增订阅</n-button>
        </n-space>
      </template>

      <n-grid :cols="3" :x-gap="24" style="margin-bottom: 8px">
        <n-grid-item>
          <n-statistic>
            <template #label>
              <n-tooltip trigger="hover" placement="top">
                <template #trigger><span>订阅总数</span></template>
                已配置的订阅词数量（含已停用）。
              </n-tooltip>
            </template>
            <template #default>{{ subs.stats.total }}</template>
          </n-statistic>
        </n-grid-item>
        <n-grid-item>
          <n-statistic>
            <template #label>
              <n-tooltip trigger="hover" placement="top">
                <template #trigger><span>启用中</span></template>
                当前生效、会对新通知自动标记的订阅词数量。
              </n-tooltip>
            </template>
            <template #default>{{ subs.stats.enabled }}</template>
          </n-statistic>
        </n-grid-item>
        <n-grid-item>
          <n-statistic>
            <template #label>
              <n-tooltip trigger="hover" placement="top">
                <template #trigger><span>命中总数</span></template>
                全库所有历史通知（含已读）与启用订阅词的匹配总数；点击各订阅卡片的“命中 N 条”可查看明细。
              </n-tooltip>
            </template>
            <template #default>{{ subs.stats.matches }}</template>
          </n-statistic>
        </n-grid-item>
      </n-grid>

      <div style="font-size: 12px; color: #888; margin-bottom: 16px">
        口径说明：命中基于全库历史通知（含已读）做标题/摘要包含匹配（大小写不敏感，可选限定类型）；修改订阅词后自动全库重匹配；停用不清历史命中记录。
      </div>

      <n-spin :show="subs.loading">
        <n-empty v-if="subs.list.length === 0 && !subs.loading" size="large">
          <template #extra>
            <n-space vertical align="center">
              <div style="color: #888; max-width: 420px">
                添加订阅词后，系统会对库中通知的标题/摘要做包含匹配（可选限定类型），并在新通知抓取时自动标记命中。
                展开订阅卡片即可查看命中的通知列表。示例词：「奖学金」「竞赛」「讲座」。
              </div>
              <n-button type="primary" @click="openCreate">新增订阅</n-button>
            </n-space>
          </template>
        </n-empty>

        <n-space v-else vertical>
          <n-card
            v-for="s in subs.list"
            :key="s.id"
            size="small"
            :bordered="true"
            content-style="padding-top: 12px"
          >
            <template #header>
              <n-space align="center" size="small">
                <n-tag :bordered="false" :type="s.enabled === 1 ? 'success' : 'default'" size="small">
                  {{ s.enabled === 1 ? '已启用' : '已停用' }}
                </n-tag>
                <span style="font-weight: 600">{{ s.keyword }}</span>
                <n-tag v-if="s.type_label" :bordered="false" type="info" size="small">{{ s.type_label }}</n-tag>
              </n-space>
            </template>

            <template #default>
              <n-space vertical size="small">
                <n-button
                  text
                  type="primary"
                  :loading="!!matchedLoading[s.id] && expandedId === s.id"
                  @click="toggleExpand(s)"
                >
                  {{ expandedId === s.id ? '收起命中明细 ▾' : `命中 ${s.match_count} 条通知` }}
                </n-button>

                <div v-if="expandedId === s.id">
                  <n-spin :show="!!matchedLoading[s.id]">
                    <div v-if="matchedError[s.id]" style="color: #d03050; font-size: 13px">
                      加载失败：{{ matchedError[s.id] }}
                    </div>
                    <n-empty
                      v-else-if="!matchedLoading[s.id] && matchedState(s)?.items.length === 0"
                      size="small"
                    >
                      <template #description>
                        <n-space vertical align="center" style="padding: 4px 0">
                          <span>暂无命中通知。可尝试更宽泛的关键词，或检查类型过滤。</span>
                          <n-button size="small" secondary @click="openEdit(s)">调整订阅词</n-button>
                        </n-space>
                      </template>
                    </n-empty>
                    <n-list v-else-if="matchedState(s)">
                      <n-list-item v-for="n in matchedState(s)!.items" :key="n.id">
                        <template #default>
                          <n-space vertical size="small">
                            <n-space align="center" size="small">
                              <n-tag size="small" :bordered="false" type="info">{{ typeLabel(n.notice_type) }}</n-tag>
                              <a href="#" @click.prevent="openDetail(n)">{{ n.title }}</a>
                              <n-tag
                                v-for="kw in n.keywords"
                                :key="kw"
                                size="small"
                                :bordered="false"
                                type="warning"
                                round
                              >
                                命中 · {{ kw }}
                              </n-tag>
                            </n-space>
                            <div style="font-size: 13px; color: #888">
                              {{ n.source }} · {{ fmtDate(n.published_at ?? n.crawled_at) }}
                            </div>
                          </n-space>
                        </template>
                      </n-list-item>
                      <template #footer>
                        <n-space justify="end" style="padding-top: 8px">
                          <n-pagination
                            v-if="(matchedState(s)?.total ?? 0) > (matchedState(s)?.pageSize ?? 10)"
                            size="small"
                            :page="matchedState(s)!.page"
                            :page-size="matchedState(s)!.pageSize"
                            :item-count="matchedState(s)!.total"
                            @update:page="(p: number) => handleMatchedPage(s, p)"
                          />
                        </n-space>
                      </template>
                    </n-list>
                  </n-spin>
                </div>
              </n-space>
            </template>

            <template #footer>
              <n-space justify="end">
                <n-button size="small" secondary @click="toggleSubscription(s)">
                  {{ s.enabled === 1 ? '停用' : '启用' }}
                </n-button>
                <n-button size="small" secondary @click="openEdit(s)">编辑</n-button>
                <n-button size="small" quaternary type="error" @click="onDelete(s)">删除</n-button>
              </n-space>
            </template>
          </n-card>
        </n-space>
      </n-spin>
    </n-card>

    <n-modal
      v-model:show="showModal"
      preset="card"
      :title="isEditing ? '编辑订阅' : '新增订阅'"
      style="width: 640px"
    >
      <n-form label-placement="left" label-width="90">
        <n-form-item label="关键词">
          <n-input v-model:value="keyword" placeholder="如 奖学金 / 课程表" />
        </n-form-item>
        <n-form-item label="通知类型">
          <n-select
            v-model:value="noticeType"
            clearable
            placeholder="全部类型"
            :options="typeOptions()"
          />
        </n-form-item>
        <n-form-item label="启用">
          <n-switch v-model:value="enabled" />
        </n-form-item>
        <n-form-item label="影响面预览">
          <n-space vertical>
            <n-button size="small" secondary :loading="previewLoading" @click="runPreview">预览命中</n-button>
            <div v-if="preview" style="font-size: 13px">
              <div>
                命中 <b>{{ preview.matched }}</b> / {{ preview.total }} 条通知
                <span v-if="!enabled" style="color: #888">（当前为停用状态，不计入命中）</span>
              </div>
              <div v-if="preview.samples?.length" style="margin-top: 8px">
                <div style="color: #888; margin-bottom: 4px">样例标题（点击查看详情）：</div>
                <div v-for="(t, i) in preview.samples" :key="i">
                  <a href="#" @click.prevent="openDetailById(preview.sample_ids?.[i] ?? 0)">· {{ t }}</a>
                </div>
              </div>
            </div>
          </n-space>
        </n-form-item>
      </n-form>
      <template #footer>
        <n-space justify="end">
          <n-button @click="showModal = false">取消</n-button>
          <n-button type="primary" :loading="submitting" @click="confirm">
            {{ isEditing ? '保存更新' : '确认创建' }}
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
            <n-descriptions-item label="状态">{{ detail.status }}</n-descriptions-item>
            <n-descriptions-item label="目标受众">{{ detail.target_audience || '—' }}</n-descriptions-item>
            <n-descriptions-item label="报名方式">{{ detail.signup_method || '—' }}</n-descriptions-item>
            <n-descriptions-item v-if="detail.signup_url" label="报名链接">
              <n-a :href="detail.signup_url" target="_blank" rel="noopener">{{ detail.signup_url }}</n-a>
            </n-descriptions-item>
            <n-descriptions-item label="地点">{{ detail.location || '—' }}</n-descriptions-item>
            <n-descriptions-item label="截止时间">{{ fmtDate(detail.deadline) }}</n-descriptions-item>
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