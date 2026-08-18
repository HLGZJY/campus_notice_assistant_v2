<script setup lang="ts">
import { computed, h, onMounted, onUnmounted, ref } from 'vue'
import { NIcon, NBadge, NSpace } from 'naive-ui'
import { useRoute } from 'vue-router'
import {
  AlarmOutline,
  ChatbubbleEllipsesOutline,
  CheckmarkDoneCircleOutline,
  HomeOutline,
  MenuOutline,
  MoonOutline,
  NewspaperOutline,
  NotificationsOutline,
  RocketOutline,
  SchoolOutline,
  SettingsOutline,
  StorefrontOutline,
  SunnyOutline,
} from '@vicons/ionicons5'
import { endpoints } from './api/endpoints'
import { get } from './api/http'
import router from './router'
import { useTaskStore } from './stores/useTaskStore'
import { useThemeStore, type ThemeMode } from './stores/useThemeStore'
import { lightThemeOverrides, darkThemeOverrides } from './theme'
import TaskListDrawer from './components/TaskListDrawer.vue'

const route = useRoute()
const theme = useThemeStore()
const taskStore = useTaskStore()
const pendingCount = ref(0)
const collapsed = ref(false)

function renderIcon(icon: unknown) {
  return () => h(NIcon, { size: 18 }, { default: () => h(icon as never) })
}

const menuItems = [
  { key: '/', label: '首页', icon: HomeOutline },
  { key: '/notices', label: '通知浏览', icon: NewspaperOutline },
  { key: '/todos', label: '待办清单', icon: CheckmarkDoneCircleOutline },
  { key: '/qa', label: '智能问答', icon: ChatbubbleEllipsesOutline },
  { key: '/subscriptions', label: '订阅管理', icon: NotificationsOutline },
  { key: '/config', label: '系统配置', icon: SettingsOutline },
  { key: '/market', label: '服务市场', icon: StorefrontOutline },
]

const menuOptions = computed(() =>
  menuItems.map((item) => ({
    key: item.key,
    icon: renderIcon(item.icon),
    label: () => {
      if (item.key !== '/' || pendingCount.value <= 0) return item.label
      return h(
        NSpace,
        { align: 'center', size: 8 },
        {
          default: () => [
            h('span', null, item.label),
            h(NBadge, { value: pendingCount.value, max: 99, type: 'error' }),
          ],
        }
      )
    },
  }))
)

const themeOptions = [
  { label: '跟随系统', key: 'auto' },
  { label: '浅色模式', key: 'light' },
  { label: '深色模式', key: 'dark' },
]

function onThemeSelect(key: string) {
  theme.setMode(key as ThemeMode)
}

const pageTitle = computed(() => (route.meta.title as string) || '')
const pageSubtitle = computed(() => (route.meta.subtitle as string) || '')

function onMenuUpdate(key: string) {
  router.push(key)
}

async function fetchPendingCount() {
  try {
    pendingCount.value = await get<number>(endpoints.reminders.pendingCount)
  } catch {
    pendingCount.value = 0
  }
}

function updateCollapsed() {
  collapsed.value = window.innerWidth < 1024
}

let timer: ReturnType<typeof setInterval> | undefined

onMounted(() => {
  updateCollapsed()
  window.addEventListener('resize', updateCollapsed)
  theme.listen()
  fetchPendingCount()
  timer = setInterval(fetchPendingCount, 30000)
  taskStore.fetchList()
  taskStore.startGlobalPolling()
})

onUnmounted(() => {
  window.removeEventListener('resize', updateCollapsed)
  if (timer) clearInterval(timer)
  taskStore.stopGlobalPolling()
})
</script>

