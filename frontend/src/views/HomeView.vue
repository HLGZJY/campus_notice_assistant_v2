<script setup lang="ts">
import { computed, onMounted } from 'vue'
import { useNoticesStore } from '../stores/useNoticesStore'
import { useRemindersStore } from '../stores/useRemindersStore'
import StatCard from '../components/StatCard.vue'
import {
  AlarmOutline,
  ChatbubbleEllipsesOutline,
  CheckmarkDoneCircleOutline,
  CheckmarkDoneOutline,
  CloseCircleOutline,
  DocumentOutline,
  FlashOutline,
  NewspaperOutline,
} from '@vicons/ionicons5'

const notices = useNoticesStore()
const reminders = useRemindersStore()

onMounted(async () => {
  await Promise.all([
    notices.fetchMeta().catch(() => {}),
    notices.fetchFilters().catch(() => {}),
    notices.fetchNotices({ page: 1, page_size: 8 }).catch(() => {}),
    notices.fetchStatusCounts().catch(() => {}),
    reminders.fetchPendingCount().catch(() => {}),
  ])
})

const greeting = computed(() => {
  const h = new Date().getHours()
  if (h < 6) return '夜深了'
  if (h < 12) return '早上好'
  if (h < 18) return '下午好'
  return '晚上好'
})

const todayText = computed(() =>
  new Date().toLocaleDateString('zh-CN', { year: 'numeric', month: 'long', day: 'numeric', weekday: 'long' })
)

function statusLabel(v: string): string {
  return notices.meta?.statuses?.find((s) => s.value === v)?.label ?? v
}

function typeLabel(v?: string | null): string {
  if (!v) return '未分类'
  return notices.meta?.notice_types?.find((t) => t.value === v)?.label ?? v
}

function fmtDate(iso?: string | null): string {
  if (!iso) return '—'
  return iso.replace('T', ' ').slice(0, 16)
}

const quickActions = [
  { to: '/notices', icon: NewspaperOutline, color: 'primary', title: '通知浏览', desc: '抓取 / 提取 / 检索通知' },
  { to: '/todos', icon: CheckmarkDoneCircleOutline, color: 'success', title: '待办清单', desc: '跟踪行动项与截止时间' },
  { to: '/qa', icon: ChatbubbleEllipsesOutline, color: 'violet', title: '智能问答', desc: '基于通知库语义问答' },
]
</script>

<template>
  <div class="home">
    <div class="hero">
      <div class="hero-main">
        <div class="hero-greet">{{ greeting }}，欢迎回来</div>
        <div class="hero-date">{{ todayText }}</div>
        <div class="hero-sub">校园通知智能助手为你聚合最新通知、提取行动项并跟踪截止时间。</div>
      </div>
      <div class="hero-actions">
        <router-link v-for="a in quickActions" :key="a.to" :to="a.to" class="hero-action">
          <span class="hero-action-icon" :class="`stat-icon--${a.color}`">
            <n-icon size="20"><component :is="a.icon" /></n-icon>
          </span>
          <span class="hero-action-text">
            <span class="hero-action-title">{{ a.title }}</span>
            <span class="hero-action-desc">{{ a.desc }}</span>
          </span>
        </router-link>
      </div>
    </div>

    <div class="stats">
      <StatCard :icon="DocumentOutline" label="未提取" :value="notices.statusCounts.raw" color="info" :hint="statusLabel('raw')" />
      <StatCard :icon="CheckmarkDoneOutline" label="已提取" :value="notices.statusCounts.extracted" color="success" :hint="statusLabel('extracted')" />
      <StatCard :icon="FlashOutline" label="部分提取" :value="notices.statusCounts.partial" color="warning" :hint="statusLabel('partial')" />
      <StatCard :icon="CloseCircleOutline" label="提取失败" :value="notices.statusCounts.failed" color="error" :hint="statusLabel('failed')" />
      <StatCard :icon="AlarmOutline" label="待处理提醒" color="error" hint="截止前 3/1 天自动生成的待办提醒">
        <template #value>
          <span :style="{ color: reminders.pendingCount > 0 ? 'var(--error)' : undefined }">
            {{ reminders.pendingCount }}
          </span>
        </template>
      </StatCard>
    </div>

    <n-card class="recent-card" :bordered="false">
      <template #header>
        <div class="section-title">
          <n-icon size="18" color="var(--primary)"><NewspaperOutline /></n-icon>
          近期通知
          <router-link to="/notices" class="recent-more">查看全部 →</router-link>
        </div>
      </template>

      <div v-if="notices.list.length === 0" class="empty-hint">暂无通知，去「通知浏览」页发起抓取。</div>
      <div v-else class="notice-list">
        <router-link
          v-for="item in notices.list"
          :key="item.id"
          :to="{ path: '/notices', query: {} }"
          class="notice-row"
        >
          <span class="notice-type">{{ typeLabel(item.notice_type) }}</span>
          <span class="notice-title">{{ item.title }}</span>
          <span class="notice-meta">
            <span class="meta-main">{{ item.source }} · {{ fmtDate(item.published_at ?? item.crawled_at) }}</span>
            <span v-if="item.deadline" class="deadline">截止 {{ fmtDate(item.deadline) }}</span>
          </span>
        </router-link>
      </div>
    </n-card>
  </div>
