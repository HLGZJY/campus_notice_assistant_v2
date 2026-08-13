<script setup lang="ts">
import { onMounted } from 'vue'
import { useMessage } from 'naive-ui'
import { useTodosStore } from '../stores/useTodosStore'
import { useRemindersStore } from '../stores/useRemindersStore'
import { trackEvent, EVENT_TYPES } from '../api/events'

const message = useMessage()
const todos = useTodosStore()
const reminders = useRemindersStore()

onMounted(async () => {
  await Promise.all([
    todos.fetchTodos().catch(() => {}),
    todos.fetchStats().catch(() => {}),
    reminders.fetchPendingCount().catch(() => {}),
    reminders.fetchReminders('pending').catch(() => {}),
  ])
})

async function mark(id: number, status: string) {
  try {
    await todos.mark(id, status)
    if (status === 'done') trackEvent(EVENT_TYPES.TODO_DONE, id)
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

function fmtDate(iso?: string | null): string {
  if (!iso) return '—'
  return iso.replace('T', ' ').slice(0, 16)
}
</script>

<template>
  <n-space vertical size="large">
    <n-card title="截止提醒">
      <n-space>
        <n-tag :bordered="false" type="error" round>待处理 {{ reminders.pendingCount }}</n-tag>
      </n-space>
      <div v-if="reminders.reminders.length === 0" style="margin-top: 12px">暂无待处理提醒</div>
      <n-list v-else style="margin-top: 12px">
        <n-list-item v-for="r in reminders.reminders" :key="r.id">
          <template #default>
            <n-space vertical size="small">
              <n-space align="center" size="small">
                <n-tag
                  size="small"
                  :bordered="false"
                  :type="r.is_today ? 'error' : 'warning'"
                >
                  {{ r.is_today ? '今天截止' : r.tier_label || r.tier }}
                </n-tag>
                <span>{{ r.notice_title || `通知 #${r.notice_id}` }}</span>
              </n-space>
              <div>截止 {{ fmtDate(r.due_at) }}<span v-if="r.todo_action"> · {{ r.todo_action }}</span></div>
            </n-space>
          </template>
          <template #suffix>
            <n-button size="small" quaternary type="error" @click="ignoreReminder(r.id)">忽略</n-button>
          </template>
        </n-list-item>
      </n-list>
    </n-card>

    <n-card title="待办清单">
      <template #header-extra>
        <n-space>
          <n-button size="small" secondary @click="todos.fetchTodos().then(() => todos.fetchStats()).catch(() => {})">
            刷新
          </n-button>
          <router-link to="/notices" style="text-decoration: none">
            <n-button size="small" type="primary" secondary>从通知生成待办</n-button>
          </router-link>
        </n-space>
      </template>
      <div v-if="todos.list.length === 0">暂无待办</div>
      <n-list v-else>
        <n-list-item v-for="t in todos.list" :key="t.id">
          <template #default>
            <n-space vertical size="small">
              <n-space align="center" size="small">
                <n-tag
                  size="small"
                  :bordered="false"
                  :type="t.status === 'done' ? 'success' : t.status === 'skipped' ? 'default' : t.due_at && t.due_at <= new Date().toISOString() ? 'error' : 'warning'"
                >
                  {{ t.status }}
                </n-tag>
                <span :style="{ textDecoration: t.status === 'done' ? 'line-through' : undefined }">{{ t.action }}</span>
              </n-space>
              <div>{{ t.notice_title }} · 截止 {{ fmtDate(t.due_at) }}</div>
            </n-space>
          </template>
          <template #suffix>
            <n-space>
              <n-button
                v-if="t.status !== 'done'"
                size="small"
                type="success"
                @click="mark(t.id, 'done')"
              >
                完成
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
  </n-space>
</template>