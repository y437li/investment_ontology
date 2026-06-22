<template>
  <div class="main-view">
    <!-- Header -->
    <header class="app-header">
      <div class="header-left">
        <div class="brand" @click="router.push('/')">THEME ENGINE</div>
        <div class="run-badge">{{ runId }}</div>
      </div>
      <div class="header-right">
        <nav class="view-nav">
          <router-link :to="`/runs/${runId}/import`" class="nav-link" active-class="active">Pipeline</router-link>
          <router-link :to="`/runs/${runId}/graph`" class="nav-link" active-class="active">Graph</router-link>
          <router-link :to="`/runs/${runId}/themes`" class="nav-link" active-class="active">Themes</router-link>
          <router-link :to="`/runs/${runId}/validation`" class="nav-link" active-class="active">Validation</router-link>
          <router-link :to="`/runs/${runId}/report`" class="nav-link" active-class="active">Report</router-link>
          <router-link :to="`/runs/${runId}/interaction`" class="nav-link" active-class="active">Evidence Q&A</router-link>
        </nav>
      </div>
    </header>

    <div class="content-area">
      <!-- Left: Step runner -->
      <div class="step-panel">
        <div class="scroll-container">

          <!-- Import step -->
          <div class="step-card" :class="stepClass('import')">
            <div class="card-header">
              <div class="step-info">
                <span class="step-num">01</span>
                <span class="step-title">Data Import</span>
              </div>
              <div class="step-status">
                <span class="badge" :class="badgeClass(steps.import.status)">{{ steps.import.status }}</span>
              </div>
            </div>
            <div class="card-content">
              <p class="api-note">POST /api/data/import</p>
              <p class="description">Ingest documents from the source directory; apply point-in-time gating.</p>
              <div class="input-group">
                <label>Documents Directory</label>
                <input v-model="importParams.documentsDir" placeholder="/path/to/documents" :disabled="steps.import.status === 'running'" />
              </div>
              <div class="input-group">
                <label>Source Manifest Path</label>
                <input v-model="importParams.manifestPath" placeholder="/path/to/source_manifest.csv" :disabled="steps.import.status === 'running'" />
              </div>
              <button class="action-btn" @click="runStep('import')" :disabled="!canRun('import')">
                <span v-if="steps.import.status === 'running'" class="spinner-sm"></span>
                {{ steps.import.status === 'running' ? 'Running...' : 'Run Step' }}
              </button>
              <div v-if="steps.import.result" class="result-box">
                <span v-if="steps.import.result.success">
                  Imported {{ steps.import.result.raw_documents }} docs,
                  quarantined {{ steps.import.result.quarantined }}.
                </span>
                <span v-else class="error-text">{{ steps.import.result.detail }}</span>
              </div>
            </div>
          </div>

          <!-- Clean -->
          <div class="step-card" :class="stepClass('clean')">
            <div class="card-header">
              <div class="step-info">
                <span class="step-num">02</span>
                <span class="step-title">Data Clean</span>
              </div>
              <span class="badge" :class="badgeClass(steps.clean.status)">{{ steps.clean.status }}</span>
            </div>
            <div class="card-content">
              <p class="api-note">POST /api/data/clean</p>
              <p class="description">Apply data quality filters; remove boilerplate and PII.</p>
              <button class="action-btn" @click="runStep('clean')" :disabled="!canRun('clean')">
                <span v-if="steps.clean.status === 'running'" class="spinner-sm"></span>
                {{ steps.clean.status === 'running' ? 'Running...' : 'Run Step' }}
              </button>
              <div v-if="steps.clean.result" class="result-box">
                <span v-if="steps.clean.result.success">
                  Included {{ steps.clean.result.included_documents }},
                  quarantined {{ steps.clean.result.quarantined_documents }}.
                </span>
                <span v-else class="error-text">{{ steps.clean.result.detail }}</span>
              </div>
            </div>
          </div>

          <!-- Chunk -->
          <div class="step-card" :class="stepClass('chunk')">
            <div class="card-header">
              <div class="step-info">
                <span class="step-num">03</span>
                <span class="step-title">Chunk</span>
              </div>
              <span class="badge" :class="badgeClass(steps.chunk.status)">{{ steps.chunk.status }}</span>
            </div>
            <div class="card-content">
              <p class="api-note">POST /api/data/chunk</p>
              <p class="description">Split documents into overlapping chunks for extraction.</p>
              <button class="action-btn" @click="runStep('chunk')" :disabled="!canRun('chunk')">
                <span v-if="steps.chunk.status === 'running'" class="spinner-sm"></span>
                {{ steps.chunk.status === 'running' ? 'Running...' : 'Run Step' }}
              </button>
              <div v-if="steps.chunk.result" class="result-box">
                <span v-if="steps.chunk.result.success">{{ steps.chunk.result.chunk_count }} chunks created.</span>
                <span v-else class="error-text">{{ steps.chunk.result.detail }}</span>
              </div>
            </div>
          </div>

          <!-- Extract -->
          <div class="step-card" :class="stepClass('extract')">
            <div class="card-header">
              <div class="step-info">
                <span class="step-num">04</span>
                <span class="step-title">Extraction</span>
              </div>
              <span class="badge" :class="badgeClass(steps.extract.status)">{{ steps.extract.status }}</span>
            </div>
            <div class="card-content">
              <p class="api-note">POST /api/extraction/run</p>
              <p class="description">Extract entities and edges from each chunk.</p>
              <button class="action-btn" @click="runStep('extract')" :disabled="!canRun('extract')">
                <span v-if="steps.extract.status === 'running'" class="spinner-sm"></span>
                {{ steps.extract.status === 'running' ? 'Running...' : 'Run Step' }}
              </button>
              <div v-if="steps.extract.result" class="result-box">
                <span v-if="steps.extract.result.success">
                  {{ steps.extract.result.entity_count }} entities,
                  {{ steps.extract.result.edge_count }} edges extracted.
                </span>
                <span v-else class="error-text">{{ steps.extract.result.detail }}</span>
              </div>
            </div>
          </div>

          <!-- Resolve -->
          <div class="step-card" :class="stepClass('resolve')">
            <div class="card-header">
              <div class="step-info">
                <span class="step-num">05</span>
                <span class="step-title">Entity Resolution</span>
              </div>
              <span class="badge" :class="badgeClass(steps.resolve.status)">{{ steps.resolve.status }}</span>
            </div>
            <div class="card-content">
              <p class="api-note">POST /api/extraction/resolve</p>
              <p class="description">Merge duplicate entity mentions into canonical entities.</p>
              <button class="action-btn" @click="runStep('resolve')" :disabled="!canRun('resolve')">
                <span v-if="steps.resolve.status === 'running'" class="spinner-sm"></span>
                {{ steps.resolve.status === 'running' ? 'Running...' : 'Run Step' }}
              </button>
              <div v-if="steps.resolve.result" class="result-box">
                <span v-if="steps.resolve.result.success">{{ steps.resolve.result.alias_count }} aliases resolved.</span>
                <span v-else class="error-text">{{ steps.resolve.result.detail }}</span>
              </div>
            </div>
          </div>

          <!-- Graph Build -->
          <div class="step-card" :class="stepClass('graph')">
            <div class="card-header">
              <div class="step-info">
                <span class="step-num">06</span>
                <span class="step-title">Graph Build</span>
              </div>
              <span class="badge" :class="badgeClass(steps.graph.status)">{{ steps.graph.status }}</span>
            </div>
            <div class="card-content">
              <p class="api-note">POST /api/graph/build</p>
              <p class="description">Construct the structural knowledge graph from resolved entities.</p>
              <button class="action-btn" @click="runStep('graph')" :disabled="!canRun('graph')">
                <span v-if="steps.graph.status === 'running'" class="spinner-sm"></span>
                {{ steps.graph.status === 'running' ? 'Running...' : 'Run Step' }}
              </button>
              <div v-if="steps.graph.result" class="result-box">
                <span v-if="steps.graph.result.success">
                  {{ steps.graph.result.node_count }} nodes,
                  {{ steps.graph.result.edge_count }} edges built.
                </span>
                <span v-else class="error-text">{{ steps.graph.result.detail }}</span>
              </div>
              <router-link v-if="steps.graph.status === 'done'" :to="`/runs/${runId}/graph`" class="view-link">
                View Graph Explorer →
              </router-link>
            </div>
          </div>

          <!-- Theme Discovery -->
          <div class="step-card" :class="stepClass('themes')">
            <div class="card-header">
              <div class="step-info">
                <span class="step-num">07</span>
                <span class="step-title">Theme Discovery</span>
              </div>
              <span class="badge" :class="badgeClass(steps.themes.status)">{{ steps.themes.status }}</span>
            </div>
            <div class="card-content">
              <p class="api-note">POST /api/themes/discover</p>
              <p class="description">Detect communities in the graph and label emerging themes.</p>
              <button class="action-btn" @click="runStep('themes')" :disabled="!canRun('themes')">
                <span v-if="steps.themes.status === 'running'" class="spinner-sm"></span>
                {{ steps.themes.status === 'running' ? 'Running...' : 'Run Step' }}
              </button>
              <div v-if="steps.themes.result" class="result-box">
                <span v-if="steps.themes.result.success">{{ steps.themes.result.community_count }} communities detected.</span>
                <span v-else class="error-text">{{ steps.themes.result.detail }}</span>
              </div>
              <router-link v-if="steps.themes.status === 'done'" :to="`/runs/${runId}/themes`" class="view-link">
                View Theme Radar →
              </router-link>
            </div>
          </div>

          <!-- Exposure -->
          <div class="step-card" :class="stepClass('exposure')">
            <div class="card-header">
              <div class="step-info">
                <span class="step-num">08</span>
                <span class="step-title">Exposure Compute</span>
              </div>
              <span class="badge" :class="badgeClass(steps.exposure.status)">{{ steps.exposure.status }}</span>
            </div>
            <div class="card-content">
              <p class="api-note">POST /api/exposure/compute</p>
              <p class="description">Compute company-theme exposure scores for each community.</p>
              <button class="action-btn" @click="runStep('exposure')" :disabled="!canRun('exposure')">
                <span v-if="steps.exposure.status === 'running'" class="spinner-sm"></span>
                {{ steps.exposure.status === 'running' ? 'Running...' : 'Run Step' }}
              </button>
              <div v-if="steps.exposure.result" class="result-box">
                <span v-if="steps.exposure.result.success">
                  {{ steps.exposure.result.theme_count }} themes,
                  {{ steps.exposure.result.company_theme_pair_count }} company-theme pairs.
                </span>
                <span v-else class="error-text">{{ steps.exposure.result.detail }}</span>
              </div>
            </div>
          </div>

          <!-- Freeze -->
          <div class="step-card" :class="stepClass('freeze')">
            <div class="card-header">
              <div class="step-info">
                <span class="step-num">09</span>
                <span class="step-title">Discovery Freeze</span>
              </div>
              <span class="badge" :class="badgeClass(steps.freeze.status)">{{ steps.freeze.status }}</span>
            </div>
            <div class="card-content">
              <p class="api-note">POST /api/discovery/freeze</p>
              <p class="description">Hash and lock all discovery artifacts. Required before validation.</p>
              <button class="action-btn" @click="runStep('freeze')" :disabled="!canRun('freeze')">
                <span v-if="steps.freeze.status === 'running'" class="spinner-sm"></span>
                {{ steps.freeze.status === 'running' ? 'Running...' : 'Run Step' }}
              </button>
              <div v-if="steps.freeze.result" class="result-box">
                <span v-if="steps.freeze.result.success">Discovery frozen successfully.</span>
                <span v-else class="error-text">{{ steps.freeze.result.detail }}</span>
              </div>
            </div>
          </div>

          <!-- Validation -->
          <div class="step-card" :class="stepClass('validation')">
            <div class="card-header">
              <div class="step-info">
                <span class="step-num">10</span>
                <span class="step-title">Validation</span>
              </div>
              <span class="badge" :class="badgeClass(steps.validation.status)">{{ steps.validation.status }}</span>
            </div>
            <div class="card-content">
              <p class="api-note">POST /api/validation/run</p>
              <p class="description">Run freeze-gated forward-return validation.</p>
              <button class="action-btn" @click="runStep('validation')" :disabled="!canRun('validation')">
                <span v-if="steps.validation.status === 'running'" class="spinner-sm"></span>
                {{ steps.validation.status === 'running' ? 'Running...' : 'Run Step' }}
              </button>
              <div v-if="steps.validation.result" class="result-box">
                <span v-if="steps.validation.result.success">
                  Status: {{ steps.validation.result.validation_status }}.
                  {{ steps.validation.result.validated_themes }} themes validated.
                </span>
                <span v-else class="error-text">{{ steps.validation.result.detail }}</span>
              </div>
              <router-link v-if="steps.validation.status === 'done'" :to="`/runs/${runId}/validation`" class="view-link">
                View Validation →
              </router-link>
            </div>
          </div>

          <!-- Report -->
          <div class="step-card" :class="stepClass('report')">
            <div class="card-header">
              <div class="step-info">
                <span class="step-num">11</span>
                <span class="step-title">Report Generate</span>
              </div>
              <span class="badge" :class="badgeClass(steps.report.status)">{{ steps.report.status }}</span>
            </div>
            <div class="card-content">
              <p class="api-note">POST /api/report/generate</p>
              <p class="description">Generate a research report citing discovered themes and evidence.</p>
              <button class="action-btn" @click="runStep('report')" :disabled="!canRun('report')">
                <span v-if="steps.report.status === 'running'" class="spinner-sm"></span>
                {{ steps.report.status === 'running' ? 'Running...' : 'Run Step' }}
              </button>
              <div v-if="steps.report.result" class="result-box">
                <span v-if="steps.report.result.success">Report generated: {{ steps.report.result.report_path }}</span>
                <span v-else class="error-text">{{ steps.report.result.detail }}</span>
              </div>
              <router-link v-if="steps.report.status === 'done'" :to="`/runs/${runId}/report`" class="view-link">
                View Report →
              </router-link>
            </div>
          </div>

        </div>

        <!-- System Log -->
        <div class="system-logs">
          <div class="log-header">
            <span class="log-title">SYSTEM LOG</span>
            <span class="log-id">{{ runId }}</span>
          </div>
          <div class="log-content" ref="logContent">
            <div class="log-line" v-for="(log, idx) in systemLogs" :key="idx">
              <span class="log-time">{{ log.time }}</span>
              <span class="log-msg">{{ log.msg }}</span>
            </div>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, nextTick } from 'vue'
