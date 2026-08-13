<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import { useMessage } from 'naive-ui'
import { useConfigStore } from '../stores/useConfigStore'
import type { ConfigMutationResult, ModelProfileView, ModelsConfig, ProviderConfig, ReloadResult, SourceConfig } from '../api/schema'

const message = useMessage()
const cfg = useConfigStore()
const activeTab = ref('models')

const modelsDraft = ref<ModelsConfig | null>(null)
const providerDraft = ref<Record<string, ProviderConfig>>({})
const sourcesDraft = ref<SourceConfig[]>([])
const testModelInput = ref<Record<string, string>>({})
const testBusy = ref<Record<string, boolean>>({})
const sourceTestBusy = ref<Record<string, boolean>>({})
const saving = ref(false)
const reloading = ref(false)
const loading = ref(false)

const providerNames = computed(() => Object.keys(cfg.providers || {}))

const taskLabels: Record<string, string> = {
  extraction: '信息提取',
  qa: '智能问答',
  todo: '待办生成',
  embedding: '向量嵌入',
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
      extraction: { ...cfg.models.extraction },
      qa: { ...cfg.models.qa },
      todo: { ...cfg.models.todo },
      embedding: { ...cfg.models.embedding },
    }
    testModelInput.value = {}
    for (const profile of Object.values(cfg.models) as ModelProfileView[]) {
      if (profile.provider && !testModelInput.value[profile.provider]) {
        testModelInput.value[profile.provider] = profile.model
      }
    }
  }
  providerDraft.value = {}
  for (const [name, p] of Object.entries(cfg.providers || {})) {
    providerDraft.value[name] = { name: p.name, base_url: p.base_url, api_key_env: p.api_key_env }
  }
  sourcesDraft.value = (cfg.sources?.sources ?? []).map((s) => ({ ...s }))
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

async function saveSources() {
  saving.value = true
  try {
    handleMutation(await cfg.updateSources(sourcesDraft.value))
  } catch (e) {
    message.error(e instanceof Error ? e.message : String(e))
  } finally {
    saving.value = false
  }
}

function addSource() {
  sourcesDraft.value.push({ name: '', type: 'web', list_url: '', url_pattern: null, max_pages: 5 })
}

function removeSource(idx: number) {
  sourcesDraft.value.splice(idx, 1)
}

async function testProvider(name: string) {
  const model = (testModelInput.value[name] || '').trim()
  if (!model) {
    message.warning('请先填写测试用的模型名')
    return
  }
  testBusy.value[name] = true
  try {
    const res = await cfg.testModel({ provider: name, model, timeout: 30 })
    if (res.ok) {
      message.success(`连接成功（${res.latency_ms}ms）${res.completion ? `：${res.completion}` : ''}`)
    } else {
      message.error(res.error || '连接失败')
    }
  } catch (e) {
    message.error(e instanceof Error ? e.message : String(e))
  } finally {
    testBusy.value[name] = false
  }
}

