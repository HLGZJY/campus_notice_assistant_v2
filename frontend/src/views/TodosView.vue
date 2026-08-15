<script setup lang="ts">
import { computed, onMounted, onUnmounted, reactive, ref } from 'vue'
import { useMessage } from 'naive-ui'
import { useTodosStore } from '../stores/useTodosStore'
import { useRemindersStore } from '../stores/useRemindersStore'
import { trackEvent, EVENT_TYPES } from '../api/events'
import { fmtDate, relativeDueText, todoStatusMeta, daysUntil } from '../utils/format'
import type { TodoItem, TodoUpdateRequest } from '../api/schema'

const message = useMessage()
const todos = useTodosStore()
const reminders = useRemindersStore()

const editOpen = ref(false)
const editMode = ref<'edit' | 'postpone'>('edit')
const editingTodo = ref<TodoItem | null>(null)
const saving = ref(false)
const editForm = reactive({ action: '', dueTs: null as number | null, notes: '' })

const overdueCount = computed(
  () =>
    todos.list.filter((t) => {
      if (t.status !== 'pending') return false
      const d = daysUntil(t.due_at)
      return d !== null && d < 0
    }).length,
)

let timer: ReturnType<typeof setInterval> | undefined

onMounted(() => {
  refresh()
  timer = setInterval(refresh, 30000)
})

onUnmounted(() => {
  if (timer) clearInterval(timer)
})

async function refresh() {
  await Promise.all([
    todos.fetchTodos().catch(() => {}),
    todos.fetchStats().catch(() => {}),
    reminders.fetchPendingCount().catch(() => {}),
    reminders.fetchReminders('pending').catch(() => {}),
  ])
}

async function mark(id: number, status: string) {
  try {
    await todos.mark(id, status)
    if (status === 'done') trackEvent(EVENT_TYPES.TODO_DONE, id)
    await Promise.all([
      reminders.fetchPendingCount().catch(() => {}),
      reminders.fetchReminders('pending').catch(() => {}),
    ])
    message.success(status === 'done' ? '待办已完成' : status === 'skipped' ? '待办已跳过' : '待办已恢复')
  } catch (e) {
    message.error(e instanceof Error ? e.message : String(e))
  }
}

async function ignoreReminder(id: number) {
  try {
    await reminders.mark(id, 'ignored')
    message.success('提醒已忽略')
  } catch (e) {
    message.error(e instanceof Error ? e.message : String(e))
  }
}

function openEdit(todo: TodoItem, mode: 'edit' | 'postpone' = 'edit') {
  editMode.value = mode
  editingTodo.value = todo
  editForm.action = todo.action
  editForm.dueTs = todo.due_at ? new Date(todo.due_at).getTime() : null
  editForm.notes = todo.notes ?? ''
  editOpen.value = true
}

async function saveEdit() {
  if (!editingTodo.value) return
  const action = editForm.action.trim()
  if (!action) {
    message.error('待办内容不能为空')
    return
  }
  const payload: TodoUpdateRequest = {
    action,
    due_at: editForm.dueTs ? new Date(editForm.dueTs).toISOString() : null,
    notes: editForm.notes.trim() || null,
  }
  saving.value = true
  try {
    await todos.update(editingTodo.value.id, payload)
    message.success(editMode.value === 'postpone' ? '已延期' : '待办已更新')
    editOpen.value = false
  } catch (e) {
    message.error(e instanceof Error ? e.message : String(e))
  } finally {
    saving.value = false
  }
}
</script>