import { useRouter } from 'vue-router'
import {
  importData, cleanData, chunkData, runExtraction, resolveEntities,
  buildGraph, discoverThemes, computeExposure, freezeDiscovery,
  runValidation, generateReport
} from '../api/pipeline.js'

const props = defineProps({ runId: String })
const router = useRouter()

const importParams = ref({
  documentsDir: '',
  manifestPath: ''
})

const logContent = ref(null)
const systemLogs = ref([])

const steps = ref({
  import: { status: 'pending', result: null },
  clean: { status: 'pending', result: null },
  chunk: { status: 'pending', result: null },
  extract: { status: 'pending', result: null },
  resolve: { status: 'pending', result: null },
  graph: { status: 'pending', result: null },
  themes: { status: 'pending', result: null },
  exposure: { status: 'pending', result: null },
  freeze: { status: 'pending', result: null },
  validation: { status: 'pending', result: null },
  report: { status: 'pending', result: null }
})

// Step order for dependency checking
const stepOrder = ['import', 'clean', 'chunk', 'extract', 'resolve', 'graph', 'themes', 'exposure', 'freeze', 'validation', 'report']

const canRun = (step) => {
  const s = steps.value[step]
  if (s.status === 'running') return false
  const idx = stepOrder.indexOf(step)
  if (idx === 0) return true
  const prev = stepOrder[idx - 1]
  return steps.value[prev].status === 'done'
}

