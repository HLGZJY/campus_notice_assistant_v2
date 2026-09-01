<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { useMessage } from 'naive-ui'
import { useRouter } from 'vue-router'
import {
  AlarmOutline,
  CheckmarkDoneOutline,
  CloudDownloadOutline,
  EyeOffOutline,
  InformationCircleOutline,
  NotificationsOutline,
  RefreshOutline,
  ServerOutline,
} from '@vicons/ionicons5'
import StatCard from '../components/StatCard.vue'
import { useNotificationsStore, type SystemMessage } from '../stores/useNotificationsStore'
import { fmtDate, relativeDueText } from '../utils/format'

const message = useMessage()
const router = useRouter()
const notifications = useNotificationsStore()

const activeStatus = ref<string>('pending')
const activeTab = ref<'reminders' | 'system'>('reminders')

const filtered = computed(() => {
  if (activeStatus.value === 'all') return notifications.reminders
  return notifications.reminders.filter((r) => r.status === activeStatus.value)
})

const statCards = computed(() => [
  { key: 'pending', label: '待处理', value: notifications.stats.pending, icon: AlarmOutline },
  { key: 'read', label: '已读', value: notifications.stats.read, icon: CheckmarkDoneOutline },
  { key: 'ignored', label: '已忽略', value: notifications.stats.ignored, icon: EyeOffOutline },
  { key: 'total', label: '全部', value: notifications.stats.total, icon: NotificationsOutline },
])

function tierTagType(tier: string): 'error' | 'warning' | 'default' | 'success' {
  if (tier === 'critical' || tier === 'urgent') return 'error'
  if (tier === 'important' || tier === 'high') return 'warning'
  if (tier === 'normal') return 'success'
  return 'default'
}

async function markAs(id: number, status: 'read' | 'ignored') {
  try {
    await notifications.mark(id, status)
    message.success(status === 'read' ? '已标记为已读' : '已忽略')
    await notifications.fetchReminders(activeStatus.value === 'all' ? undefined : activeStatus.value).catch(() => {})
  } catch (e) {
    message.error(e instanceof Error ? e.message : String(e))
  }
}

function onStatusFilter(key: string) {
  activeStatus.value = key
  notifications.fetchReminders(key === 'all' ? undefined : key).catch(() => {})
}

const statusFilterOptions = [
  { label: '待处理', value: 'pending' },
  { label: '已读', value: 'read' },
  { label: '已忽略', value: 'ignored' },
  { label: '全部', value: 'all' },
]

function systemLevelType(level: SystemMessage['level']): 'default' | 'success' | 'warning' | 'error' | 'info' {
  if (level === 'success') return 'success'
  if (level === 'warning') return 'warning'
  if (level === 'error') return 'error'
  if (level === 'info') return 'info'
  return 'default'
}

function systemIcon(kind: SystemMessage['kind']) {
  if (kind === 'update') return CloudDownloadOutline
  if (kind === 'desktop') return ServerOutline
  return InformationCircleOutline
}

async function refresh() {
  await notifications.refresh().catch(() => {})
  await notifications
    .fetchReminders(activeStatus.value === 'all' ? undefined : activeStatus.value)
    .catch(() => {})
  await notifications.loadSystemMessages().catch(() => {})
}

let timer: ReturnType<typeof setInterval> | undefined

onMounted(() => {
  refresh()
  timer = setInterval(() => {
    notifications.fetchPendingCount().catch(() => {})
  }, 30000)
})

onUnmounted(() => {
  if (timer) clearInterval(timer)
})

function checkUpdate() {
  router.push('/config')
}
</script>

