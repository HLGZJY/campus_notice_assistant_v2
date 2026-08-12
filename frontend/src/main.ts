import { createApp } from 'vue'
import { createPinia } from 'pinia'
import { createDiscreteApi } from 'naive-ui'
import App from './App.vue'
import router from './router'

const { message, dialog } = createDiscreteApi(['message', 'dialog'])

const app = createApp(App)
app.use(createPinia())
app.use(router)
app.provide('message', message)
app.provide('dialog', dialog)
app.mount('#app')
