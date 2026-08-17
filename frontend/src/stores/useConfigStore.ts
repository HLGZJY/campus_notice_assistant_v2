import { defineStore } from 'pinia'
import { ref } from 'vue'
import { endpoints } from '../api/endpoints'
import { get, post, put } from '../api/http'
import type {
  ApiKeyRequest,
  ApiKeyResult,
  ConfigMutationResult,
  CrawlConfig,
  DiskInfo,
  ExtractConfig,
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
  TokenUsageSummary,
} from '../api/schema'

export const useConfigStore = defineStore('config', () => {
  const values = ref<Record<string, unknown>>({})
  const models = ref<ModelsView | null>(null)
  const providers = ref<Record<string, ProviderView>>({})
  const sources = ref<SchoolConfig | null>(null)
  const crawl = ref<CrawlConfig | null>(null)
  const extract = ref<ExtractConfig | null>(null)
  const disk = ref<DiskInfo | undefined>(undefined)
  const tokenUsage = ref<TokenUsageSummary | null>(null)
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

  async function fetchCrawl() {
    crawl.value = await get<CrawlConfig>(endpoints.config.crawl)
  }

  async function fetchExtract() {
    extract.value = await get<ExtractConfig>(endpoints.config.extract)
  }

  async function fetchDisk() {
    disk.value = await get<DiskInfo>(endpoints.config.disk)
  }

  async function fetchTokenUsage(days: number) {
    const summary = await get<TokenUsageSummary>(endpoints.usage.tokens, { days })
    tokenUsage.value = summary
    return summary
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

  async function updateCrawl(body: CrawlConfig) {
    return await put<ConfigMutationResult>(endpoints.config.crawl, body)
  }

  async function updateExtract(body: ExtractConfig) {
    return await put<ConfigMutationResult>(endpoints.config.extract, body)
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

  async function saveApiKey(providerName: string, apiKey: string) {
    const body: ApiKeyRequest = { api_key: apiKey }
    return await put<ApiKeyResult>(endpoints.config.apiKey(providerName), body)
  }

  return {
    values,
    models,
    providers,
    sources,
    crawl,
    extract,
    disk,
    tokenUsage,
    loading,
    fetchConfig,
    fetchModels,
    fetchProviders,
    fetchSources,
    fetchCrawl,
    fetchExtract,
    fetchDisk,
    fetchTokenUsage,
    updateModels,
    updateProviders,
    updateSources,
    updateCrawl,
    updateExtract,
    reload,
    testSource,
    testModel,
    saveApiKey,
  }
})