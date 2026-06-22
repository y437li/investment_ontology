<template>
  <div class="home-container">
    <!-- Navigation -->
    <nav class="navbar">
      <div class="nav-brand">THEME ENGINE</div>
      <div class="nav-links">
        <span class="nav-tagline">Investment Theme Discovery</span>
      </div>
    </nav>

    <div class="main-content">
      <!-- Hero Section -->
      <section class="hero-section">
        <div class="hero-left">
          <div class="tag-row">
            <span class="accent-tag">INVESTMENT RESEARCH</span>
            <span class="version-text">v0.1.0</span>
          </div>

          <h1 class="main-title">
            Investment<br>
            <span class="gradient-text">Theme Discovery</span>
          </h1>

          <div class="hero-desc">
            <p>
              Evidence-backed investment theme discovery from financial filings
              and research documents. Build knowledge graphs, detect communities,
              and compute company-theme exposure scores.
            </p>
            <p class="slogan-text">
              From documents to evidence-backed themes<span class="blinking-cursor">_</span>
            </p>
          </div>

          <div class="decoration-square"></div>
        </div>

        <div class="hero-right">
          <div class="steps-container">
            <div class="steps-header">
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
      </section>

      <!-- Create Run Section -->
      <section class="dashboard-section">
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
            <span class="status-dot">■</span> RECENT RUNS
          </div>

          <div v-if="recentRuns.length === 0" class="empty-runs">
            No runs yet. Create your first run to get started.
          </div>

          <div v-else class="runs-list">
            <div
              v-for="run in recentRuns"
              :key="run.run_id"
              class="run-card"
              @click="openRun(run.run_id)"
            >
              <div class="run-id">{{ run.run_id }}</div>
              <div class="run-meta">
                <span class="meta-item">
                  <span class="meta-label">Date</span>
                  <span class="meta-value">{{ run.as_of_date }}</span>
                </span>
                <span class="meta-item">
                  <span class="meta-label">Created</span>
                  <span class="meta-value">{{ formatDate(run.created_at) }}</span>
                </span>
                <span class="meta-item">
                  <span
                    class="badge"
                    :class="run.discovery_frozen ? 'badge-frozen' : 'badge-active'"
                  >
                    {{ run.discovery_frozen ? 'Frozen' : 'Active' }}
                  </span>
                </span>
              </div>
            </div>
          </div>
        </div>
      </section>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { createRun, getRunStatus } from '../api/runs.js'

const router = useRouter()

const asOfDate = ref('')
const creating = ref(false)
const createError = ref('')
const recentRuns = ref([])

const pipelineSteps = [
  { title: 'Data Import & Clean', desc: 'Ingest source documents and apply data quality gates.' },
  { title: 'Extraction & Resolution', desc: 'Extract entities and edges; resolve entity aliases.' },
  { title: 'Graph Build', desc: 'Construct the structural knowledge graph.' },
  { title: 'Theme Discovery', desc: 'Detect communities and label investment themes.' },
  { title: 'Validation & Report', desc: 'Compute exposure, validate, and generate the research report.' }
]

const canCreate = computed(() => {
  return /^\d{4}-\d{2}-\d{2}$/.test(asOfDate.value.trim())
})

const handleCreateRun = async () => {
  if (!canCreate.value || creating.value) return
  creating.value = true
  createError.value = ''
  try {
    const manifest = await createRun(asOfDate.value.trim())
    const runId = manifest.run_id
    // Save to local history
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
  ].slice(0, 20) // keep last 20
  try {
    localStorage.setItem(HISTORY_KEY, JSON.stringify(updated))
  } catch {}
  recentRuns.value = updated
}

const loadHistory = () => {
  try {
    return JSON.parse(localStorage.getItem(HISTORY_KEY) || '[]')
  } catch {
    return []
  }
}

onMounted(() => {
  recentRuns.value = loadHistory()
})
</script>

<style scoped>
.home-container {
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
  font-family: var(--font-mono);
  font-weight: 800;
  letter-spacing: 1px;
  font-size: 1.2rem;
}

