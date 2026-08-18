<script setup lang="ts">
import { computed } from 'vue'
import { marked } from 'marked'
import DOMPurify from 'dompurify'

const props = defineProps<{ content: string }>()

const html = computed(() => {
  try {
    const raw = marked.parse(props.content)
    return DOMPurify.sanitize(typeof raw === 'string' ? raw : '')
  } catch {
    return DOMPurify.sanitize(props.content)
  }
})
</script>

<template>
  <div class="markdown-body" v-html="html"></div>
</template>

<style scoped>
.markdown-body {
  font-size: 14px;
  line-height: 1.7;
  word-break: break-word;
  color: inherit;
}

.markdown-body :deep(p) {
  margin: 0 0 10px;
}
.markdown-body :deep(p:last-child) {
  margin-bottom: 0;
}
.markdown-body :deep(h1),
.markdown-body :deep(h2),
.markdown-body :deep(h3),
.markdown-body :deep(h4),
.markdown-body :deep(h5),
.markdown-body :deep(h6) {
  margin: 16px 0 8px;
  font-weight: 600;
  line-height: 1.4;
  color: inherit;
}
.markdown-body :deep(h1) {
  font-size: 20px;
}
.markdown-body :deep(h2) {
  font-size: 18px;
}
.markdown-body :deep(h3) {
  font-size: 16px;
}
.markdown-body :deep(h4) {
  font-size: 15px;
}
.markdown-body :deep(h5),
.markdown-body :deep(h6) {
  font-size: 14px;
}
.markdown-body :deep(ul),
.markdown-body :deep(ol) {
  margin: 0 0 10px;
  padding-left: 22px;
}
.markdown-body :deep(li) {
  margin: 4px 0;
}
.markdown-body :deep(blockquote) {
  margin: 0 0 10px;
  padding: 4px 12px;
  border-left: 3px solid var(--border);
  color: var(--text-2);
}
.markdown-body :deep(code) {
  font-family: var(--font-mono);
  font-size: 13px;
  background: var(--bg-soft);
  border-radius: 4px;
  padding: 2px 5px;
}
.markdown-body :deep(pre) {
  margin: 0 0 10px;
  padding: 12px 14px;
  border-radius: 8px;
  background: var(--bg-soft);
  overflow-x: auto;
}
.markdown-body :deep(pre code) {
  background: transparent;
  padding: 0;
  white-space: pre-wrap;
  word-break: break-word;
}
.markdown-body :deep(table) {
  border-collapse: collapse;
  margin: 0 0 10px;
  width: 100%;
  font-size: 13px;
}
.markdown-body :deep(th),
.markdown-body :deep(td) {
  border: 1px solid var(--border);
  padding: 6px 10px;
  text-align: left;
}
.markdown-body :deep(th) {
  background: var(--bg-soft);
  font-weight: 600;
}
.markdown-body :deep(a) {
  color: var(--primary);
  text-decoration: none;
}
.markdown-body :deep(a:hover) {
  text-decoration: underline;
}
.markdown-body :deep(hr) {
  border: none;
  border-top: 1px solid var(--border);
  margin: 14px 0;
}
.markdown-body :deep(img) {
  max-width: 100%;
}
</style>
