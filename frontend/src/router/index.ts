import { createRouter, createWebHistory } from 'vue-router'
// 首页（首屏）静态导入，保证首屏即时渲染；其余页面路由懒加载（B21.T4，
// 代码分割，首屏仅加载首页与公共库，8 页面按需异步加载，降低首屏 JS 体积）。
import HomeView from '../views/HomeView.vue'
import { trackEvent } from '../api/events'

const routes = [
  { path: '/', component: HomeView, meta: { title: '首页', subtitle: '数据概览与快捷入口' } },
  {
    path: '/notices',
    component: () => import('../views/NoticesView.vue'),
    meta: { title: '通知浏览', subtitle: '抓取 · 提取 · 检索与管理' },
  },
  {
    path: '/todos',
    component: () => import('../views/TodosView.vue'),
    meta: { title: '待办清单', subtitle: '行动跟踪与临期提醒' },
  },
  {
    path: '/qa',
    component: () => import('../views/QaView.vue'),
    meta: { title: '智能问答', subtitle: '基于已入库通知的语义问答' },
  },
  {
    path: '/config',
    component: () => import('../views/ConfigView.vue'),
    meta: { title: '系统配置', subtitle: '模型 · 供应商 · 数据源' },
  },
  {
    path: '/subscriptions',
    component: () => import('../views/SubscriptionsView.vue'),
    meta: { title: '订阅管理', subtitle: '关键词订阅与命中跟踪' },
  },
  {
    path: '/market',
    component: () => import('../views/MarketView.vue'),
    meta: { title: '服务市场', subtitle: '可扩展服务' },
  },
  {
    path: '/sources',
    component: () => import('../views/DataSourceCenterView.vue'),
    meta: { title: '数据源', subtitle: '公共数据源库 · 我的数据源 · 改完即存' },
  },
  {
    path: '/notifications',
    component: () => import('../views/NotificationsView.vue'),
    meta: { title: '通知中心', subtitle: '提醒与系统消息' },
  },
]

const router = createRouter({
  history: createWebHistory(),
  routes,
})

let lastRoute = ''
router.afterEach((to) => {
  if (to.path !== lastRoute) {
    lastRoute = to.path
    trackEvent('page_view', undefined, to.path)
  }
})

export default router