async function testSourceUrl(idx: number, url: string) {
  if (!url.trim()) {
    message.warning('请先填写 list_url')
    return
  }
  sourceTestBusy.value[idx] = true
  try {
    const res = await cfg.testSource({ url, timeout: 15 })
    if (res.ok) {
      message.success(`链接可达（${res.latency_ms}ms，发现 ${res.link_count} 条链接）`)
    } else {
      message.error(res.error || '链接测试失败')
    }
  } catch (e) {
    message.error(e instanceof Error ? e.message : String(e))
  } finally {
    sourceTestBusy.value[idx] = false
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
</script>

<template>
  <n-card title="系统配置">
    <n-spin :show="loading">
      <n-tabs v-model:value="activeTab" type="line" animated>
        <n-tab-pane name="models" tab="模型">
          <n-form label-placement="left" label-width="90" v-if="modelsDraft">
            <n-form-item v-for="task in (['extraction', 'qa', 'todo', 'embedding'] as const)" :key="task" :label="taskLabels[task]">
              <n-space>
                <n-select
                  v-model:value="modelsDraft[task].provider"
                  :options="providerNames.map((p) => ({ label: p, value: p }))"
                  placeholder="Provider"
                  style="width: 200px"
                />
                <n-input v-model:value="modelsDraft[task].model" placeholder="模型名" style="width: 300px" />
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
              :title="`${name}（${name}）`"
            >
              <n-form label-placement="left" label-width="110">
                <n-form-item label="Base URL">
                  <n-input v-model:value="p.base_url" placeholder="https://api.example.com" />
                </n-form-item>
                <n-form-item label="API Key 环境变量">
                  <n-input v-model:value="p.api_key_env" placeholder="OPENAI_API_KEY" />
                </n-form-item>
                <n-form-item label="Key 状态">
                  <n-tag :bordered="false" :type="cfg.providers?.[name]?.api_key_status ? 'success' : 'default'" size="small">
                    已配置{{ cfg.providers?.[name]?.api_key_status ? '' : '（未读）' }}
                  </n-tag>
                </n-form-item>
                <n-form-item label="连通性测试">
                  <n-space>
                    <n-input v-model:value="testModelInput[name]" placeholder="测试用模型名" style="width: 200px" />
                    <n-button size="small" secondary :loading="testBusy[name]" @click="testProvider(name)">测试连接</n-button>
                  </n-space>
                </n-form-item>
              </n-form>
            </n-card>
            <n-button type="primary" :loading="saving" @click="saveProviders">保存供应商配置</n-button>
          </n-space>
        </n-tab-pane>

        <n-tab-pane name="sources" tab="数据源">
          <n-descriptions :column="2" size="small" style="margin-bottom: 12px">
            <n-descriptions-item label="学校">{{ cfg.sources?.name || '—' }}</n-descriptions-item>
            <n-descriptions-item label="代码">{{ cfg.sources?.code || '—' }}</n-descriptions-item>
          </n-descriptions>
          <n-space vertical size="large">
            <n-card v-for="(s, idx) in sourcesDraft" :key="idx" size="small" :title="`数据源 ${idx + 1}`">
              <n-form label-placement="left" label-width="110">
                <n-form-item label="名称">
                  <n-input v-model:value="s.name" placeholder="如 教务处" />
                </n-form-item>
                <n-form-item label="类型">
                  <n-select
                    v-model:value="s.type"
                    :options="[{ label: 'web', value: 'web' }]"
                    style="width: 140px"
                  />
                </n-form-item>
                <n-form-item label="列表地址">
                  <n-input v-model:value="s.list_url" placeholder="https://..." />
                </n-form-item>
                <n-form-item label="URL 模式">
                  <n-input v-model:value="s.url_pattern" placeholder="可选，正文链接正则" />
                </n-form-item>
                <n-form-item label="最大页数">
                  <n-input-number v-model:value="s.max_pages" :min="1" style="width: 120px" />
                </n-form-item>
                <n-form-item label=" ">
                  <n-space>
                    <n-button size="small" secondary :loading="sourceTestBusy[idx]" @click="testSourceUrl(idx, s.list_url)">
                      测试链接
                    </n-button>
                    <n-button size="small" quaternary type="error" @click="removeSource(idx)">删除</n-button>
                  </n-space>
                </n-form-item>
              </n-form>
            </n-card>
            <n-space>
              <n-button secondary @click="addSource">添加数据源</n-button>
              <n-button type="primary" :loading="saving" @click="saveSources">保存数据源配置</n-button>
            </n-space>
          </n-space>
        </n-tab-pane>

        <n-tab-pane name="usage" tab="Token 用量">
          <n-alert type="info" :bordered="false">
            Token 用量分析依赖 <code>GET /usage/tokens</code> 端点，后端暂未实现（§7 遗留项），后续版本提供。
          </n-alert>
        </n-tab-pane>

        <n-tab-pane name="reload" tab="重载与磁盘">
          <n-space vertical size="large">
            <n-space>
              <n-button type="primary" :loading="reloading" @click="reloadConfig">强制重载配置</n-button>
            </n-space>
            <n-card title="磁盘信息" size="small" v-if="cfg.disk">
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
      </n-tabs>
    </n-spin>
  </n-card>
</template>