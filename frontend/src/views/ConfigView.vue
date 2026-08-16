<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import { useMessage } from 'naive-ui'
import { useConfigStore } from '../stores/useConfigStore'
import type {
  ConfigMutationResult,
  CrawlConfig,
  ExtractConfig,
  ModelProfileView,
  ModelsConfig,
  ProviderConfig,
  ReloadResult,
  SourceConfig,
} from '../api/schema'

const message = useMessage()
const cfg = useConfigStore()
const activeTab = ref('models')

const modelsDraft = ref<ModelsConfig | null>(null)
const providerDraft = ref<Record<string, ProviderConfig>>({})
const sourcesDraft = ref<SourceConfig[]>([])
const crawlDraft = ref<CrawlConfig | null>(null)
const extractDraft = ref<ExtractConfig | null>(null)
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

const crawlModeOptions = [
  { label: '增量抓取（推荐：已入库的不重复抓，变更靠深度检查）', value: 'incremental' },
  { label: '全量抓取（每轮重抓全部详情页，耗时高）', value: 'full' },
  { label: '仅列表（只抓列表页标题/链接，不抓正文）', value: 'list_only' },
]

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
  crawlDraft.value = cfg.crawl ? { ...cfg.crawl } : null
  extractDraft.value = cfg.extract ? { ...cfg.extract } : null
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
  sourcesDraft.value.push({
    name: '',
    type: 'web',
    list_url: '',
    url_pattern: null,
    max_pages: 5,
    enabled: true,
    crawl_mode: 'incremental',
    max_age_days: null,
    fetch_detail: true,
    deep_check: false,
  })
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
      const s = sourcesDraft.value[idx]
      if (res.suggested_pattern && !s.url_pattern) {
        s.url_pattern = res.suggested_pattern
        message.info(`已根据页面自动填充 URL 模式：${res.suggested_pattern}`)
      }
    } else {
      message.error(res.error || '链接测试失败')
    }
  } catch (e) {
    message.error(e instanceof Error ? e.message : String(e))
  } finally {
    sourceTestBusy.value[idx] = false
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
                <n-form-item label="启用">
                  <n-switch v-model:value="s.enabled" />
                  <span style="margin-left: 8px; color: #999; font-size: 12px">停用后定时抓取与全量抓取会跳过该来源</span>
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
                  <n-input v-model:value="s.url_pattern" placeholder="可选，正文链接正则；留空时点“测试链接”自动填充" />
                  <template #feedback>
                    只抓取匹配该正则的链接；留空则抓取全部发现链接
                  </template>
                </n-form-item>
                <n-form-item label="抓取模式">
                  <n-select v-model:value="s.crawl_mode" :options="crawlModeOptions" style="width: 380px" />
                </n-form-item>
                <n-form-item label="最近 N 天">
                  <n-input-number v-model:value="s.max_age_days" :min="1" clearable style="width: 120px" />
                  <template #feedback>留空 = 不限；只抓取发布时间在 N 天以内的通知</template>
                </n-form-item>
                <n-form-item label="最大页数">
                  <n-input-number v-model:value="s.max_pages" :min="1" style="width: 120px" />
                </n-form-item>
                <n-form-item label="抓取正文">
                  <n-switch v-model:value="s.fetch_detail" />
                  <span style="margin-left: 8px; color: #999; font-size: 12px">关闭后仅入库标题与链接（节省流量）</span>
                </n-form-item>
                <n-form-item label="深度检查">
                  <n-switch v-model:value="s.deep_check" />
                  <span style="margin-left: 8px; color: #999; font-size: 12px">增量模式下周期重抓详情页比对内容变更</span>
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

        <n-tab-pane name="crawl" tab="抓取与提取">
          <n-space vertical size="large">
            <n-card title="全局抓取参数" size="small" v-if="crawlDraft">
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
            </n-card>
            <n-card title="提取前置过滤" size="small" v-if="extractDraft">
              <n-alert type="info" :bordered="false" style="margin-bottom: 12px">
                批量提取前先按规则预筛，不通过的通知不调 LLM（标记为“已跳过提取”），节省 Token。
                全部条件为“且”关系，留空/关闭的条件不参与判定。
              </n-alert>
              <n-form label-placement="left" label-width="160">
                <n-form-item label="单批上限">
                  <n-input-number v-model:value="extractDraft.batch_limit" :min="1" style="width: 120px" />
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
            </n-card>
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