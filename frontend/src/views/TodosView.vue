<script setup lang="ts">
import { computed, onMounted, onUnmounted, reactive, ref } from 'vue'
import { useMessage } from 'naive-ui'
import {
  AlarmOutline,
  CalendarOutline,
  CheckmarkDoneCircleOutline,
  CheckmarkDoneOutline,
  CreateOutline,
  ListOutline,
  RefreshOutline,
  TimeOutline,
} from '@vicons/ionicons5'
import { useTodosStore } from '../stores/useTodosStore'
import { useRemindersStore } from '../stores/useRemindersStore'
import StatCard from '../components/StatCard.vue'
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

function barClass(type: 'success' | 'default' | 'error' | 'warning' | 'info'): string {
  switch (type) {
    case 'success':
      return 'bar--success'
    case 'error':
      return 'bar--error'
    case 'warning':
      return 'bar--warning'
    case 'info':
      return 'bar--primary'
    default:
      return 'bar--default'
  }
}
</script>

<template>
  <div class="todos">
    <div class="stats">
      <StatCard :icon="ListOutline" label="待办" :value="todos.stats.pending" color="primary" hint="当前未完成（待开始/临期/逾期）的行动项" />
      <StatCard :icon="AlarmOutline" label="临期提醒" color="error" hint="截止前 3/1 天自动生成的提醒">
        <template #value>
          <span :style="{ color: reminders.pendingCount > 0 ? 'var(--error)' : undefined }">
            {{ reminders.pendingCount }}
          </span>
        </template>
      </StatCard>
      <StatCard :icon="TimeOutline" label="逾期" :value="overdueCount" color="warning" hint="已超过截止日期的待办" />
      <StatCard :icon="CheckmarkDoneOutline" label="已完成" :value="todos.stats.done" color="success" hint="累计完成的待办数量" />
    </div>

    <n-card :bordered="false">
      <template #header>
        <div class="section-title">
          <n-icon size="18" color="var(--warning)"><AlarmOutline /></n-icon>
          临期提醒
          <span class="section-sub">截止前 3/1 天自动生成</span>
        </div>
      </template>
      <template #header-extra>
        <n-space>
          <n-button size="small" secondary :loading="todos.loading" @click="refresh">
            <template #icon><n-icon><RefreshOutline /></n-icon></template>
            刷新
          </n-button>
          <router-link to="/notices" class="inline-link">
            <n-button size="small" type="primary" secondary>
              <template #icon><n-icon><CheckmarkDoneCircleOutline /></n-icon></template>
              到通知页生成待办
            </n-button>
          </router-link>
        </n-space>
      </template>

      <n-empty v-if="reminders.reminders.length === 0" description="暂无临期提醒" size="small">
        <template #extra>
          <span class="muted">在「通知浏览」页对行动型通知点「生成待办」，临近截止时会自动提醒。</span>
        </template>
      </n-empty>
      <div v-else class="reminder-list">
        <div v-for="r in reminders.reminders" :key="r.id" class="reminder-row" :class="{ 'reminder-row--today': r.is_today }">
          <div class="reminder-bar" :class="r.is_today ? 'bar--error' : 'bar--warning'" />
          <div class="reminder-content">
            <div class="reminder-top">
              <n-tag size="small" :bordered="false" :type="r.is_today ? 'error' : 'warning'">
                {{ r.is_today ? '今天截止' : r.tier_label || r.tier }}
              </n-tag>
              <span class="reminder-title">{{ r.notice_title || `通知 #${r.notice_id}` }}</span>
            </div>
            <div class="reminder-meta muted">
              截止 {{ fmtDate(r.due_at) }} · {{ relativeDueText(r.due_at) }}
              <span v-if="r.todo_action"> · {{ r.todo_action }}</span>
            </div>
          </div>
          <div class="reminder-action">
            <n-button size="small" quaternary type="error" @click="ignoreReminder(r.id)">忽略</n-button>
          </div>
        </div>
      </div>
    </n-card>

    <n-card :bordered="false">
      <template #header>
        <div class="section-title">
          <n-icon size="18" color="var(--primary)"><ListOutline /></n-icon>
          待办清单
        </div>
      </template>

      <n-empty
        v-if="todos.list.length === 0 && reminders.reminders.length === 0"
        description="暂无待办"
        size="small"
      >
        <template #extra>
          <span class="muted">去「通知浏览」页为行动型通知生成待办。</span>
        </template>
      </n-empty>
      <div v-else class="todo-list">
        <div v-for="t in todos.list" :key="t.id" class="todo-row">
          <div class="todo-bar" :class="barClass(todoStatusMeta(t.status, t.due_at).type)" />
          <div class="todo-main">
            <div class="todo-top">
              <n-tag size="small" :bordered="false" :type="todoStatusMeta(t.status, t.due_at).type">
                {{ todoStatusMeta(t.status, t.due_at).label }}
              </n-tag>
              <span class="todo-action" :class="{ 'todo-action--done': t.status === 'done' }">
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
            </div>
            <div class="todo-meta muted">
              来源：
              <n-a v-if="t.notice_url" :href="t.notice_url" target="_blank" rel="noopener">
                {{ t.notice_title || `通知 #${t.notice_id}` }}
              </n-a>
              <span v-else>{{ t.notice_title || `通知 #${t.notice_id}` }}</span>
              · 截止 {{ fmtDate(t.due_at) }} · {{ relativeDueText(t.due_at) }}
            </div>
            <div v-if="t.notes" class="todo-notes">备注：{{ t.notes }}</div>
          </div>
          <div class="todo-actions">
            <n-button
              v-if="t.status === 'pending'"
              size="small"
              secondary
              @click="openEdit(t, 'edit')"
            >
              <template #icon><n-icon><CreateOutline /></n-icon></template>
              编辑
            </n-button>
            <n-button
              v-if="t.status === 'pending'"
              size="small"
              secondary
              @click="openEdit(t, 'postpone')"
            >
              <template #icon><n-icon><CalendarOutline /></n-icon></template>
              延期
            </n-button>
            <n-button
              v-if="t.status !== 'done'"
              size="small"
              type="success"
              @click="mark(t.id, 'done')"
            >
              <template #icon><n-icon><CheckmarkDoneOutline /></n-icon></template>
              标记完成
            </n-button>
            <n-button v-if="t.status === 'pending'" size="small" secondary @click="mark(t.id, 'skipped')">
              跳过
            </n-button>
            <n-button v-if="t.status !== 'pending'" size="small" secondary @click="mark(t.id, 'pending')">
              恢复
            </n-button>
          </div>
        </div>
      </div>
    </n-card>

    <n-modal v-model:show="editOpen" preset="card" :title="editMode === 'postpone' ? '延期待办' : '编辑待办'" style="width: 520px">
      <n-form label-placement="top">
        <n-form-item label="待办内容">
          <n-input v-model:value="editForm.action" placeholder="待办内容" />
        </n-form-item>
        <n-form-item label="截止时间（清空 = 无截止）">
          <n-date-picker v-model:value="editForm.dueTs" type="datetime" clearable style="width: 100%" />
        </n-form-item>
        <n-form-item label="备注">
          <n-input v-model:value="editForm.notes" type="textarea" :rows="3" placeholder="记录进展、补充说明" />
        </n-form-item>
      </n-form>
      <template #footer>
        <n-space justify="end">
          <n-button @click="editOpen = false">取消</n-button>
          <n-button type="primary" :loading="saving" @click="saveEdit">保存</n-button>
        </n-space>
      </template>
    </n-modal>
  </div>
