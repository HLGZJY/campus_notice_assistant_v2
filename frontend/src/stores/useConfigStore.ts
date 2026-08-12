import { defineStore } from 'pinia'
import { ref } from 'vue'
import { endpoints } from '../api/endpoints'
import { get, post, put } from '../api/http'

export interface ConfigState {
  values: Record<string, unknown>
  models: string[]
  providers: string[]
  sources: string[]
  disk?: Record<string, unknown>
}

export const useConfigStore = defineStore('config', () => {
  const values = ref<Record<string, unknown>>({})
  const models = ref<string[]>([])
  const providers = ref<string[]>([])
  const sources = ref<string[]>([])
  const disk = ref<Record<string, unknown> | undefined>(undefined)
  const loading = ref(false)

  async function fetchConfig() {
    loading.value = true
    try {
      values.value = await get<Record<string, unknown>>(endpoints.config.get)
    } finally {
      loading.value = false
    }
  }

  async function fetchModels() {
    models.value = await get<string[]>(endpoints.config.models)
  }

  async function fetchProviders() {
    providers.value = await get<string[]>(endpoints.config.providers)
  }

  async function fetchSources() {
    sources.value = await get<string[]>(endpoints.config.sources)
  }

  async function fetchDisk() {
    disk.value = await get<Record<string, unknown>>(endpoints.config.disk)
  }

  async function updateConfig(payload: Record<string, unknown>) {
    // use PUT to update whole config or POST depending on backend
    return await put(endpoints.config.get, payload)
  }

  async function reload() {
    return await post(endpoints.config.reload)
  }

  async function testSource(payload: Record<string, unknown>) {
    return await post(endpoints.config.testSource, payload)
  }

  async function testModel(payload: Record<string, unknown>) {
    return await post(endpoints.config.testModel, payload)
  }

  return {
    values,
    models,
    providers,
    sources,
    disk,
    loading,
    fetchConfig,
    fetchModels,
    fetchProviders,
    fetchSources,
    fetchDisk,
    updateConfig,
    reload,
    testSource,
    testModel,
  }
})