const stepClass = (step) => {
  const status = steps.value[step].status
  return {
    active: status === 'running',
    completed: status === 'done',
    failed: status === 'failed'
  }
}

const badgeClass = (status) => {
  return {
    'badge-pending': status === 'pending',
    'badge-running': status === 'running',
    'badge-done': status === 'done',
    'badge-failed': status === 'failed'
  }
}

const addLog = (msg) => {
  const now = new Date()
  const time = now.toLocaleTimeString('en-US', { hour12: false }) + '.' + String(now.getMilliseconds()).padStart(3, '0')
  systemLogs.value.push({ time, msg })
  nextTick(() => {
    if (logContent.value) {
      logContent.value.scrollTop = logContent.value.scrollHeight
    }
  })
}

const stepApis = {
  import: async () => importData(props.runId, importParams.value.documentsDir, importParams.value.manifestPath),
  clean: async () => cleanData(props.runId),
  chunk: async () => chunkData(props.runId),
  extract: async () => runExtraction(props.runId),
  resolve: async () => resolveEntities(props.runId),
  graph: async () => buildGraph(props.runId),
  themes: async () => discoverThemes(props.runId),
  exposure: async () => computeExposure(props.runId),
  freeze: async () => freezeDiscovery(props.runId),
  validation: async () => runValidation(props.runId),
  report: async () => generateReport(props.runId)
}

