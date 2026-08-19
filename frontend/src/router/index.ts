import { createRouter, createWebHistory } from 'vue-router'
import HomeView from '../views/HomeView.vue'
import NoticesView from '../views/NoticesView.vue'
import TodosView from '../views/TodosView.vue'
import QaView from '../views/QaView.vue'
import ConfigView from '../views/ConfigView.vue'
import SubscriptionsView from '../views/SubscriptionsView.vue'
import MarketView from '../views/MarketView.vue'
import DataSourceCenterView from '../views/DataSourceCenterView.vue'
import { trackEvent } from '../api/events'

const routes = [
  { path: '/', component: HomeView, meta: { title: '首页', subtitle: '数据概览与快捷入口' } },
  { path: '/notices', component: NoticesView, meta: { title: '通知浏览', subtitle: '抓取 · 提取 · 检索与管理' } },
  { path: '/todos', component: TodosView, meta: { title: '待办清单', subtitle: '行动跟踪与临期提醒' } },
  { path: '/qa', component: QaView, meta: { title: '智能问答', subtitle: '基于已入库通知的语义问答' } },
  { path: '/config', component: ConfigView, meta: { title: '系统配置', subtitle: '模型 · 供应商 · 数据源' } },
  { path: '/subscriptions', component: SubscriptionsView, meta: { title: '订阅管理', subtitle: '关键词订阅与命中跟踪' } },
  { path: '/market', component: MarketView, meta: { title: '服务市场', subtitle: '可扩展服务' } },
  { path: '/sources', component: DataSourceCenterView, meta: { title: '数据源中心', subtitle: '公共数据源 · 搜索筛选 · 一键选用' } },
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