</template>

<style scoped>
.home {
  width: 100%;
  display: flex;
  flex-direction: column;
  gap: 20px;
}
.hero {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 24px;
  padding: 28px 32px;
  border-radius: 14px;
  background: var(--gradient-hero);
  border: 1px solid var(--border);
  flex-wrap: wrap;
}
.hero-greet {
  font-size: 22px;
  font-weight: 700;
  color: var(--text-1);
}
.hero-date {
  font-size: 13px;
  color: var(--text-2);
  margin-top: 2px;
}
.hero-sub {
  font-size: 13px;
  color: var(--text-2);
  margin-top: 8px;
  max-width: 520px;
  line-height: 1.6;
}
.hero-actions {
  display: flex;
  gap: 12px;
  flex-wrap: wrap;
}
.hero-action {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 10px 14px;
  border-radius: 10px;
  background: var(--bg-card);
  border: 1px solid var(--border);
  box-shadow: var(--shadow-1);
  text-decoration: none;
  transition: transform 0.15s ease, box-shadow 0.15s ease;
}
.hero-action:hover {
  transform: translateY(-2px);
  box-shadow: var(--shadow-2);
}
.hero-action-icon {
  flex-shrink: 0;
  width: 38px;
  height: 38px;
  border-radius: 10px;
  display: flex;
  align-items: center;
  justify-content: center;
}
.stat-icon--primary {
  background: var(--primary-soft);
  color: var(--primary);
}
.stat-icon--success {
  background: var(--success-soft);
  color: var(--success);
}
.stat-icon--violet {
  background: rgba(139, 92, 246, 0.12);
  color: var(--violet);
}
.hero-action-text {
  display: flex;
  flex-direction: column;
}
.hero-action-title {
  font-size: 14px;
  font-weight: 600;
  color: var(--text-1);
}
.hero-action-desc {
  font-size: 11px;
  color: var(--text-3);
}
.stats {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
  gap: 16px;
}
.recent-more {
  margin-left: auto;
  font-size: 13px;
  font-weight: 400;
}
.empty-hint {
  padding: 20px 0;
  color: var(--text-3);
  text-align: center;
}
.notice-list {
  display: flex;
  flex-direction: column;
}
.notice-row {
  display: flex;
  align-items: baseline;
  gap: 12px;
  padding: 12px 8px;
  border-radius: 8px;
  text-decoration: none;
  border-bottom: 1px solid var(--border);
  transition: background 0.15s ease;
}
.notice-row:last-child {
  border-bottom: none;
}
.notice-row:hover {
  background: var(--bg-soft);
}
.notice-type {
  flex-shrink: 0;
  font-size: 12px;
  color: var(--primary);
  background: var(--primary-soft);
  padding: 2px 8px;
  border-radius: 6px;
  max-width: 120px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.notice-title {
  font-size: 14px;
  font-weight: 500;
  color: var(--text-1);
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.notice-row:hover .notice-title {
  color: var(--primary);
}
.notice-meta {
  display: inline-flex;
  align-items: baseline;
  gap: 6px;
  flex: 0 1 auto;
  min-width: 0;
  max-width: 100%;
  overflow: hidden;
  white-space: nowrap;
  font-size: 12px;
  color: var(--text-3);
  font-variant-numeric: tabular-nums;
}
.meta-main {
  flex-shrink: 0;
  white-space: nowrap;
}
.deadline {
  flex: 1 1 auto;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: var(--warning);
}
</style>