const runStep = async (step) => {
  if (!canRun(step)) return
  steps.value[step].status = 'running'
  steps.value[step].result = null
  addLog(`Starting ${step}...`)
  try {
    const result = await stepApis[step]()
    steps.value[step].status = 'done'
    steps.value[step].result = result
    addLog(`${step} completed successfully.`)
  } catch (err) {
    steps.value[step].status = 'failed'
    const detail = err?.response?.data?.detail || err.message || 'Unknown error'
    steps.value[step].result = { success: false, detail }
    addLog(`${step} failed: ${detail}`)
  }
}
</script>

<style scoped>
.main-view {
  display: flex;
  flex-direction: column;
  height: 100vh;
  background: #F8F9FA;
}

.app-header {
  height: 56px;
  background: var(--black);
  color: var(--white);
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 0 24px;
  flex-shrink: 0;
  gap: 20px;
}

.header-left {
  display: flex;
  align-items: center;
  gap: 16px;
}

.brand {
  font-family: var(--font-mono);
  font-weight: 800;
  letter-spacing: 1px;
  cursor: pointer;
  font-size: 1rem;
}

.run-badge {
  font-family: var(--font-mono);
  font-size: 0.75rem;
  color: #888;
  padding: 2px 8px;
  border: 1px solid #333;
  border-radius: 2px;
}

