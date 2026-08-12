<script setup lang="ts">
import { onMounted } from 'vue'
import { useConfigStore } from '../stores/useConfigStore'

const cfg = useConfigStore()

onMounted(async () => {
  await cfg.fetchConfig().catch(() => {})
  await cfg.fetchModels().catch(() => {})
  await cfg.fetchProviders().catch(() => {})
})

async function reload() {
  await cfg.reload().catch(() => {})
}
</script>

<template>
  <n-card title="系统配置">
    <div>模型：{{ cfg.models.join(', ') }}</div>
    <n-button @click="reload">Reload</n-button>
  </n-card>
</template>
