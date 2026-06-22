<template>
  <div class="page-view">
    <RunNav :runId="runId" />
    <div class="content-area">
      <div class="report-body">
        <div class="report-header">
          <h1 class="page-title">Research Report</h1>
          <button class="refresh-btn" @click="loadReport" :disabled="loading">
            {{ loading ? 'Loading...' : 'Refresh' }}
          </button>
        </div>

        <div v-if="loading" class="loading-msg">Loading report...</div>
        <div v-else-if="error" class="error-msg">{{ error }}</div>
        <div v-else-if="!reportMd" class="empty-msg">
          No report generated yet. Complete the pipeline and run the Report Generate step.
        </div>
        <div v-else class="markdown-body" v-html="renderedMarkdown"></div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import RunNav from '../components/RunNav.vue'
import { getReport } from '../api/artifacts.js'

const props = defineProps({ runId: String })

const loading = ref(false)
const error = ref('')
const reportMd = ref('')

// Minimal markdown-to-HTML renderer (no external dependency)
const renderedMarkdown = computed(() => {
  if (!reportMd.value) return ''
  let html = reportMd.value
    // Escape HTML characters first
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    // Headings
    .replace(/^### (.+)$/gm, '<h3>$1</h3>')
    .replace(/^## (.+)$/gm, '<h2>$1</h2>')
    .replace(/^# (.+)$/gm, '<h1>$1</h1>')
    // Bold, italic
    .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
    .replace(/\*(.+?)\*/g, '<em>$1</em>')
    // Code inline
    .replace(/`(.+?)`/g, '<code>$1</code>')
    // Horizontal rule
    .replace(/^---$/gm, '<hr>')
    // Unordered list items
    .replace(/^[-*] (.+)$/gm, '<li>$1</li>')
    // Paragraph breaks (double newline)
    .replace(/\n{2,}/g, '</p><p>')

  return `<div><p>${html}</p></div>`
    .replace(/<p>(<h[1-3]>|<hr>|<li>)/g, '$1')
    .replace(/(<\/h[1-3]>|<\/li>)<\/p>/g, '$1')
})

const loadReport = async () => {
  loading.value = true
  error.value = ''
  try {
    reportMd.value = await getReport(props.runId)
  } catch (err) {
    error.value = err?.response?.data?.detail || err.message || 'Failed to load report'
    reportMd.value = ''
  } finally {
    loading.value = false
  }
}

onMounted(loadReport)
</script>

<style scoped>
.page-view {
  display: flex;
  flex-direction: column;
  height: 100vh;
  background: #F8F9FA;
}

.content-area {
  flex: 1;
  overflow-y: auto;
}

.report-body {
  max-width: 820px;
  margin: 0 auto;
  padding: 40px 24px;
}

.report-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 32px;
}

.page-title {
  font-size: 1.5rem;
  font-weight: 700;
}

.refresh-btn {
  background: var(--black);
  color: var(--white);
  border: none;
  padding: 8px 16px;
  font-family: var(--font-mono);
  font-size: 12px;
  font-weight: 700;
  cursor: pointer;
  border-radius: 3px;
  transition: opacity 0.2s;
}

.refresh-btn:hover:not(:disabled) { opacity: 0.8; }
.refresh-btn:disabled { background: #CCC; cursor: not-allowed; }

.loading-msg, .empty-msg {
  padding: 40px;
  text-align: center;
  color: #999;
  font-size: 14px;
}

.error-msg {
  padding: 14px;
  color: #ef4444;
  background: #FEE2E2;
  border-radius: 6px;
  font-size: 12px;
  font-family: var(--font-mono);
  margin-bottom: 20px;
}

/* Markdown rendering */
.markdown-body {
  background: #FFF;
  border: 1px solid #E5E5E5;
  border-radius: 8px;
  padding: 32px 40px;
  line-height: 1.7;
  color: #333;
  font-size: 15px;
}

.markdown-body :deep(h1) {
  font-size: 1.8rem;
  font-weight: 700;
  margin: 0 0 16px 0;
  color: var(--black);
  border-bottom: 2px solid #E5E5E5;
  padding-bottom: 12px;
}

.markdown-body :deep(h2) {
  font-size: 1.3rem;
  font-weight: 700;
  margin: 28px 0 12px 0;
  color: var(--black);
}

.markdown-body :deep(h3) {
  font-size: 1.1rem;
  font-weight: 600;
  margin: 20px 0 10px 0;
  color: #444;
}

.markdown-body :deep(p) {
  margin: 0 0 14px 0;
}

.markdown-body :deep(strong) {
  font-weight: 700;
  color: var(--black);
}

.markdown-body :deep(em) {
  font-style: italic;
  color: #555;
}

.markdown-body :deep(code) {
  background: #F5F5F5;
  padding: 1px 5px;
  border-radius: 3px;
  font-family: var(--font-mono);
  font-size: 0.88em;
  color: #444;
}

.markdown-body :deep(li) {
  margin: 4px 0 4px 20px;
  list-style: disc;
  display: list-item;
}

.markdown-body :deep(hr) {
  border: none;
  border-top: 1px solid #E5E5E5;
  margin: 24px 0;
}
</style>
