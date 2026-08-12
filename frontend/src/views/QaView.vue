<script setup lang="ts">
import { ref } from 'vue'
import { useQaStore } from '../stores/useQaStore'

const qa = useQaStore()
const question = ref('')
const answer = ref('')
let abortController: AbortController | null = null

async function ask() {
  answer.value = ''
  abortController = new AbortController()
  try {
    await qa.askStream({ question: question.value }, (chunk) => {
      answer.value += chunk
    }, abortController.signal)
  } catch (e) {
    answer.value += '\n[错误] ' + (e instanceof Error ? e.message : String(e))
  } finally {
    abortController = null
  }
}

function cancel() {
  abortController?.abort()
  abortController = null
}
</script>

<template>
  <n-card title="智能问答">
    <n-input v-model:value="question" placeholder="输入问题" />
    <n-space>
      <n-button @click="ask">提问</n-button>
      <n-button secondary @click="cancel">取消</n-button>
    </n-space>
    <n-card title="回答">
      <pre>{{ answer }}</pre>
    </n-card>
  </n-card>
</template>
