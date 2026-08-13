import { defineStore } from 'pinia'
import { ref } from 'vue'
import { endpoints } from '../api/endpoints'
import { get, post, put } from '../api/http'
import type {
  ConfigMutationResult,
  DiskInfo,
  ModelsConfig,
  ModelsView,
  ProviderConfig,
  ProviderView,
  ReloadResult,
  SchoolConfig,
  SourceConfig,
  TestModelRequest,
  TestModelResult,
  TestSourceRequest,
  TestSourceResult,
} from '../api/schema'

export const useConfigStore = defineStore('config', () => {
  const values = ref<Record<string, unknown>>({})
  const models = ref<ModelsView | null>(null)
  const providers = ref<Record<string, ProviderView>>({})
  const sources = ref<SchoolConfig | null>(null)
  const disk = ref<DiskInfo | undefined>(undefined)
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
    models.value = await get<ModelsView>(endpoints.config.models)
  }

  async function fetchProviders() {
    providers.value = await get<Record<string, ProviderView>>(endpoints.config.providers)
  }

  async function fetchSources() {
    sources.value = await get<SchoolConfig>(endpoints.config.sources)
  }

  async function fetchDisk() {
    disk.value = await get<DiskInfo>(endpoints.config.disk)
  }

  async function updateModels(body: ModelsConfig) {
    return await put<ConfigMutationResult>(endpoints.config.models, body)
  }

  async function updateProviders(body: Record<string, ProviderConfig>) {
    return await put<ConfigMutationResult>(endpoints.config.providers, body)
  }

  async function updateSources(body: SourceConfig[]) {
    return await put<ConfigMutationResult>(endpoints.config.sources, body)
  }

  async function reload() {
    return await post<ReloadResult>(endpoints.config.reload)
  }

  async function testSource(payload: TestSourceRequest) {
    return await post<TestSourceResult>(endpoints.config.testSource, payload)
  }

  async function testModel(payload: TestModelRequest) {
    return await post<TestModelResult>(endpoints.config.testModel, payload)
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
    updateModels,
    updateProviders,
    updateSources,
    reload,
    testSource,
    testModel,
  }
})