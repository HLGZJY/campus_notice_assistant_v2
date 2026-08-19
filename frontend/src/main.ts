import { createApp } from 'vue'
import { createPinia } from 'pinia'
import naive from 'naive-ui'
import App from './App.vue'
import router from './router'
import './style.css'

const app = createApp(App)

// 全局错误兜底：任何组件渲染/生命周期错误只打印并提示，不再卸载整个组件树
// （Vue 3 默认渲染错误会导致整站白屏，刷新才恢复，误判为"切页空白"）。
app.config.errorHandler = (err, _instance, info) => {
  console.error('[vue-error]', info, err)
}

app.use(createPinia())
app.use(router)
app.use(naive)
app.mount('#app')