<template>
  <div class="notifications-view">
    <div class="stat-row">
      <StatCard
        v-for="card in statCards"
        :key="card.key"
        :icon="card.icon"
        :label="card.label"
        :value="card.value"
      />
    </div>

    <n-tabs
      v-model:value="activeTab"
      type="line"
      animated
    >
      <!-- 提醒 Tab -->
      <n-tab-pane
        name="reminders"
        tab="提醒"
      >
        <template #tab>
          <span class="tab-label">
            <n-icon><AlarmOutline /></n-icon>
            提醒
            <n-badge
              v-if="notifications.pendingCount > 0"
              :value="notifications.pendingCount"
              :max="99"
              type="error"
              :show="notifications.pendingCount > 0"
            />
          </span>
        </template>

        <div class="filter-bar">
          <n-radio-group
            :value="activeStatus"
            size="small"
            @update:value="onStatusFilter"
          >
            <n-radio-button
              v-for="opt in statusFilterOptions"
              :key="opt.value"
              :value="opt.value"
            >
              {{ opt.label }}
            </n-radio-button>
          </n-radio-group>
        </div>

        <div
          v-if="notifications.loading"
          class="empty-state"
        >
          加载中…
        </div>
        <div
          v-else-if="filtered.length === 0"
          class="empty-state"
        >
          <n-icon
            size="40"
            color="#c2c2c2"
          >
            <NotificationsOutline />
          </n-icon>
          <p>当前筛选下没有提醒</p>
        </div>
        <div
          v-else
          class="reminder-list"
        >
          <div
            v-for="r in filtered"
            :key="r.id"
            class="reminder-item"
            :class="{ 'is-read': r.status !== 'pending' }"
          >
            <div class="reminder-left">
              <div class="reminder-title-row">
                <n-tag
                  size="small"
                  :type="tierTagType(r.tier)"
                  :bordered="false"
                >
                  {{ r.tier_label || r.tier }}
                </n-tag>
                <span class="reminder-title">{{ r.notice_title || `通知 #${r.notice_id}` }}</span>
                <span
                  v-if="r.is_today"
                  class="today-tag"
                >今天</span>
              </div>
              <div class="reminder-detail">
                <span v-if="r.todo_action">📌 {{ r.todo_action }}</span>
                <span v-if="r.due_at">｜ {{ relativeDueText(r.due_at) }}</span>
                <span v-if="r.notice_source">｜ {{ r.notice_source }}</span>
              </div>
              <div class="reminder-meta">
                创建于 {{ fmtDate(r.created_at) }}
                <template v-if="r.read_at">
                  ｜ 已于 {{ fmtDate(r.read_at) }} 读
                </template>
              </div>
            </div>
            <div class="reminder-actions">
              <template v-if="r.status === 'pending'">
                <n-button
                  size="small"
                  type="primary"
                  tertiary
                  @click="markAs(r.id, 'read')"
                >
                  <template #icon>
                    <n-icon><CheckmarkDoneOutline /></n-icon>
                  </template>
                  已读
                </n-button>
                <n-button
                  size="small"
                  tertiary
                  @click="markAs(r.id, 'ignored')"
                >
                  <template #icon>
                    <n-icon><EyeOffOutline /></n-icon>
                  </template>
                  忽略
                </n-button>
              </template>
              <n-text
                v-else
                depth="3"
                style="font-size: 12px"
              >
                {{ r.status === 'read' ? '已读' : '已忽略' }}
              </n-text>
            </div>
          </div>
        </div>
      </n-tab-pane>

      <!-- 系统消息 Tab -->
      <n-tab-pane
        name="system"
        tab="系统消息"
      >
        <div
          v-if="notifications.systemMessages.length === 0"
          class="empty-state"
        >
          <n-icon
            size="40"
            color="#c2c2c2"
          >
            <InformationCircleOutline />
          </n-icon>
          <p>暂无系统消息</p>
        </div>
        <div
          v-else
          class="system-list"
        >
          <div
            v-for="m in notifications.systemMessages"
            :key="m.id"
            class="system-item"
          >
            <n-icon
              size="22"
              :color="m.level === 'error' ? '#e88080' : m.level === 'warning' ? '#f0a020' : '#63e2b7'"
            >
              <component :is="systemIcon(m.kind)" />
            </n-icon>
            <div class="system-body">
              <div class="system-title-row">
                <span class="system-title">{{ m.title }}</span>
                <n-tag
                  size="small"
                  :type="systemLevelType(m.level)"
                  :bordered="false"
                >
                  {{ m.kind === 'update' ? '更新' : m.kind === 'desktop' ? '桌面' : '信息' }}
                </n-tag>
              </div>
              <div class="system-detail">
                {{ m.detail }}
              </div>
              <div class="system-meta">
                {{ fmtDate(m.timestamp) }}
              </div>
            </div>
          </div>
        </div>
        <div class="system-actions">
          <n-button
            size="small"
            secondary
            @click="checkUpdate"
          >
            <template #icon>
              <n-icon><RefreshOutline /></n-icon>
            </template>
            前往系统配置检查更新
          </n-button>
        </div>
      </n-tab-pane>
    </n-tabs>
  </div>
</template>

<style scoped>
.notifications-view {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.stat-row {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 12px;
}

.tab-label {
  display: inline-flex;
  align-items: center;
  gap: 6px;
}

.filter-bar {
  margin-bottom: 12px;
}

.empty-state {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  padding: 48px 0;
  color: var(--text-3, #999);
  gap: 8px;
}
.empty-state p {
  margin: 0;
  font-size: 13px;
}

.reminder-list,
.system-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.reminder-item {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
  padding: 14px 16px;
  background: var(--bg-2, rgba(127, 127, 127, 0.06));
  border: 1px solid var(--border, rgba(127, 127, 127, 0.16));
  border-radius: 10px;
}
.reminder-item.is-read {
  opacity: 0.62;
}
.reminder-left {
  min-width: 0;
  flex: 1;
}
.reminder-title-row {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}
.reminder-title {
  font-weight: 600;
  font-size: 14px;
  color: var(--text-1, #222);
}
.today-tag {
  font-size: 11px;
  color: #e88080;
  border: 1px solid #e88080;
  border-radius: 4px;
  padding: 0 5px;
  line-height: 16px;
}
.reminder-detail {
  margin-top: 6px;
  font-size: 12px;
  color: var(--text-2, #666);
}
.reminder-meta {
  margin-top: 4px;
  font-size: 11px;
  color: var(--text-3, #999);
}
.reminder-actions {
  display: flex;
  gap: 6px;
  flex-shrink: 0;
}

.system-item {
  display: flex;
  align-items: flex-start;
  gap: 12px;
  padding: 14px 16px;
  background: var(--bg-2, rgba(127, 127, 127, 0.06));
  border: 1px solid var(--border, rgba(127, 127, 127, 0.16));
  border-radius: 10px;
}
.system-body {
  min-width: 0;
  flex: 1;
}
.system-title-row {
  display: flex;
  align-items: center;
  gap: 8px;
}
.system-title {
  font-weight: 600;
  font-size: 14px;
  color: var(--text-1, #222);
}
.system-detail {
  margin-top: 4px;
  font-size: 12px;
  color: var(--text-2, #666);
}
.system-meta {
  margin-top: 4px;
  font-size: 11px;
  color: var(--text-3, #999);
}
.system-actions {
  margin-top: 8px;
}

@media (max-width: 720px) {
  .stat-row {
    grid-template-columns: repeat(2, 1fr);
  }
}
</style>
