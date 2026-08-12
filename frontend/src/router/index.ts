import { createRouter, createWebHistory } from 'vue-router'
import HomeView from '../views/HomeView.vue'
import NoticesView from '../views/NoticesView.vue'
import TodosView from '../views/TodosView.vue'
import QaView from '../views/QaView.vue'
import ConfigView from '../views/ConfigView.vue'
import SubscriptionsView from '../views/SubscriptionsView.vue'
import MarketView from '../views/MarketView.vue'
import { trackEvent } from '../api/events'

const routes = [
  { path: '/', component: HomeView, meta: { title: '首页' } },
  { path: '/notices', component: NoticesView, meta: { title: '通知浏览' } },
  { path: '/todos', component: TodosView, meta: { title: '待办清单' } },
  { path: '/qa', component: QaView, meta: { title: '智能问答' } },
  { path: '/config', component: ConfigView, meta: { title: '系统配置' } },
  { path: '/subscriptions', component: SubscriptionsView, meta: { title: '订阅管理' } },
  { path: '/market', component: MarketView, meta: { title: '服务市场' } },
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
