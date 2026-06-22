<template>
  <div class="admin-container">
    <!-- Navigation -->
    <nav class="navbar">
      <div class="nav-brand">
        <router-link to="/" class="back-link">← THEME ENGINE</router-link>
        <span class="nav-sep">/</span>
        <span class="nav-section">Admin</span>
      </div>
      <div class="nav-tagline">Developer Area</div>
    </nav>

    <div class="main-content">
      <!-- Header -->
      <div class="page-header">
        <div class="tag-row">
          <span class="accent-tag">ADMIN</span>
          <span class="version-text">Developer tools</span>
        </div>
        <h1 class="page-title">Pipeline Control</h1>
        <p class="page-desc">
          Create and manage pipeline runs. This area is for data engineering and
          QA — not the primary PM workspace.
        </p>
      </div>

      <div class="dashboard-section">
        <!-- Create Run -->
        <div class="left-panel">
          <div class="panel-header">
            <span class="status-dot">■</span> CREATE NEW RUN
          </div>

          <h2 class="section-title">Start Discovery</h2>
          <p class="section-desc">
            Provide an as-of date to create a new pipeline run.
            All data will be point-in-time gated to this date.
          </p>

          <div class="console-box">
            <div class="console-section">
              <div class="console-header">
                <span class="console-label">AS-OF DATE (YYYY-MM-DD)</span>
              </div>
              <input
                v-model="asOfDate"
                class="date-input"
                type="text"
                placeholder="2024-06-30"
                :disabled="creating"
              />
            </div>

            <div class="console-section btn-section">
              <button
                class="start-btn"
                @click="handleCreateRun"
                :disabled="!canCreate || creating"
              >
                <span v-if="!creating">Create Run →</span>
                <span v-else>Creating...</span>
              </button>
            </div>

            <div v-if="createError" class="error-msg">{{ createError }}</div>
          </div>
        </div>

        <!-- Recent Runs -->
        <div class="right-panel">
          <div class="panel-header">
            <span class="status-dot">■</span> ALL RUNS
            <span class="run-count" v-if="allRuns.length">({{ allRuns.length }})</span>
          </div>

          <div v-if="loadingRuns" class="loading-msg">Loading runs...</div>

          <div v-else-if="allRuns.length === 0" class="empty-runs">
            No runs yet. Create your first run to get started.
          </div>

          <div v-else class="runs-list">
            <div
              v-for="run in allRuns"
              :key="run.run_id"
              class="run-card"
              @click="openRun(run.run_id)"
            >
              <div class="run-card-top">
                <div class="run-id">{{ run.run_id }}</div>
                <span
                  class="badge"
                  :class="run.discovery_frozen ? 'badge-frozen' : 'badge-active'"
                >
                  {{ run.discovery_frozen ? 'Frozen' : 'Active' }}
                </span>
              </div>
              <div class="run-meta">
                <span class="meta-item">
                  <span class="meta-label">As-of</span>
                  <span class="meta-value">{{ run.as_of_date }}</span>
                </span>
                <span class="meta-item">
                  <span class="meta-label">Created</span>
                  <span class="meta-value">{{ formatDate(run.created_at) }}</span>
                </span>
              </div>
            </div>
          </div>
        </div>
      </div>

      <!-- Pipeline overview -->
      <div class="pipeline-section">
        <div class="panel-header">
          <span class="diamond-icon">◇</span> PIPELINE SEQUENCE
        </div>
        <div class="workflow-list">
          <div class="workflow-item" v-for="(step, i) in pipelineSteps" :key="i">
            <span class="step-num">{{ String(i + 1).padStart(2, '0') }}</span>
            <div class="step-info">
              <div class="step-title">{{ step.title }}</div>
              <div class="step-desc">{{ step.desc }}</div>
            </div>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { createRun, listRuns } from '../api/runs.js'

const router = useRouter()

const asOfDate = ref('')
const creating = ref(false)
const createError = ref('')
const allRuns = ref([])
const loadingRuns = ref(false)

const pipelineSteps = [
  { title: 'Data Import & Clean', desc: 'Ingest source documents and apply data quality gates.' },
  { title: 'Extraction & Resolution', desc: 'Extract entities and edges; resolve entity aliases.' },
  { title: 'Graph Build', desc: 'Construct the structural knowledge graph.' },
  { title: 'Theme Discovery', desc: 'Detect communities and label emerging themes.' },
  { title: 'Validation & Report', desc: 'Compute exposure, validate, and generate the research report.' }
]