.header-right {
  flex: 1;
  display: flex;
  justify-content: flex-end;
}

.view-nav {
  display: flex;
  gap: 4px;
}

.nav-link {
  color: #999;
  text-decoration: none;
  font-family: var(--font-mono);
  font-size: 0.78rem;
  padding: 6px 12px;
  border-radius: 2px;
  transition: all 0.2s;
}

.nav-link:hover, .nav-link.active {
  color: var(--white);
  background: #222;
}

.content-area {
  flex: 1;
  overflow: hidden;
  display: flex;
}

.step-panel {
  flex: 1;
  display: flex;
  flex-direction: column;
  overflow: hidden;
  background: #FAFAFA;
}

.scroll-container {
  flex: 1;
  overflow-y: auto;
  padding: 24px;
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.step-card {
  background: #FFF;
  border-radius: 8px;
  padding: 20px;
  box-shadow: 0 1px 4px rgba(0,0,0,0.04);
  border: 1px solid #EAEAEA;
  transition: border-color 0.2s;
}

.step-card.active {
  border-color: var(--accent);
  box-shadow: 0 2px 8px rgba(26, 86, 219, 0.08);
}

.step-card.completed {
  border-color: #10b981;
}

.step-card.failed {
  border-color: #ef4444;
}

.card-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 14px;
}

