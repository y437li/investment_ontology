<template>
  <div class="page-view">
    <RunNav :runId="runId" />
    <div class="content-area">
      <div class="page-body">
        <div class="page-header">
          <h1 class="page-title">Validation Results</h1>
          <button class="refresh-btn" @click="loadData" :disabled="loading">
            {{ loading ? 'Loading...' : 'Refresh' }}
          </button>
        </div>

        <div v-if="loading" class="loading-msg">Loading validation data...</div>
        <div v-else-if="error" class="error-msg">{{ error }}</div>
        <div v-else-if="!rows.length" class="empty-msg">
          No validation data. Run the Validation step in the pipeline first.
        </div>

        <div v-else>
          <div class="caveat-banner">
            Single-snapshot validation: forward-return metrics are illustrative only.
            Results do not constitute investment advice.
          </div>

          <!-- Summary stats -->
          <div class="summary-row">
            <div class="summary-card">
              <span class="summary-value">{{ rows.length }}</span>
              <span class="summary-label">Total Rows</span>
            </div>
            <div class="summary-card">
              <span class="summary-value">{{ uniqueThemes }}</span>
              <span class="summary-label">Unique Themes</span>
            </div>
            <div class="summary-card" v-if="passCount !== null">
              <span class="summary-value">{{ passCount }}</span>
              <span class="summary-label">Passed</span>
            </div>
          </div>

          <!-- Table -->
          <div class="table-wrapper">
            <table class="data-table">
              <thead>
                <tr>
                  <th v-for="col in columns" :key="col">{{ col }}</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="(row, idx) in rows" :key="idx">
                  <td v-for="col in columns" :key="col">
                    <span
                      v-if="col === 'validation_status' || col === 'backtest_status'"
                      class="status-badge"
                      :class="statusClass(row[col])"
                    >{{ row[col] || '' }}</span>
                    <span v-else>{{ row[col] ?? '' }}</span>
                  </td>
                </tr>
              </tbody>
            </table>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import RunNav from '../components/RunNav.vue'
import { getValidationCsv } from '../api/artifacts.js'

const props = defineProps({ runId: String })

const loading = ref(false)
const error = ref('')
const rows = ref([])

const columns = computed(() => {
  if (!rows.value.length) return []
  return Object.keys(rows.value[0])
})

const uniqueThemes = computed(() => {
  const themes = new Set(rows.value.map(r => r.community_id || r.theme_name).filter(Boolean))
  return themes.size
})

const passCount = computed(() => {
  const col = columns.value.find(c => c.toLowerCase().includes('pass') || c.toLowerCase().includes('status'))
  if (!col) return null
  return rows.value.filter(r => (r[col] || '').toLowerCase().includes('pass')).length
})

const statusClass = (val) => {
  if (!val) return ''
  const v = val.toLowerCase()
  if (v.includes('pass') || v.includes('ok') || v.includes('success')) return 'status-pass'
  if (v.includes('fail') || v.includes('error')) return 'status-fail'
  if (v.includes('block') || v.includes('skip')) return 'status-skip'
  return ''
}

const loadData = async () => {
  loading.value = true
  error.value = ''
  try {
    rows.value = await getValidationCsv(props.runId)
  } catch (err) {
    error.value = err?.response?.data?.detail || err.message || 'Failed to load validation data'
    rows.value = []
  } finally {
    loading.value = false
  }
}

onMounted(loadData)
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

.page-body {
  max-width: 1200px;
  margin: 0 auto;
  padding: 32px 24px;
}

.page-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 24px;
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

.caveat-banner {
  padding: 12px 16px;
  background: #FFF7ED;
  border: 1px solid #FED7AA;
  border-radius: 6px;
  font-size: 12px;
  color: #9A3412;
  margin-bottom: 20px;
  line-height: 1.5;
}

.summary-row {
  display: flex;
  gap: 16px;
  margin-bottom: 24px;
}

.summary-card {
  background: #FFF;
  border: 1px solid #E5E5E5;
  border-radius: 6px;
  padding: 16px 20px;
  min-width: 120px;
  text-align: center;
}

.summary-value {
  display: block;
  font-family: var(--font-mono);
  font-size: 1.6rem;
  font-weight: 700;
  color: var(--black);
}

.summary-label {
  display: block;
  font-size: 10px;
  color: #888;
  text-transform: uppercase;
  margin-top: 4px;
  font-family: var(--font-mono);
}

.table-wrapper {
  overflow-x: auto;
  border: 1px solid #E5E5E5;
  border-radius: 8px;
  background: #FFF;
}

.data-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 12px;
}

.data-table th {
  background: #F8F9FA;
  padding: 10px 14px;
  text-align: left;
  font-size: 10px;
  font-family: var(--font-mono);
  text-transform: uppercase;
  color: #666;
  border-bottom: 1px solid #E5E5E5;
  white-space: nowrap;
}

.data-table td {
  padding: 10px 14px;
  border-bottom: 1px solid #F5F5F5;
  color: #333;
  white-space: nowrap;
}

.data-table tr:last-child td { border-bottom: none; }
.data-table tr:hover td { background: #FAFAFA; }

.status-badge {
  display: inline-block;
  padding: 2px 8px;
  border-radius: 3px;
  font-size: 10px;
  font-family: var(--font-mono);
  font-weight: 700;
  text-transform: uppercase;
}

.status-pass { background: #ECFDF5; color: #065F46; }
.status-fail { background: #FEE2E2; color: #991B1B; }
.status-skip { background: #FEF9C3; color: #92400E; }

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
</style>