</template>

<style scoped>
.todos {
  display: flex;
  flex-direction: column;
  gap: 20px;
}
.stats {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
  gap: 16px;
}
.inline-link {
  text-decoration: none;
}
.section-sub {
  font-size: 12px;
  font-weight: 400;
  color: var(--text-3);
}
.reminder-list {
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.reminder-row,
.todo-row {
  display: flex;
  align-items: center;
  gap: 14px;
  padding: 14px 16px;
  border: 1px solid var(--border);
  border-radius: 10px;
  background: var(--bg-card);
  transition: box-shadow 0.15s ease, transform 0.15s ease;
}
.reminder-row:hover,
.todo-row:hover {
  box-shadow: var(--shadow-2);
  transform: translateY(-1px);
}
.reminder-row--today {
  border-color: var(--error);
  background: var(--error-soft);
}
.reminder-bar,
.todo-bar {
  flex-shrink: 0;
  width: 4px;
  align-self: stretch;
  border-radius: 2px;
}
.bar--success {
  background: var(--success);
}
.bar--error {
  background: var(--error);
}
.bar--warning {
  background: var(--warning);
}
.bar--primary {
  background: var(--primary);
}
.bar--default {
  background: var(--text-3);
}
.reminder-content,
.todo-main {
  flex: 1;
  min-width: 0;
}
.reminder-top,
.todo-top {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}
.reminder-title {
  font-size: 14px;
  font-weight: 600;
  color: var(--text-1);
}
.todo-action {
  font-size: 14px;
  font-weight: 600;
  color: var(--text-1);
}
.todo-action--done {
  text-decoration: line-through;
  color: var(--text-3);
}
.reminder-meta,
.todo-meta {
  font-size: 12px;
  margin-top: 6px;
  font-variant-numeric: tabular-nums;
}
.todo-notes {
  font-size: 12px;
  color: var(--text-2);
  margin-top: 6px;
}
.reminder-action,
.todo-actions {
  display: flex;
  gap: 8px;
  flex-shrink: 0;
  flex-wrap: wrap;
  justify-content: flex-end;
}
.todo-list {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

@media (max-width: 720px) {
  .reminder-row,
  .todo-row {
    align-items: flex-start;
    flex-direction: column;
  }
  .reminder-action,
  .todo-actions {
    width: 100%;
    justify-content: flex-start;
  }
}
</style>