.nav-tagline {
  font-family: var(--font-mono);
  font-size: 0.8rem;
  color: #999;
}

.main-content {
  max-width: 1400px;
  margin: 0 auto;
  padding: 60px 40px;
}

.hero-section {
  display: flex;
  justify-content: space-between;
  gap: 60px;
  margin-bottom: 80px;
}

.hero-left {
  flex: 1;
}

.tag-row {
  display: flex;
  align-items: center;
  gap: 15px;
  margin-bottom: 25px;
  font-family: var(--font-mono);
  font-size: 0.8rem;
}

.accent-tag {
  background: var(--accent);
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

.main-title {
  font-size: 4rem;
  line-height: 1.2;
  font-weight: 600;
  margin: 0 0 40px 0;
  letter-spacing: -2px;
}

.gradient-text {
  background: linear-gradient(90deg, #1a56db 0%, #7c3aed 100%);
  -webkit-background-clip: text;
  -webkit-text-fill-color: transparent;
  display: inline-block;
}

.hero-desc {
  font-size: 1rem;
  line-height: 1.8;
  color: var(--gray-text);
  max-width: 580px;
  margin-bottom: 50px;
}

.hero-desc p {
  margin-bottom: 1.2rem;
}

.slogan-text {
  font-size: 1.1rem;
  font-weight: 600;
  color: var(--black);
  border-left: 3px solid var(--accent);
  padding-left: 15px;
  margin-top: 10px;
}

.blinking-cursor {
  color: var(--accent);
  animation: blink 1s step-end infinite;
  font-weight: 700;
}

@keyframes blink {
  0%, 100% { opacity: 1; }
  50% { opacity: 0; }
}

.decoration-square {
  width: 16px;
  height: 16px;
  background: var(--accent);
  margin-top: 20px;
}

.hero-right {
  flex: 0.9;
}

.steps-container {
  border: 1px solid var(--border);
  padding: 30px;
}

.steps-header {
  font-family: var(--font-mono);
  font-size: 0.8rem;
  color: #999;
  margin-bottom: 25px;
  display: flex;
  align-items: center;
  gap: 8px;
}

.workflow-list {
  display: flex;
  flex-direction: column;
  gap: 20px;
}

.workflow-item {
  display: flex;
  align-items: flex-start;
  gap: 20px;
}

.step-num {
  font-family: var(--font-mono);
  font-weight: 700;
  color: var(--black);
  opacity: 0.3;
  min-width: 28px;
}

.step-info {
  flex: 1;
}

.step-title {
  font-weight: 600;
  font-size: 0.95rem;
  margin-bottom: 4px;
}

.step-desc {
  font-size: 0.82rem;
  color: var(--gray-text);
}

/* Dashboard */
.dashboard-section {
  display: flex;
  gap: 60px;
  border-top: 1px solid var(--border);
  padding-top: 60px;
  align-items: flex-start;
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

.section-title {
  font-size: 1.8rem;
  font-weight: 600;
  margin: 0 0 15px 0;
}

.section-desc {
  color: var(--gray-text);
  margin-bottom: 25px;
  line-height: 1.6;
  font-size: 0.9rem;
}

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
  gap: 12px;
}

.run-card {
  border: 1px solid var(--border);
  padding: 18px 20px;
  cursor: pointer;
  transition: all 0.2s;
}

.run-card:hover {
  border-color: var(--accent);
  background: var(--accent-light);
}

.run-id {
  font-family: var(--font-mono);
  font-size: 0.9rem;
  font-weight: 700;
  margin-bottom: 10px;
}

.run-meta {
  display: flex;
  align-items: center;
  gap: 20px;
  flex-wrap: wrap;
}

.meta-item {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.meta-label {
  font-size: 0.7rem;
  color: #999;
  font-family: var(--font-mono);
  text-transform: uppercase;
}

.meta-value {
  font-size: 0.85rem;
  font-weight: 500;
}

.badge {
  display: inline-block;
  padding: 3px 10px;
  font-size: 0.72rem;
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

@media (max-width: 1024px) {
  .hero-section, .dashboard-section {
    flex-direction: column;
    gap: 40px;
  }
}
</style>