const canCreate = computed(() => /^\d{4}-\d{2}-\d{2}$/.test(asOfDate.value.trim()))

const handleCreateRun = async () => {
  if (!canCreate.value || creating.value) return
  creating.value = true
  createError.value = ''
  try {
    const manifest = await createRun(asOfDate.value.trim())
    const runId = manifest.run_id
    saveRunToHistory(manifest)
    router.push({ name: 'Import', params: { runId } })
  } catch (err) {
    createError.value = err?.response?.data?.detail || err.message || 'Failed to create run'
  } finally {
    creating.value = false
  }
}

const openRun = (runId) => {
  router.push({ name: 'Import', params: { runId } })
}

const formatDate = (iso) => {
  if (!iso) return ''
  try {
    return new Date(iso).toLocaleString('en-US', {
      month: 'short', day: 'numeric', year: 'numeric',
      hour: 'numeric', minute: '2-digit', hour12: true
    })
  } catch {
    return iso
  }
}

const HISTORY_KEY = 'theme_engine_runs'

const saveRunToHistory = (manifest) => {
  const existing = loadHistory()
  const updated = [
    { run_id: manifest.run_id, as_of_date: manifest.as_of_date, created_at: manifest.created_at, discovery_frozen: manifest.discovery_frozen },
    ...existing.filter(r => r.run_id !== manifest.run_id)
  ].slice(0, 20)
  try {
    localStorage.setItem(HISTORY_KEY, JSON.stringify(updated))
  } catch {}
  allRuns.value = updated
}

const loadHistory = () => {
  try {
    return JSON.parse(localStorage.getItem(HISTORY_KEY) || '[]')
  } catch {
    return []
  }
}

onMounted(async () => {
  loadingRuns.value = true
  const local = loadHistory()
  allRuns.value = local
  try {
    const backend = await listRuns()
    const byId = new Map()
    for (const r of [...local, ...(backend || [])]) {
      byId.set(r.run_id, { ...byId.get(r.run_id), ...r })
    }
    allRuns.value = Array.from(byId.values())
      .sort((a, b) => (b.created_at || '').localeCompare(a.created_at || ''))
  } catch {
    allRuns.value = local
  } finally {
    loadingRuns.value = false
  }
})
</script>

<style scoped>
.admin-container {
  min-height: 100vh;
  background: var(--white);
  font-family: var(--font-sans);
  color: var(--black);
}

.navbar {
  height: 60px;
  background: var(--black);
  color: var(--white);
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 0 40px;
}

.nav-brand {
  display: flex;
  align-items: center;
  gap: 10px;
  font-family: var(--font-mono);
  font-weight: 800;
  letter-spacing: 1px;
  font-size: 1rem;
}

.back-link {
  color: #aaa;
  text-decoration: none;
  font-size: 0.85rem;
  transition: color 0.2s;
}

.back-link:hover {
  color: #fff;
}

.nav-sep {
  color: #555;
}

.nav-section {
  color: #fff;
}

.nav-tagline {
  font-family: var(--font-mono);
  font-size: 0.75rem;
  color: #666;
}

.main-content {
  max-width: 1400px;
  margin: 0 auto;
  padding: 60px 40px;
}

/* ── Page header ── */
.page-header {
  margin-bottom: 60px;
}

.tag-row {
  display: flex;
  align-items: center;
  gap: 15px;
  margin-bottom: 16px;
  font-family: var(--font-mono);
  font-size: 0.8rem;
}

.accent-tag {
  background: var(--black);
  color: var(--white);
  padding: 4px 10px;
  font-weight: 700;
  letter-spacing: 1px;
  font-size: 0.75rem;
}

.version-text {
  color: #999;
  font-weight: 500;
}

.page-title {
  font-size: 3rem;
  font-weight: 600;
  letter-spacing: -2px;
  margin-bottom: 12px;
}

.page-desc {
  color: var(--gray-text);
  font-size: 0.95rem;
  line-height: 1.6;
  max-width: 600px;
}

/* ── Two-col layout ── */
.dashboard-section {
  display: flex;
  gap: 60px;
  border-top: 1px solid var(--border);
  padding-top: 60px;
  align-items: flex-start;
  margin-bottom: 80px;
}

.left-panel {
  flex: 0.8;
}

.right-panel {
  flex: 1.2;
}