<template>
  <n-config-provider
    :theme="theme.naiveTheme"
    :theme-overrides="theme.isDark ? darkThemeOverrides : lightThemeOverrides"
    style="height: 100%"
  >
    <n-message-provider>
      <n-dialog-provider>
        <n-layout position="absolute" has-sider style="inset: 0">
          <n-layout-sider
            bordered
            :width="280"
            :collapsed-width="64"
            :collapsed="collapsed"
            collapse-mode="width"
            :show-trigger="false"
          >
            <div class="sider-inner">
              <div class="brand" :class="{ collapsed }">
                <div class="brand-logo">
                  <n-icon size="22"><SchoolOutline /></n-icon>
                </div>
                <transition name="fade">
                  <div v-if="!collapsed" class="brand-text">
                    <div class="brand-title">校园通知智能助手</div>
                    <div class="brand-sub">Campus Notice Assistant</div>
                  </div>
                </transition>
              </div>
              <div class="sider-menu">
                <n-menu
                  :value="route.path"
                  :options="menuOptions"
                  :collapsed="collapsed"
                  :collapsed-width="64"
                  :collapsed-icon-size="22"
                  @update:value="onMenuUpdate"
                />
              </div>
              <TaskListDrawer v-if="!collapsed" />
            </div>
          </n-layout-sider>

          <n-layout :native-scrollbar="false">
            <div class="topbar">
              <div class="topbar-left">
                <n-button quaternary circle @click="collapsed = !collapsed">
                  <template #icon>
                    <n-icon size="20"><MenuOutline /></n-icon>
                  </template>
                </n-button>
                <div class="topbar-titles">
                  <div class="topbar-title">{{ pageTitle }}</div>
                  <div v-if="pageSubtitle" class="topbar-subtitle">{{ pageSubtitle }}</div>
                </div>
              </div>
              <div class="topbar-right">
                <router-link to="/todos" class="pending-link">
                  <n-badge
                    :value="pendingCount"
                    :max="99"
                    :show="pendingCount > 0"
                    type="error"
                    :offset="[-2, 2]"
                  >
                    <n-button quaternary circle title="待处理提醒">
                      <template #icon>
                        <n-icon size="20"><AlarmOutline /></n-icon>
                      </template>
                    </n-button>
                  </n-badge>
                </router-link>
                <n-dropdown
                  :options="themeOptions"
                  :value="theme.mode"
                  trigger="click"
                  @select="onThemeSelect"
                >
                  <n-button quaternary circle title="切换主题">
                    <template #icon>
                      <n-icon size="20">
                        <component :is="theme.isDark ? MoonOutline : SunnyOutline" />
                      </n-icon>
                    </template>
                  </n-button>
                </n-dropdown>
              </div>
            </div>

            <n-layout-content content-style="padding: 24px 24px 48px" :native-scrollbar="false">
              <div class="page-wrap">
                <router-view v-slot="{ Component }">
                  <transition name="page" mode="out-in">
                    <component :is="Component" :key="route.path" />
                  </transition>
                </router-view>
              </div>
            </n-layout-content>
          </n-layout>
        </n-layout>
      </n-dialog-provider>
    </n-message-provider>
  </n-config-provider>
</template>

<style scoped>
.sider-inner {
  display: flex;
  flex-direction: column;
  height: 100%;
  overflow: hidden;
}
.sider-menu {
  flex: 1;
  overflow-y: auto;
  overflow-x: hidden;
  min-height: 0;
}
.brand {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 20px 16px;
  height: 72px;
  box-sizing: border-box;
  overflow: hidden;
}
.brand.collapsed {
  justify-content: center;
  padding: 20px 0;
}
.brand-logo {
  flex-shrink: 0;
  width: 36px;
  height: 36px;
  border-radius: 10px;
  background: var(--gradient-brand);
  color: #fff;
  display: flex;
  align-items: center;
  justify-content: center;
  box-shadow: 0 4px 12px -2px rgba(99, 102, 241, 0.5);
}
.brand-text {
  min-width: 0;
}
.brand-title {
  font-size: 15px;
  font-weight: 700;
  color: var(--text-1);
  white-space: nowrap;
}
.brand-sub {
  font-size: 10px;
  color: var(--text-3);
  letter-spacing: 0.4px;
  white-space: nowrap;
}
.fade-enter-active,
.fade-leave-active {
  transition: opacity 0.15s ease;
}
.fade-enter-from,
.fade-leave-to {
  opacity: 0;
}

.topbar {
  position: sticky;
  top: 0;
  z-index: 10;
  height: 60px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0 20px;
  background: var(--topbar-bg);
  backdrop-filter: blur(8px);
  -webkit-backdrop-filter: blur(8px);
  border-bottom: 1px solid var(--border);
}
.topbar-left {
  display: flex;
  align-items: center;
  gap: 12px;
  min-width: 0;
}
.topbar-titles {
  min-width: 0;
}
.topbar-title {
  font-size: 16px;
  font-weight: 700;
  color: var(--text-1);
  line-height: 1.2;
}
.topbar-subtitle {
  font-size: 12px;
  color: var(--text-3);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.topbar-right {
  display: flex;
  align-items: center;
  gap: 4px;
}
.pending-link {
  display: inline-flex;
  border-radius: 50%;
}
</style>