.step-info {
  display: flex;
  align-items: center;
  gap: 12px;
}

.step-num {
  font-family: var(--font-mono);
  font-size: 18px;
  font-weight: 700;
  color: #DDD;
}

.step-card.active .step-num,
.step-card.completed .step-num,
.step-card.failed .step-num {
  color: var(--black);
}

.step-title {
  font-weight: 600;
  font-size: 14px;
}

.badge {
  font-size: 10px;
  padding: 3px 8px;
  border-radius: 3px;
  font-weight: 700;
  text-transform: uppercase;
  font-family: var(--font-mono);
}

.badge-pending { background: #F5F5F5; color: #999; }
.badge-running { background: var(--accent); color: #FFF; }
.badge-done { background: #E8F5E9; color: #2E7D32; }
.badge-failed { background: #FEE2E2; color: #991B1B; }

.api-note {
  font-family: var(--font-mono);
  font-size: 10px;
  color: #999;
  margin-bottom: 6px;
}

.description {
  font-size: 12px;
  color: #666;
  line-height: 1.5;
  margin-bottom: 14px;
}

.input-group {
  margin-bottom: 12px;
}

.input-group label {
  display: block;
  font-size: 11px;
  color: #888;
  font-family: var(--font-mono);
  margin-bottom: 4px;
  text-transform: uppercase;
}

.input-group input {
  width: 100%;
  border: 1px solid #E0E0E0;
  background: #FAFAFA;
  padding: 8px 12px;
  font-family: var(--font-mono);
  font-size: 12px;
  outline: none;
  transition: border-color 0.2s;
  border-radius: 3px;
}

.input-group input:focus {
  border-color: var(--accent);
}

.action-btn {
  background: var(--black);
  color: var(--white);
  border: none;
  padding: 10px 20px;
  font-family: var(--font-mono);
  font-size: 12px;
  font-weight: 700;
  cursor: pointer;
  transition: opacity 0.2s;
  display: flex;
  align-items: center;
  gap: 8px;
  border-radius: 3px;
}

.action-btn:hover:not(:disabled) { opacity: 0.85; }
.action-btn:disabled { background: #CCC; cursor: not-allowed; }

.result-box {
  margin-top: 12px;
  padding: 10px 14px;
  background: #F8F9FA;
  border-radius: 4px;
  font-size: 12px;
  color: #333;
  font-family: var(--font-mono);
}

.error-text { color: #ef4444; }

.view-link {
  display: inline-block;
  margin-top: 12px;
  font-size: 12px;
  color: var(--accent);
  text-decoration: none;
  font-family: var(--font-mono);
  font-weight: 600;
}

.view-link:hover { text-decoration: underline; }

.spinner-sm {
  width: 12px;
  height: 12px;
  border: 2px solid rgba(255,255,255,0.3);
  border-top-color: #FFF;
  border-radius: 50%;
  animation: spin 1s linear infinite;
  flex-shrink: 0;
}

@keyframes spin { to { transform: rotate(360deg); } }

/* System Logs */
.system-logs {
  background: #000;
  color: #DDD;
  padding: 14px 16px;
  font-family: var(--font-mono);
  border-top: 1px solid #222;
  flex-shrink: 0;
}

.log-header {
  display: flex;
  justify-content: space-between;
  border-bottom: 1px solid #333;
  padding-bottom: 6px;
  margin-bottom: 6px;
  font-size: 10px;
  color: #888;
}

.log-content {
  display: flex;
  flex-direction: column;
  gap: 3px;
  height: 72px;
  overflow-y: auto;
}

.log-line {
  font-size: 11px;
  display: flex;
  gap: 12px;
  line-height: 1.5;
}

.log-time { color: #555; min-width: 90px; }
.log-msg { color: #CCC; word-break: break-all; }
</style>
