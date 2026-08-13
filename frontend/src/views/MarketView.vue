<script setup lang="ts">
import { ref } from 'vue'
import { useMessage } from 'naive-ui'
import { trackEvent, EVENT_TYPES } from '../api/events'

const message = useMessage()
const calling = ref(false)

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
  <n-card title="服务市场">
    <n-space vertical size="large">
      <n-empty description="市场功能演示（假服务按钮 + 埋点）" />
      <n-space justify="center">
        <n-button type="primary" :loading="calling" @click="callFakeService">调用假服务</n-button>
      </n-space>
    </n-space>
  </n-card>
</template>