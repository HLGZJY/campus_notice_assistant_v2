<script setup lang="ts">
import { ref, computed, watch, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { useDialog, useMessage } from 'naive-ui'
import {
  CheckmarkCircleOutline,
  ChevronDownOutline,
  ChevronUpOutline,
  CloseCircleOutline,
  CloudDownloadOutline,
  DiscOutline,
  FilterOutline,
  InformationCircleOutline,
  KeyOutline,
  PaperPlaneOutline,
  PencilOutline,
  PulseOutline,
  ReloadOutline,
  ServerOutline,
  SettingsOutline,
} from '@vicons/ionicons5'
import { useConfigStore } from '../stores/useConfigStore'
import { endpoints } from '../api/endpoints'
import { get as httpGet } from '../api/http'
import StatCard from '../components/StatCard.vue'
import type {
  ConfigMutationResult,
  CrawlConfig,
  ExtractConfig,
  ModelProfileView,
  ModelsConfig,
  ProviderConfig,
  ReloadResult,
  TokenUsageRow,
  TokenUsageSummary,
  UpdateCheckResult,
} from '../api/schema'

const message = useMessage()
const dialog = useDialog()
const router = useRouter()
const cfg = useConfigStore()
const activeTab = ref('models')

const modelsDraft = ref<ModelsConfig | null>(null)
const providerDraft = ref<Record<string, ProviderConfig>>({})
const providerKeyDraft = ref<Record<string, string>>({})
const savingKey = ref<Record<string, boolean>>({})
const pendingModel = ref<Record<string, string>>({})
const advancedOpen = ref<Record<string, boolean>>({})
const testResult = ref<Record<string, { ok: boolean; latency?: number; error?: string }>>({})
const crawlDraft = ref<CrawlConfig | null>(null)
const extractDraft = ref<ExtractConfig | null>(null)
const testModelInput = ref<Record<string, string>>({})
const testBusy = ref<Record<string, boolean>>({})
const saving = ref(false)
const reloading = ref(false)
const loading = ref(false)
const providerExpanded = ref<Record<string, boolean>>({})
const crawlExpanded = ref(false)
const extractExpanded = ref(false)

// ---- Token 用量 Tab（GET /usage/tokens，阶段 7 遗留项落地） ----
const usageDays = ref(7)
const usageLoading = ref(false)
const usageSummary = ref<TokenUsageSummary | null>(null)

async function loadUsage() {
  usageLoading.value = true
  try {
    usageSummary.value = await cfg.fetchTokenUsage(usageDays.value)
  } catch {
    message.error('Token 用量加载失败')
  } finally {
    usageLoading.value = false
  }
}

const usageRows = computed<TokenUsageRow[]>(() => usageSummary.value?.rows ?? [])
const usageTotal = computed(() => usageSummary.value?.total ?? {})
const usageColumns = [
  { title: '任务', key: 'task_label' },
  { title: '供应商', key: 'provider' },
  { title: '模型', key: 'model' },
  { title: '调用', key: 'calls' },
  { title: '成功', key: 'success' },
  { title: '失败', key: 'failed' },
  { title: '重试', key: 'retry_calls' },
  { title: '输入 tokens', key: 'input_tokens' },
  { title: '输出 tokens', key: 'output_tokens' },
]

watch(usageDays, () => loadUsage())
watch(activeTab, (tab) => {
  if (tab === 'usage') loadUsage()
})

// ---- 检查更新 Tab（GET /update/check，打包发布方案 Step 5） ----
const updateChecking = ref(false)
const updateResult = ref<UpdateCheckResult | null>(null)

async function checkUpdate() {
  updateChecking.value = true
  try {
    updateResult.value = await httpGet<UpdateCheckResult>(endpoints.update.check)
  } catch {
    message.error('检查更新请求失败（后端不可达）')
  } finally {
    updateChecking.value = false
  }
}

function formatSize(bytes: number): string {
  if (!bytes) return '—'
  if (bytes >= 1024 * 1024 * 1024) return `${(bytes / 1024 / 1024 / 1024).toFixed(2)} GB`
  if (bytes >= 1024 * 1024) return `${(bytes / 1024 / 1024).toFixed(1)} MB`
  return `${Math.round(bytes / 1024)} KB`
}

function openExternal(url: string) {
  window.open(url, '_blank', 'noopener')
}

function isInteractiveTarget(e: MouseEvent): boolean {
  const el = e.target as HTMLElement | null
  if (!el) return false
  return !!el.closest(
    'button, a, input, textarea, select, option, .n-button, .n-input, .n-base-select, .n-select, .n-switch, .n-input-number, .n-checkbox, .n-radio, .n-tag, .n-slider'
  )
}

function provShow(name: string) {
  return !!providerExpanded.value[name]
}
function toggleProvider(name: string, e: MouseEvent) {
  if (isInteractiveTarget(e)) {
    providerExpanded.value[name] = true
    return
  }
  providerExpanded.value[name] = !providerExpanded.value[name]
}
function toggleCrawl(e: MouseEvent) {
  if (isInteractiveTarget(e)) {
    crawlExpanded.value = true
    return
  }
  crawlExpanded.value = !crawlExpanded.value
}
function toggleExtract(e: MouseEvent) {
  if (isInteractiveTarget(e)) {
    extractExpanded.value = true
    return
  }
  extractExpanded.value = !extractExpanded.value
}

const providerOptions = computed(() =>
  Object.entries(cfg.providers || {}).map(([k, p]) => ({ label: p.display_name || p.name, value: k }))
)

const taskKeys = ['extraction', 'qa', 'todo', 'embedding'] as const
type TaskKey = (typeof taskKeys)[number]

type SelectValue = string | number | Array<string | number> | null

const taskLabels: Record<string, string> = {
  extraction: '信息提取',
  qa: '智能问答',
  todo: '待办生成',
  embedding: '向量嵌入',
}

// 按供应商类型内置的常用模型建议（与 config/defaults.py 对齐）；手动输入自定义模型不受此表限制
const MODEL_PRESETS: Record<string, string[]> = {
  bailian: ['qwen3.7-max-2026-05-20', 'qwen3.7-flash-2026-07-15', 'qwen3.7-turbo', 'qwen3.7-max'],
  'opencode-zen': ['kimi-k2.7-code', 'deepseek-v4-pro', 'kimi-k2.5-turbo'],
  local: ['models/bge-small-zh-v1.5', 'sentence-transformers/all-MiniLM-L6-v2'],
}

const TYPE_LABELS: Record<string, string> = {
  bailian: 'Bailian',
  'opencode-zen': 'OpenCode Zen',
  local: 'Local',
}

onMounted(async () => {
  await load()
})

async function load() {
  loading.value = true
  try {
    await Promise.all([
      cfg.fetchModels().catch(() => {}),
      cfg.fetchProviders().catch(() => {}),
      cfg.fetchSources().catch(() => {}),
      cfg.fetchCrawl().catch(() => {}),
      cfg.fetchExtract().catch(() => {}),
      cfg.fetchDisk().catch(() => {}),
    ])
    initDrafts()
  } finally {
    loading.value = false
  }
}

function initDrafts() {
  if (cfg.models) {
    modelsDraft.value = {
      extraction: { provider: cfg.models.extraction.provider, models: [...cfg.models.extraction.models] },
      qa: { provider: cfg.models.qa.provider, models: [...cfg.models.qa.models] },
      todo: { provider: cfg.models.todo.provider, models: [...cfg.models.todo.models] },
      embedding: { provider: cfg.models.embedding.provider, models: [...cfg.models.embedding.models] },
    }
    testModelInput.value = {}
    for (const profile of Object.values(cfg.models) as ModelProfileView[]) {
      if (profile.provider && !testModelInput.value[profile.provider]) {
        testModelInput.value[profile.provider] = profile.models?.[0] ?? ''
      }
    }
  }
  providerDraft.value = {}
  for (const [name, p] of Object.entries(cfg.providers || {})) {
    providerDraft.value[name] = {
      name: p.name,
      display_name: p.display_name ?? p.name,
      base_url: p.base_url,
      api_key_env: p.api_key_env,
      models: [...(p.models ?? [])],
      type: p.type ?? '',
    }
  }
  providerKeyDraft.value = {}
  pendingModel.value = {}
  testResult.value = {}
  crawlDraft.value = cfg.crawl ? { ...cfg.crawl } : null
  extractDraft.value = cfg.extract ? { ...cfg.extract } : null
  providerExpanded.value = {}
  crawlExpanded.value = false
  extractExpanded.value = false
}

function handleMutation(res: ConfigMutationResult, okText = '保存成功') {
  if (res.ok) {
    message.success(res.message || okText)
  } else {
    message.error(res.error || '保存失败')
  }
}

async function saveModels() {
  if (!modelsDraft.value) return
  saving.value = true
  try {
    handleMutation(await cfg.updateModels(modelsDraft.value))
  } catch (e) {
    message.error(e instanceof Error ? e.message : String(e))
  } finally {
    saving.value = false
  }
}

async function saveProviders() {
  saving.value = true
  try {
    handleMutation(await cfg.updateProviders(providerDraft.value))
  } catch (e) {
    message.error(e instanceof Error ? e.message : String(e))
  } finally {
    saving.value = false
  }
}

// ---------- 任务候选模型列表操作 ----------

function modelOptionsFor(provider: string) {
  return (cfg.providers?.[provider]?.models ?? []).map((m) => ({ label: m, value: m }))
}

function addTaskModel(task: TaskKey) {
  modelsDraft.value![task].models.push('')
}

function removeTaskModel(task: TaskKey, idx: number) {
  const arr = modelsDraft.value![task].models
  if (arr.length <= 1) {
    message.warning('至少保留一个候选模型')
    return
  }
  arr.splice(idx, 1)
}

function moveTaskModel(task: TaskKey, idx: number, dir: number) {
  const arr = modelsDraft.value![task].models
  const j = idx + dir
  if (j < 0 || j >= arr.length) return
  const tmp = arr[idx]
  arr[idx] = arr[j]
  arr[j] = tmp
}

function setTaskModel(task: TaskKey, idx: number, v: SelectValue) {
  const arr = modelsDraft.value![task].models
  const val = Array.isArray(v) ? String(v[0] ?? '') : (v ?? '').toString()
  if (arr[idx] !== undefined) arr[idx] = val
}

async function testModelRow(provider: string, model: string) {
  if (!model.trim()) {
    message.warning('该候选模型为空，请先填写模型名')
    return
  }
  const id = `${provider}::${model}`
  testBusy.value[id] = true
  try {
    const res = await cfg.testModel({ provider, model, timeout: 30 })
    if (res.ok) {
      message.success(`连接成功（${res.latency_ms}ms）${res.completion ? `：${res.completion}` : ''}`)
    } else {
      message.error(res.error || '连接失败')
    }
  } catch (e) {
    message.error(e instanceof Error ? e.message : String(e))
  } finally {
    testBusy.value[id] = false
  }
}

// ---------- 供应商增删 / 改名 / API Key ----------

function typeLabel(t?: string) {
  return TYPE_LABELS[t ?? ''] ?? (t || 'Custom')
}

function badgeType(t?: string) {
  return t === 'local' ? 'success' : t === 'custom' || !t ? 'default' : 'info'
}

function keyStatus(name: string) {
  return !!cfg.providers?.[name]?.api_key_status
}

function addProvider() {
  let i = 1
  let name = 'new-provider'
  while (providerDraft.value[name]) {
    i += 1
    name = `new-provider-${i}`
  }
  providerDraft.value[name] = {
    name,
    display_name: `新供应商 ${i}`,
    base_url: '',
    api_key_env: '',
    type: '',
    models: [],
  }
  providerExpanded.value[name] = true
}

function providerDisplay(name: string) {
  return providerDraft.value[name]?.display_name || name
}

function confirmRemoveProvider(name: string) {
  const used = (Object.values(modelsDraft.value ?? {}) as ModelProfileView[]).some(
    (p) => p.provider === name
  )
  if (used) {
    message.error(`任务模型仍在引用供应商「${providerDisplay(name)}」，请先在「模型」tab 更换后再删除`)
    return
  }
  dialog.warning({
    title: '删除供应商',
    content: `确定删除供应商「${providerDisplay(name)}」？删除后需点「保存供应商配置」才会生效。`,
    positiveText: '删除',
    negativeText: '取消',
    onPositiveClick: () => {
      delete providerDraft.value[name]
      delete providerKeyDraft.value[name]
      delete pendingModel.value[name]
      delete testModelInput.value[name]
      delete testResult.value[name]
      delete providerExpanded.value[name]
      message.success('已删除，保存供应商配置后生效')
    },
  })
}

// ---------- 可用模型（下拉建议 + 标签） ----------

function toggleAdvanced(name: string) {
  advancedOpen.value[name] = !advancedOpen.value[name]
}

function modelAddOptions(name: string) {
  const p = providerDraft.value[name]
  if (!p) return []
  const seen = new Set<string>()
  const out: { label: string; value: string }[] = []
  for (const m of [...(MODEL_PRESETS[p.type] ?? []), ...p.models]) {
    if (!seen.has(m)) {
      seen.add(m)
      out.push({ label: m, value: m })
    }
  }
  return out
}

function addProviderModel(name: string) {
  const p = providerDraft.value[name]
  const m = (pendingModel.value[name] || '').trim()
  if (!m) {
    message.warning('请选择或输入一个模型名')
    return
  }
  if (p.models.includes(m)) {
    message.warning('该模型已在列表中')
    return
  }
  p.models.push(m)
  pendingModel.value[name] = ''
}

function removeProviderModel(name: string, idx: number) {
  providerDraft.value[name].models.splice(idx, 1)
}

function modelTestOptions(name: string) {
  const p = providerDraft.value[name]
  const pool = p?.models.length ? p.models : (MODEL_PRESETS[p.type] ?? [])
  return pool.map((m) => ({ label: m, value: m }))
}

async function saveProviderKey(name: string) {
  const key = (providerKeyDraft.value[name] || '').trim()
  if (!key) {
    message.warning('请先粘贴 API Key')
    return
  }
  savingKey.value[name] = true
  try {
    const res = await cfg.saveApiKey(name, key)
    if (res.ok) {
      providerKeyDraft.value[name] = ''
      message.success(`已写入 .env（${res.env_var}）${res.env_path ? `：${res.env_path}` : ''}`)
      await cfg.fetchProviders()
      const p = providerDraft.value[name]
      if (p && cfg.providers?.[name]) {
        p.api_key_env = cfg.providers[name].api_key_env
      }
    } else {
      message.error(res.error || '写入失败')
    }
  } catch (e) {
    message.error(e instanceof Error ? e.message : String(e))
  } finally {
    savingKey.value[name] = false
  }
}

async function testProvider(name: string) {
  const model = (testModelInput.value[name] || '').trim()
  if (!model) {
    message.warning('请先选择测试用的模型名')
    return
  }
  testBusy.value[name] = true
  try {
    const res = await cfg.testModel({ provider: name, model, timeout: 30 })
    if (res.ok) {
      testResult.value[name] = { ok: true, latency: res.latency_ms }
      message.success(`连接成功（${res.latency_ms}ms）${res.completion ? `：${res.completion}` : ''}`)
    } else {
      testResult.value[name] = { ok: false, error: res.error || '连接失败' }
      message.error(res.error || '连接失败')
    }
  } catch (e) {
    testResult.value[name] = { ok: false, error: e instanceof Error ? e.message : String(e) }
    message.error(e instanceof Error ? e.message : String(e))
  } finally {
    testBusy.value[name] = false
  }
}

async function saveCrawl() {
  if (!crawlDraft.value) return
  saving.value = true
  try {
    handleMutation(await cfg.updateCrawl(crawlDraft.value))
  } catch (e) {
    message.error(e instanceof Error ? e.message : String(e))
  } finally {
    saving.value = false
  }
}

async function saveExtract() {
  if (!extractDraft.value) return
  saving.value = true
  try {
    handleMutation(await cfg.updateExtract(extractDraft.value))
  } catch (e) {
    message.error(e instanceof Error ? e.message : String(e))
  } finally {
    saving.value = false
  }
}

async function reloadConfig() {
  reloading.value = true
  try {
    const res: ReloadResult = await cfg.reload()
    if (res.ok) {
      message.success(`配置已重载（version ${res.version ?? '?'}）`)
    } else {
      message.error(res.error || '重载失败')
    }
    await load()
  } catch (e) {
    message.error(e instanceof Error ? e.message : String(e))
  } finally {
    reloading.value = false
  }
}

function goSources() {
  router.push('/sources')
}
</script>

<template>
  <n-card :bordered="false">
    <template #header>
      <div class="section-title">
        <n-icon size="18" color="var(--primary)"><SettingsOutline /></n-icon>
        系统配置
      </div>
    </template>
    <n-spin :show="loading">
      <n-tabs v-model:value="activeTab" type="line" animated>
        <n-tab-pane name="models" tab="模型">
          <n-alert type="info" :bordered="false" style="margin-bottom: 12px">
            每个任务按序尝试候选模型（同供应商内）：前一个模型失败（配额不足/网络/5xx/404）时自动切换下一个。
            模型名下拉候选来自「供应商」tab 维护的可选模型列表，也可直接输入自定义模型名。
          </n-alert>
          <n-form label-placement="left" label-width="90" v-if="modelsDraft">
            <n-form-item v-for="task in taskKeys" :key="task" :label="taskLabels[task]">
              <n-space vertical>
                <n-space>
                  <n-select
                    v-model:value="modelsDraft[task].provider"
                    :options="providerOptions"
                    placeholder="Provider"
                    style="width: 200px"
                  />
                </n-space>
                <n-space v-for="(m, idx) in modelsDraft[task].models" :key="idx">
                  <n-select
                    :value="modelsDraft[task].models[idx]"
                    @update:value="(v: SelectValue) => setTaskModel(task, idx, v)"
                    :options="modelOptionsFor(modelsDraft[task].provider)"
                    filterable
                    tag
                    placeholder="模型名（可输入自定义）"
                    style="width: 300px"
                  />
                  <n-button size="small" quaternary :disabled="idx === 0" @click="moveTaskModel(task, idx, -1)">
                    ↑
                  </n-button>
                  <n-button
                    size="small"
                    quaternary
                    :disabled="idx === modelsDraft[task].models.length - 1"
                    @click="moveTaskModel(task, idx, 1)"
                  >
                    ↓
                  </n-button>
                  <n-button size="small" quaternary type="error" @click="removeTaskModel(task, idx)">
                    移除
                  </n-button>
                  <n-button
                    size="small"
                    secondary
                    :loading="testBusy[`${modelsDraft[task].provider}::${m}`]"
                    @click="testModelRow(modelsDraft[task].provider, m)"
                  >
                    测试
                  </n-button>
                </n-space>
                <n-space>
                  <n-button size="small" secondary @click="addTaskModel(task)">添加候选模型</n-button>
                  <span style="font-size: 12px; color: #999">先尝试在上，失败自动切向下一个</span>
                </n-space>
              </n-space>
            </n-form-item>
            <n-button type="primary" :loading="saving" @click="saveModels">保存模型配置</n-button>
          </n-form>
        </n-tab-pane>

        <n-tab-pane name="providers" tab="供应商">
          <n-space vertical size="large">
            <n-card
              v-for="(p, name) in providerDraft"
              :key="name"
              size="small"
              class="collapsible-card"
              @click="toggleProvider(name, $event)"
            >
              <template #header>
                <div class="card-header-bar">
                  <n-icon size="16" color="var(--text-3)"><ServerOutline /></n-icon>
                  <span class="card-header-title">{{ p.display_name || name }}</span>
                  <n-tag size="small" :bordered="false" :type="badgeType(p.type)">{{ typeLabel(p.type) }}</n-tag>
                  <n-tag size="small" :bordered="false" :type="keyStatus(name) ? 'success' : 'warning'">
                    {{ keyStatus(name) ? '已就绪' : '未就绪' }}
                  </n-tag>
                  <span class="header-spacer" />
                  <n-button size="small" quaternary type="error" @click.stop="confirmRemoveProvider(name)">删除</n-button>
                  <n-icon size="14" color="var(--text-3)">
                    <component :is="provShow(name) ? ChevronUpOutline : ChevronDownOutline" />
                  </n-icon>
                </div>
              </template>
              <n-collapse-transition :show="provShow(name)">
              <n-form label-placement="left" label-width="100">
                <n-form-item label="实例名">
                  <n-input v-model:value="p.display_name" placeholder="实例名" style="width: 240px" />
                </n-form-item>
                <n-form-item label="Base URL">
                  <n-input v-model:value="p.base_url" placeholder="https://api.example.com" />
                </n-form-item>
                <n-form-item label="API Key">
                  <n-space align="center">
                    <n-input
                      v-model:value="providerKeyDraft[name]"
                      type="password"
                      show-password-on="click"
                      placeholder="粘贴 API Key，保存后写入 .env"
                      style="width: 300px"
                    />
                    <n-button
                      size="small"
                      secondary
                      type="primary"
                      :loading="savingKey[name]"
                      @click="saveProviderKey(name)"
                    >
                      <template #icon><n-icon><KeyOutline /></n-icon></template>
                      保存密钥
                    </n-button>
                    <span class="key-dot" :class="keyStatus(name) ? 'ok' : 'bad'" />
                    <span style="font-size: 12px; color: #999">{{ keyStatus(name) ? '已就绪' : '未就绪' }}</span>
                  </n-space>
                  <template #feedback>
                    <span class="muted">密钥仅写入项目根 .env（已忽略 Git，即时生效）</span>
                    <n-tooltip trigger="hover" placement="right">
                      <template #trigger>
                        <span class="help-icon"><n-icon size="14"><InformationCircleOutline /></n-icon></span>
                      </template>
                      保存后自动 upsert 到项目根目录 .env，不落库、不进 YAML；若环境变量名为空会自动生成
                      &lt;标识&gt;_API_KEY。写入后进程内立即生效，无需重启。
                    </n-tooltip>
                  </template>
                </n-form-item>
                <n-form-item label="可用模型">
                  <n-space vertical style="width: 100%">
                    <n-space>
                      <n-select
                        v-model:value="pendingModel[name]"
                        filterable
                        tag
                        :options="modelAddOptions(name)"
                        placeholder="下拉选择或输入模型名"
                        style="width: 280px"
                      />
                      <n-button size="small" secondary @click="addProviderModel(name)">添加</n-button>
                    </n-space>
                    <n-space v-if="p.models.length">
                      <n-tag
                        v-for="(m, idx) in p.models"
                        :key="m"
                        closable
                        size="small"
                        @close="removeProviderModel(name, idx)"
                      >
                        {{ m }}
                      </n-tag>
                    </n-space>
                    <span v-else style="font-size: 12px; color: #999">
                      暂无模型；此处维护的模型会作为「模型」tab 的下拉候选
                    </span>
                  </n-space>
                </n-form-item>
                <n-button size="small" quaternary style="margin: 2px 0 8px" @click="toggleAdvanced(name)">
                    <template #icon><n-icon><component :is="advancedOpen[name] ? ChevronUpOutline : ChevronDownOutline" /></n-icon></template>
                    {{ advancedOpen[name] ? '收起高级选项' : '连通性测试 · 环境变量 · 标识' }}
                  </n-button>
                  <div v-show="advancedOpen[name]">
                    <n-form label-placement="left" label-width="100">
                      <n-form-item label="连通性测试">
                        <n-space vertical>
                          <n-space>
                            <n-select
                              v-model:value="testModelInput[name]"
                              filterable
                              :options="modelTestOptions(name)"
                              placeholder="测试用的模型名"
                              style="width: 280px"
                            />
                            <n-button
                              size="small"
                              secondary
                              type="primary"
                              :loading="testBusy[name]"
                              @click="testProvider(name)"
                            >
                              <template #icon><n-icon><PulseOutline /></n-icon></template>
                              开始测试
                            </n-button>
                          </n-space>
                          <span
                            v-if="testResult[name]"
                            :class="testResult[name].ok ? 'test-ok' : 'test-bad'"
                          >
                            <n-icon v-if="testResult[name].ok"><CheckmarkCircleOutline /></n-icon>
                            <n-icon v-else><CloseCircleOutline /></n-icon>
                            {{ testResult[name].ok ? `连接成功（${testResult[name].latency}ms）` : testResult[name].error }}
                          </span>
                        </n-space>
                      </n-form-item>
                      <n-form-item label="环境变量名">
                        <n-input v-model:value="p.api_key_env" placeholder="OPENAI_API_KEY" style="width: 240px" />
                        <template #feedback>Key 写入 .env 使用的变量名；留空时保存 Key 自动生成 &lt;标识&gt;_API_KEY</template>
                      </n-form-item>
                      <n-form-item label="标识">
                        <n-input :value="name" disabled style="width: 240px" />
                        <template #feedback>唯一标识，任务模型通过它引用本供应商，不可在页面修改（如需改名请编辑 YAML）</template>
                      </n-form-item>
                    </n-form>
                  </div>
              </n-form>
              </n-collapse-transition>
            </n-card>
            <n-space>
              <n-button secondary @click="addProvider">添加供应商</n-button>
              <n-button type="primary" :loading="saving" @click="saveProviders">保存供应商配置</n-button>
            </n-space>
          </n-space>
        </n-tab-pane>

        <n-tab-pane name="sources" tab="数据源">
          <n-alert type="info" :show-icon="true">
            <template #default>
              数据源管理已移至「数据源中心」，增删改与参数调整统一在那里完成，改完即生效
              <n-button size="small" quaternary type="primary" @click="goSources" style="margin-left: 8px">
                前往数据源中心 →
              </n-button>
            </template>
          </n-alert>
        </n-tab-pane>

        <n-tab-pane name="crawl" tab="抓取与提取">
          <n-space vertical size="large">
            <n-card
              size="small"
              v-if="crawlDraft"
              class="collapsible-card"
              @click="toggleCrawl($event)"
            >
              <template #header>
                <div class="card-header-bar">
                  <n-icon size="16" color="var(--text-3)"><CloudDownloadOutline /></n-icon>
                  <span class="card-header-title">全局抓取参数</span>
                  <span class="header-spacer" />
                  <n-icon size="14" color="var(--text-3)">
                    <component :is="crawlExpanded ? ChevronUpOutline : ChevronDownOutline" />
                  </n-icon>
                </div>
              </template>
              <n-collapse-transition :show="crawlExpanded">
              <n-form label-placement="left" label-width="160">
                <n-form-item label="抓取间隔（分钟）">
                  <n-input-number v-model:value="crawlDraft.interval_minutes" :min="1" style="width: 120px" />
                </n-form-item>
                <n-form-item label="早停（增量）">
                  <n-switch v-model:value="crawlDraft.stop_when_caught_up" />
                  <span style="margin-left: 8px; color: #999; font-size: 12px">某页全部是已知通知时停止翻页（仅增量模式生效）</span>
                </n-form-item>
                <n-form-item label="请求超时（秒）">
                  <n-input-number v-model:value="crawlDraft.request_timeout" :min="3" style="width: 120px" />
                </n-form-item>
                <n-form-item label="失败重试次数">
                  <n-input-number v-model:value="crawlDraft.retry_times" :min="0" :max="5" style="width: 120px" />
                </n-form-item>
                <n-form-item label="详情并发数">
                  <n-input-number v-model:value="crawlDraft.concurrency" :min="1" :max="8" style="width: 120px" />
                  <template #feedback>仅详情页抓取走并发（1-8），列表页始终串行</template>
                </n-form-item>
                <n-form-item label="深度检查周期">
                  <n-input-number v-model:value="crawlDraft.deep_check_interval_cycles" :min="0" style="width: 120px" />
                  <template #feedback>每 N 轮抓取自动做一次全来源深度变更检测；0 = 关闭</template>
                </n-form-item>
                <n-form-item label="清理过期">
                  <n-switch v-model:value="crawlDraft.cleanup_enabled" />
                </n-form-item>
                <n-form-item label="过期天数">
                  <n-input-number v-model:value="crawlDraft.expire_days" :min="1" style="width: 120px" />
                </n-form-item>
                <n-form-item label="User-Agent">
                  <n-input v-model:value="crawlDraft.user_agent" style="width: 360px" />
                </n-form-item>
                <n-button type="primary" :loading="saving" @click="saveCrawl">保存抓取参数</n-button>
              </n-form>
              </n-collapse-transition>
            </n-card>
            <n-card
              size="small"
              v-if="extractDraft"
              class="collapsible-card"
              @click="toggleExtract($event)"
            >
              <template #header>
                <div class="card-header-bar">
                  <n-icon size="16" color="var(--text-3)"><FilterOutline /></n-icon>
                  <span class="card-header-title">提取前置过滤</span>
                  <span class="header-spacer" />
                  <n-icon size="14" color="var(--text-3)">
                    <component :is="extractExpanded ? ChevronUpOutline : ChevronDownOutline" />
                  </n-icon>
                </div>
              </template>
              <n-collapse-transition :show="extractExpanded">
              <n-alert type="info" :bordered="false" style="margin-bottom: 12px">
                批量提取前先按规则预筛，不通过的通知不调 LLM（标记为“已跳过提取”），节省 Token。
                全部条件为“且”关系，留空/关闭的条件不参与判定。
              </n-alert>
              <n-form label-placement="left" label-width="160">
                <n-form-item label="单批上限">
                  <n-input-number v-model:value="extractDraft.batch_limit" :min="1" style="width: 120px" />
                </n-form-item>
                <n-form-item label="提取并发数">
                  <n-input-number v-model:value="extractDraft.concurrency" :min="1" :max="8" style="width: 120px" />
                  <template #feedback>批量提取并发上限（1–8，默认 3）；调大前注意供应商限流</template>
                </n-form-item>
                <n-form-item label="最短正文长度">
                  <n-input-number v-model:value="extractDraft.min_content_length" :min="0" style="width: 120px" />
                  <template #feedback>正文长度低于该值的通知跳过（默认 100，过滤空页面/占位页）</template>
                </n-form-item>
                <n-form-item label="最大通知天数">
                  <n-input-number v-model:value="extractDraft.max_age_days" :min="1" clearable style="width: 120px" />
                  <template #feedback>只提取 N 天以内发布的通知；留空 = 不限</template>
                </n-form-item>
                <n-form-item label="仅含关键词">
                  <n-input v-model:value="extractDraft.keyword_filter" placeholder="逗号分隔，标题或正文含任一关键词才提取" style="width: 360px" />
                </n-form-item>
                <n-form-item label="排除关键词">
                  <n-input v-model:value="extractDraft.skip_keywords" placeholder="逗号分隔，标题含任一关键词则跳过（如 公示,公示期）" style="width: 360px" />
                </n-form-item>
                <n-form-item label="必须含时间线索">
                  <n-switch v-model:value="extractDraft.require_time_hint" />
                  <span style="margin-left: 8px; color: #999; font-size: 12px">标题/正文须含日期（如 2026-08-16）才提取</span>
                </n-form-item>
                <n-form-item label="仅订阅命中">
                  <n-switch v-model:value="extractDraft.match_subscription_only" />
                  <span style="margin-left: 8px; color: #999; font-size: 12px">只提取至少命中一条订阅的通知</span>
                </n-form-item>
                <n-form-item label="重试失败项">
                  <n-switch v-model:value="extractDraft.retry_failed" />
                  <span style="margin-left: 8px; color: #999; font-size: 12px">每次提取顺带重试 status=failed 的旧通知</span>
                </n-form-item>
                <n-form-item label="跳过 LLM 提取">
                  <n-switch v-model:value="extractDraft.skip_llm" />
                  <span style="margin-left: 8px; color: #999; font-size: 12px">不调 LLM，仅入库 + 建向量索引（状态置“部分提取”），最省 Token 模式</span>
                </n-form-item>
                <n-button type="primary" :loading="saving" @click="saveExtract">保存提取过滤配置</n-button>
              </n-form>
              </n-collapse-transition>
            </n-card>
          </n-space>
        </n-tab-pane>

        <n-tab-pane name="usage" tab="Token 用量">
          <n-space vertical size="large">
            <n-space align="center">
              <n-radio-group v-model:value="usageDays" size="small">
                <n-radio-button :value="7">近 7 天</n-radio-button>
                <n-radio-button :value="30">近 30 天</n-radio-button>
                <n-radio-button :value="90">近 90 天</n-radio-button>
              </n-radio-group>
              <n-button size="small" :loading="usageLoading" @click="loadUsage">刷新</n-button>
              <span style="color: #999; font-size: 12px">
                统计近 {{ usageDays }} 天所有 LLM 调用（提取 / 问答 / 待办 / Embedding / 连通性测试）
              </span>
            </n-space>

            <n-spin :show="usageLoading">
              <template v-if="usageTotal.calls">
                <div class="usage-stats">
                  <StatCard :icon="PulseOutline" label="调用次数" :value="Number(usageTotal.calls ?? 0)" color="primary" />
                  <StatCard :icon="PencilOutline" label="输入 tokens" :value="Number(usageTotal.input_tokens ?? 0)" color="info" />
                  <StatCard :icon="PaperPlaneOutline" label="输出 tokens" :value="Number(usageTotal.output_tokens ?? 0)" color="violet" />
                  <StatCard :icon="CheckmarkCircleOutline" label="成功" :value="Number(usageTotal.success ?? 0)" color="success" />
                  <StatCard :icon="CloseCircleOutline" label="失败" :value="Number(usageTotal.failed ?? 0)" color="error" />
                </div>
                <n-data-table
                  :columns="usageColumns"
                  :data="usageRows"
                  size="small"
                  :bordered="false"
                  :max-height="380"
                  style="margin-top: 12px"
                />
              </template>
              <n-empty v-else-if="!usageLoading" description="暂无 Token 调用记录" />
            </n-spin>
          </n-space>
        </n-tab-pane>

        <n-tab-pane name="reload" tab="重载与磁盘">
          <n-space vertical size="large">
            <n-space>
              <n-button type="primary" :loading="reloading" @click="reloadConfig">
                <template #icon><n-icon><ReloadOutline /></n-icon></template>
                强制重载配置
              </n-button>
            </n-space>
            <n-card size="small" v-if="cfg.disk">
              <template #header>
                <div class="section-title">
                  <n-icon size="16" color="var(--text-3)"><DiscOutline /></n-icon>
                  磁盘信息
                </div>
              </template>
                <n-descriptions :column="1" size="small">
                <n-descriptions-item label="路径">{{ cfg.disk.path }}</n-descriptions-item>
                <n-descriptions-item label="存在">{{ cfg.disk.exists ? '是' : '否' }}</n-descriptions-item>
                <n-descriptions-item label="最后修改">
                  {{ cfg.disk.last_modified || '—' }}
                </n-descriptions-item>
              </n-descriptions>
            </n-card>
          </n-space>
        </n-tab-pane>

        <n-tab-pane name="update" tab="检查更新">
          <n-space vertical size="large">
            <n-space>
              <n-button type="primary" :loading="updateChecking" @click="checkUpdate">
                <template #icon><n-icon><CloudDownloadOutline /></n-icon></template>
                检查更新
              </n-button>
              <n-tag v-if="!updateChecking && updateResult?.current_version" :bordered="false">
                当前版本 v{{ updateResult.current_version }}
              </n-tag>
            </n-space>

            <n-card size="small" v-if="updateResult">
              <template #header>
                <div class="section-title">
                  <n-icon size="16" color="var(--text-3)"><CloudDownloadOutline /></n-icon>
                  {{ updateResult.update_available ? `发现新版本 ${updateResult.latest_version}` : '已是最新版本' }}
                </div>
              </template>
              <n-space vertical size="medium">
                <n-alert v-if="updateResult.error" type="warning" :bordered="false">
                  {{ updateResult.error }}
                </n-alert>
                <template v-if="updateResult.update_available">
                  <n-descriptions :column="1" size="small">
                    <n-descriptions-item label="最新版本">{{ updateResult.latest_version }}</n-descriptions-item>
                    <n-descriptions-item label="当前版本">v{{ updateResult.current_version }}</n-descriptions-item>
                  </n-descriptions>
                  <n-card size="small" embedded v-if="updateResult.notes" title="更新日志">
                    <div class="changelog">{{ updateResult.notes }}</div>
                  </n-card>
                  <n-space vertical v-if="updateResult.assets?.length">
                    <div v-for="a in updateResult.assets ?? []" :key="a.name" class="asset-row">
                      <n-button size="small" @click="openExternal(a.browser_download_url)">
                        下载 {{ a.name }}
                      </n-button>
                      <span class="asset-size">{{ formatSize(a.size) }}</span>
                    </div>
                  </n-space>
                  <n-button v-if="updateResult.html_url" text type="primary" @click="openExternal(updateResult.html_url)">
                    查看发布页 →
                  </n-button>
                  <n-text depth="3" style="font-size: 12px">
                    下载完成后直接运行新版本安装包覆盖安装，数据（通知/待办/配置）不会丢失。
                  </n-text>
                </template>
                <n-text v-else-if="!updateResult.error" depth="3">检查时间：{{ updateResult.checked_at }}</n-text>
              </n-space>
            </n-card>
          </n-space>
        </n-tab-pane>
      </n-tabs>
    </n-spin>
  </n-card>
</template>

<style scoped>
.changelog {
  white-space: pre-wrap;
  font-size: 13px;
  line-height: 1.6;
  max-height: 260px;
  overflow-y: auto;
}
.asset-row {
  display: flex;
  align-items: center;
  gap: 12px;
}
.asset-size {
  color: var(--text-3);
  font-size: 12px;
}
.usage-stats {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
  gap: 16px;
  margin-bottom: 16px;
}
.test-ok {
  color: var(--success);
  font-size: 12px;
  display: inline-flex;
  align-items: center;
  gap: 4px;
}
.test-bad {
  color: var(--error);
  font-size: 12px;
  display: inline-flex;
  align-items: center;
  gap: 4px;
}
.key-dot {
  display: inline-block;
  width: 8px;
  height: 8px;
  border-radius: 50%;
}
.key-dot.ok {
  background: var(--success);
}
.key-dot.bad {
  background: var(--error);
}
.help-icon {
  margin-left: 4px;
  cursor: help;
  color: var(--text-3);
}
.card-header-bar {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
  width: 100%;
  cursor: pointer;
  user-select: none;
}
.collapsible-card {
  cursor: pointer;
  transition: box-shadow 0.2s ease;
}
.collapsible-card:hover {
  box-shadow: var(--shadow-2);
}
.card-header-bar:hover {
  opacity: 0.88;
}
.header-index {
  color: var(--text-3);
  font-size: 13px;
  font-weight: 600;
}
.card-header-title {
  font-weight: 600;
  max-width: 220px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.header-link {
  color: var(--primary);
  text-decoration: none;
  font-size: 13px;
  max-width: 340px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.header-link:hover {
  text-decoration: underline;
}
.header-muted {
  color: var(--text-3);
  font-size: 12px;
}
.header-spacer {
  flex: 1;
}
.collapse-arrow {
  color: var(--text-3);
  font-size: 12px;
}
.input-suffix-link {
  color: var(--primary);
  cursor: pointer;
  text-decoration: none;
  font-size: 14px;
  padding: 0 2px;
}
.input-suffix-link:hover {
  text-decoration: underline;
}
.input-suffix-disabled {
  color: var(--border);
  cursor: not-allowed;
  font-size: 14px;
  padding: 0 2px;
}
</style>