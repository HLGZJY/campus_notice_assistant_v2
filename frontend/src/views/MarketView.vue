<script setup lang="ts">
import { ref } from 'vue'
import { useMessage } from 'naive-ui'
import {
  ChatbubbleEllipsesOutline,
  CheckmarkDoneCircleOutline,
  CloudDownloadOutline,
  ConstructOutline,
  ExtensionPuzzleOutline,
  NewspaperOutline,
  SparklesOutline,
  StorefrontOutline,
} from '@vicons/ionicons5'
import { trackEvent, EVENT_TYPES } from '../api/events'

const message = useMessage()
const calling = ref(false)

const services = [
  {
    key: 'crawl',
    icon: CloudDownloadOutline,
    title: '通知抓取',
    desc: '定时从各数据源抓取最新通知，支持增量与深度检查。',
    status: '内置',
    color: 'primary' as const,
  },
  {
    key: 'extract',
    icon: SparklesOutline,
    title: '智能提取',
    desc: 'LLM 结构化提取通知关键信息：类型、受众、截止时间等。',
    status: '内置',
    color: 'violet' as const,
  },
  {
    key: 'qa',
    icon: ChatbubbleEllipsesOutline,
    title: '智能问答',
    desc: '基于通知向量库的语义问答，回答附引用来源。',
    status: '内置',
    color: 'info' as const,
  },
  {
    key: 'todo',
    icon: CheckmarkDoneCircleOutline,
    title: '待办生成',
    desc: '自动将行动型通知转化为待办并跟踪截止提醒。',
    status: '内置',
    color: 'success' as const,
  },
]

function callFakeService() {
  calling.value = true
  trackEvent(EVENT_TYPES.SERVICE_CLICK, undefined, 'market')
  setTimeout(() => {
    calling.value = false
    message.success('假服务调用成功（埋点 service_button_click 已上报）')
  }, 600)
}
</script>

<template>
  <div class="market">
    <n-card :bordered="false">
      <template #header>
        <div class="section-title">
          <n-icon size="18" color="var(--primary)"><StorefrontOutline /></n-icon>
          服务市场
          <span class="section-sub muted">可扩展服务（演示）</span>
        </div>
      </template>

      <div class="service-grid">
        <div v-for="s in services" :key="s.key" class="service-card">
          <div class="service-icon" :class="`service-icon--${s.color}`">
            <n-icon size="26"><component :is="s.icon" /></n-icon>
          </div>
          <div class="service-info">
            <div class="service-title">
              {{ s.title }}
              <n-tag size="small" :bordered="false" type="success">内置</n-tag>
            </div>
            <div class="service-desc muted">{{ s.desc }}</div>
          </div>
        </div>

        <div class="service-card service-card--fake">
          <div class="service-icon service-icon--warning">
            <n-icon size="26"><ConstructOutline /></n-icon>
          </div>
          <div class="service-info">
            <div class="service-title">
              第三方服务（假服务）
              <n-tag size="small" :bordered="false" type="warning">演示</n-tag>
            </div>
            <div class="service-desc muted">
              市场功能演示：调用假服务并上报埋点 service_button_click。
            </div>
          </div>
          <div class="service-action">
            <n-button type="primary" :loading="calling" @click="callFakeService">
              <template #icon><n-icon><ExtensionPuzzleOutline /></n-icon></template>
              调用假服务
            </n-button>
          </div>
        </div>
      </div>

      <n-alert type="info" :bordered="false" style="margin-top: 20px">
        <template #icon><n-icon><NewspaperOutline /></n-icon></template>
        服务市场为预留模块：后续可在此接入通知源扩展、导出服务等第三方能力。当前页面为演示占位。
      </n-alert>
    </n-card>
  </div>
</template>

<style scoped>
.market {
  display: flex;
  flex-direction: column;
  gap: 20px;
}
.section-sub {
  font-size: 12px;
  font-weight: 400;
}
.service-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(300px, 1fr));
  gap: 16px;
}
.service-card {
  display: flex;
  align-items: flex-start;
  gap: 14px;
  padding: 20px;
  border: 1px solid var(--border);
  border-radius: 12px;
  background: var(--bg-card);
  transition: box-shadow 0.15s ease, transform 0.15s ease;
}
.service-card:hover {
  box-shadow: var(--shadow-2);
  transform: translateY(-2px);
}
.service-card--fake {
  grid-column: 1 / -1;
  align-items: center;
  border-color: var(--warning);
  background: var(--warning-soft);
}
.service-icon {
  flex-shrink: 0;
  width: 52px;
  height: 52px;
  border-radius: 14px;
  display: flex;
  align-items: center;
  justify-content: center;
}
.service-icon--primary {
  background: var(--primary-soft);
  color: var(--primary);
}
.service-icon--violet {
  background: rgba(139, 92, 246, 0.12);
  color: var(--violet);
}
.service-icon--info {
  background: var(--info-soft);
  color: var(--info);
}
.service-icon--success {
  background: var(--success-soft);
  color: var(--success);
}
.service-icon--warning {
  background: var(--warning-soft);
  color: var(--warning);
}
.service-info {
  min-width: 0;
}
.service-title {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 15px;
  font-weight: 600;
  color: var(--text-1);
}
.service-desc {
  font-size: 13px;
  margin-top: 6px;
  line-height: 1.6;
}
.service-action {
  margin-left: auto;
}
</style>