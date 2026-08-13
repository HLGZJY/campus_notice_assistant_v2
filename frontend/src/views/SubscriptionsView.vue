<script setup lang="ts">
import { ref, onMounted, computed } from 'vue'
import { useMessage } from 'naive-ui'
import { useSubscriptionsStore } from '../stores/useSubscriptionsStore'
import { useNoticesStore } from '../stores/useNoticesStore'
import { useTaskPoll } from '../composables/useTaskPoll'
import type { SubscriptionItem, SubscriptionPreview } from '../api/schema'

const message = useMessage()
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

const isEditing = computed(() => editingId.value !== null)

onMounted(async () => {
  await Promise.all([
    subs.fetchList().catch(() => {}),
    subs.fetchStats().catch(() => {}),
    notices.fetchFilters().catch(() => {}),
  ])
})

function typeOptions() {
  return notices.types.map((t) => ({ label: t, value: t }))
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
      message.success(isEditing.value ? '订阅已更新' : '订阅已创建')
      await subs.fetchList()
      await subs.fetchStats()
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

async function deleteSubscription(s: SubscriptionItem) {
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
}

async function matchAll() {
  matchingAll.value = true
  try {
    const result = await subs.matchAll()
    const task = await poll(result.task_id)
    if (task.status === 'success') {
      message.success('全库重匹配完成')
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
</script>

<template>
  <n-space vertical size="large">
    <n-card title="订阅管理">
      <template #header-extra>
        <n-space>
          <n-tag :bordered="false" type="info" round>总数 {{ subs.stats.total }}</n-tag>
          <n-tag :bordered="false" type="success" round>启用 {{ subs.stats.enabled }}</n-tag>
          <n-tag :bordered="false" type="warning" round>命中 {{ subs.stats.matches }}</n-tag>
          <n-button size="small" secondary :loading="matchingAll" @click="matchAll">全库重匹配</n-button>
          <n-button size="small" type="primary" @click="openCreate">新增订阅</n-button>
        </n-space>
      </template>

      <n-spin :show="subs.loading">
        <div v-if="subs.list.length === 0">暂无订阅，点击「新增订阅」开始。</div>
        <n-list v-else>
          <n-list-item v-for="s in subs.list" :key="s.id">
            <template #title>
              <n-space align="center" size="small">
                <n-tag :bordered="false" :type="s.enabled === 1 ? 'success' : 'default'" size="small">
                  {{ s.enabled === 1 ? '已启用' : '已停用' }}
                </n-tag>
                <span>{{ s.keyword }}</span>
                <n-tag v-if="s.type_label" :bordered="false" type="info" size="small">{{ s.type_label }}</n-tag>
              </n-space>
            </template>
            <template #desc>命中 {{ s.match_count }} 条通知</template>
            <template #extra>
              <n-space>
                <n-button size="small" secondary @click="toggleSubscription(s)">
                  {{ s.enabled === 1 ? '停用' : '启用' }}
                </n-button>
                <n-button size="small" secondary @click="openEdit(s)">编辑</n-button>
                <n-button size="small" quaternary type="error" @click="deleteSubscription(s)">删除</n-button>
              </n-space>
            </template>
          </n-list-item>
        </n-list>
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
              </div>
              <div v-if="preview.samples?.length" style="margin-top: 8px">
                <div style="color: #888; margin-bottom: 4px">样例标题：</div>
                <div v-for="(t, i) in preview.samples" :key="i">· {{ t }}</div>
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
  </n-space>
</template>