<template>
  <n-space vertical size="large">
    <n-card title="待办中心">
      <template #header-extra>
        <n-space>
          <n-button size="small" secondary :loading="todos.loading" @click="refresh">
            刷新
          </n-button>
          <n-tooltip trigger="hover">
            <template #trigger>
              <router-link to="/notices" style="text-decoration: none">
                <n-button size="small" type="primary" secondary>到通知页生成待办</n-button>
              </router-link>
            </template>
            在「通知浏览」页对行动型通知点「生成待办」即可
          </n-tooltip>
        </n-space>
      </template>

      <n-grid :cols="4" :x-gap="12" style="margin-bottom: 16px">
        <n-grid-item>
          <n-statistic label="待办" :value="todos.stats.pending" />
        </n-grid-item>
        <n-grid-item>
          <n-statistic label="临期提醒">
            <template #default>
              <span :style="{ color: reminders.pendingCount > 0 ? '#d03050' : undefined }">
                {{ reminders.pendingCount }}
              </span>
            </template>
          </n-statistic>
        </n-grid-item>
        <n-grid-item>
          <n-statistic label="逾期" :value="overdueCount" />
        </n-grid-item>
        <n-grid-item>
          <n-statistic label="已完成" :value="todos.stats.done" />
        </n-grid-item>
      </n-grid>

      <n-divider style="margin: 8px 0 12px">临期提醒（截止前 3/1 天自动生成）</n-divider>
      <div v-if="reminders.reminders.length === 0" style="color: #999; font-size: 13px">
        暂无临期提醒
      </div>
      <n-list v-else>
        <n-list-item v-for="r in reminders.reminders" :key="r.id">
          <template #default>
            <n-space vertical size="small">
              <n-space align="center" size="small">
                <n-tag size="small" :bordered="false" :type="r.is_today ? 'error' : 'warning'">
                  {{ r.is_today ? '今天截止' : r.tier_label || r.tier }}
                </n-tag>
                <span>{{ r.notice_title || `通知 #${r.notice_id}` }}</span>
              </n-space>
              <div style="color: #888; font-size: 13px">
                截止 {{ fmtDate(r.due_at) }} · {{ relativeDueText(r.due_at) }}
                <span v-if="r.todo_action"> · {{ r.todo_action }}</span>
              </div>
            </n-space>
          </template>
          <template #suffix>
            <n-button size="small" quaternary type="error" @click="ignoreReminder(r.id)">忽略</n-button>
          </template>
        </n-list-item>
      </n-list>

      <n-divider style="margin: 16px 0 12px">待办清单</n-divider>
      <div
        v-if="todos.list.length === 0 && reminders.reminders.length === 0"
        style="color: #999"
      >
        暂无待办，去「通知浏览」页为行动型通知生成待办
      </div>
      <n-list v-else>
        <n-list-item v-for="t in todos.list" :key="t.id">
          <template #default>
            <n-space vertical size="small">
              <n-space align="center" size="small">
                <n-tag size="small" :bordered="false" :type="todoStatusMeta(t.status, t.due_at).type">
                  {{ todoStatusMeta(t.status, t.due_at).label }}
                </n-tag>
                <span
                  :style="{ textDecoration: t.status === 'done' ? 'line-through' : undefined }"
                >
                  {{ t.action }}
                </span>
                <n-tag
                  v-if="t.priority === 'high' && t.status === 'pending'"
                  size="small"
                  :bordered="false"
                  type="error"
                >
                  高优先级
                </n-tag>
              </n-space>
              <div style="color: #888; font-size: 13px">
                来源：{{ t.notice_title || `通知 #${t.notice_id}` }} · 截止 {{ fmtDate(t.due_at) }} ·
                {{ relativeDueText(t.due_at) }}
              </div>
              <div v-if="t.notes" style="color: #666; font-size: 13px">备注：{{ t.notes }}</div>
            </n-space>
          </template>
          <template #suffix>
            <n-space>
              <n-button
                v-if="t.status === 'pending'"
                size="small"
                secondary
                @click="openEdit(t, 'edit')"
              >
                编辑
              </n-button>
              <n-button
                v-if="t.status === 'pending'"
                size="small"
                secondary
                @click="openEdit(t, 'postpone')"
              >
                延期
              </n-button>
              <n-button
                v-if="t.status !== 'done'"
                size="small"
                type="success"
                @click="mark(t.id, 'done')"
              >
                标记完成
              </n-button>
              <n-button
                v-if="t.status === 'pending'"
                size="small"
                secondary
                @click="mark(t.id, 'skipped')"
              >
                跳过
              </n-button>
              <n-button v-if="t.status !== 'pending'" size="small" secondary @click="mark(t.id, 'pending')">
                恢复
              </n-button>
            </n-space>
          </template>
        </n-list-item>
      </n-list>
    </n-card>

    <n-modal
      v-model:show="editOpen"
      preset="card"
      :title="editMode === 'postpone' ? '延期待办' : '编辑待办'"
      style="width: 520px"
    >
      <n-form label-placement="top">
        <n-form-item label="待办内容">
          <n-input v-model:value="editForm.action" placeholder="待办内容" />
        </n-form-item>
        <n-form-item label="截止时间（清空 = 无截止）">
          <n-date-picker
            v-model:value="editForm.dueTs"
            type="datetime"
            clearable
            style="width: 100%"
          />
        </n-form-item>
        <n-form-item label="备注">
          <n-input
            v-model:value="editForm.notes"
            type="textarea"
            :rows="3"
            placeholder="记录进展、补充说明"
          />
        </n-form-item>
      </n-form>
      <template #footer>
        <n-space justify="end">
          <n-button @click="editOpen = false">取消</n-button>
          <n-button type="primary" :loading="saving" @click="saveEdit">保存</n-button>
        </n-space>
      </template>
    </n-modal>
  </n-space>
</template>