.panel-header {
  font-family: var(--font-mono);
  font-size: 0.8rem;
  color: #999;
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 20px;
}

.status-dot {
  color: var(--accent);
  font-size: 0.8rem;
}

.run-count {
  color: #bbb;
  font-size: 0.75rem;
}

.section-title {
  font-size: 1.8rem;
  font-weight: 600;
  margin: 0 0 12px 0;
}

.section-desc {
  color: var(--gray-text);
  margin-bottom: 24px;
  line-height: 1.6;
  font-size: 0.9rem;
}

/* ── Console box ── */
.console-box {
  border: 1px solid #CCC;
  padding: 8px;
}

.console-section {
  padding: 20px;
}

.console-section.btn-section {
  padding-top: 0;
}

.console-header {
  font-family: var(--font-mono);
  font-size: 0.75rem;
  color: #666;
  margin-bottom: 12px;
}

.console-label {
  font-family: var(--font-mono);
}

.date-input {
  width: 100%;
  border: 1px solid #DDD;
  background: #FAFAFA;
  padding: 14px 16px;
  font-family: var(--font-mono);
  font-size: 1rem;
  outline: none;
  transition: border-color 0.2s;
}

.date-input:focus {
  border-color: var(--accent);
}

.date-input:disabled {
  opacity: 0.6;
}

.start-btn {
  width: 100%;
  background: var(--black);
  color: var(--white);
  border: none;
  padding: 18px;
  font-family: var(--font-mono);
  font-weight: 700;
  font-size: 1rem;
  cursor: pointer;
  transition: all 0.2s;
  letter-spacing: 1px;
  text-align: left;
}

.start-btn:hover:not(:disabled) {
  background: var(--accent);
}

.start-btn:disabled {
  background: #E5E5E5;
  color: #999;
  cursor: not-allowed;
}

.error-msg {
  padding: 12px 20px;
  color: #c0392b;
  font-size: 0.85rem;
  font-family: var(--font-mono);
}

/* ── Runs list ── */
.loading-msg {
  color: #999;
  font-size: 0.85rem;
  font-family: var(--font-mono);
  padding: 20px 0;
}

.empty-runs {
  padding: 40px 20px;
  text-align: center;
  color: #999;
  font-size: 0.9rem;
  border: 1px dashed #E0E0E0;
}

.runs-list {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.run-card {
  border: 1px solid var(--border);
  padding: 16px 18px;
  cursor: pointer;
  transition: all 0.2s;
}

.run-card:hover {
  border-color: var(--accent);
  background: var(--accent-light);
}

.run-card-top {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 8px;
}

.run-id {
  font-family: var(--font-mono);
  font-size: 0.85rem;
  font-weight: 700;
}

.badge {
  display: inline-block;
  padding: 2px 8px;
  font-size: 0.7rem;
  font-family: var(--font-mono);
  font-weight: 700;
  border-radius: 2px;
}

.badge-frozen {
  background: #E8F5E9;
  color: #2E7D32;
}

.badge-active {
  background: #EEF2FF;
  color: #3730a3;
}

.run-meta {
  display: flex;
  align-items: center;
  gap: 24px;
  flex-wrap: wrap;
}

.meta-item {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.meta-label {
  font-size: 0.68rem;
  color: #999;
  font-family: var(--font-mono);
  text-transform: uppercase;
}

.meta-value {
  font-size: 0.82rem;
  font-weight: 500;
}

/* ── Pipeline section ── */
.pipeline-section {
  border-top: 1px solid var(--border);
  padding-top: 40px;
}

.diamond-icon {
  font-size: 1.1rem;
}

.workflow-list {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(260px, 1fr));
  gap: 20px;
  margin-top: 20px;
}

.workflow-item {
  display: flex;
  align-items: flex-start;
  gap: 16px;
  border: 1px solid var(--border);
  padding: 18px;
}

.step-num {
  font-family: var(--font-mono);
  font-weight: 700;
  color: var(--black);
  opacity: 0.3;
  font-size: 1.1rem;
  min-width: 28px;
}

.step-info {
  flex: 1;
}

.step-title {
  font-weight: 600;
  font-size: 0.9rem;
  margin-bottom: 4px;
}

.step-desc {
  font-size: 0.8rem;
  color: var(--gray-text);
  line-height: 1.45;
}

@media (max-width: 1024px) {
  .dashboard-section {
    flex-direction: column;
    gap: 40px;
  }
}
</style>
