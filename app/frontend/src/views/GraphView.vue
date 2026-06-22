<template>
  <div class="page-view">
    <RunNav :runId="runId" />
    <div class="content-area">
      <div class="graph-wrapper">
        <GraphPanel
          :graphData="graphData"
          :loading="loading"
          @refresh="loadGraph"
        />
      </div>
      <div class="sidebar">
        <div class="sidebar-header">
          <span class="sidebar-title">Graph Stats</span>
        </div>
        <div v-if="graphData" class="stats-grid">
          <div class="stat-card">
            <span class="stat-value">{{ graphData.nodes?.length ?? 0 }}</span>
            <span class="stat-label">Nodes</span>
          </div>
          <div class="stat-card">
            <span class="stat-value">{{ graphData.edges?.length ?? 0 }}</span>
            <span class="stat-label">Edges</span>
          </div>
          <div class="stat-card">
            <span class="stat-value">{{ graphData.community_input_edges?.length ?? 0 }}</span>
            <span class="stat-label">Community Edges</span>
          </div>
        </div>
        <div v-if="graphData" class="meta-block">
          <div class="meta-row">
            <span class="meta-label">Run ID</span>
            <span class="meta-value mono">{{ graphData.run_id }}</span>
          </div>
          <div class="meta-row">
            <span class="meta-label">As-of Date</span>
            <span class="meta-value">{{ graphData.as_of_date }}</span>
          </div>
          <div class="meta-row">
            <span class="meta-label">Schema Version</span>
            <span class="meta-value">{{ graphData.schema_version }}</span>
          </div>
        </div>
        <div v-if="error" class="error-msg">{{ error }}</div>
        <div v-if="!graphData && !loading && !error" class="empty-msg">
          Run the Graph Build step in the pipeline to generate graph data.
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import RunNav from '../components/RunNav.vue'
import GraphPanel from '../components/GraphPanel.vue'
import { getGraphJson } from '../api/artifacts.js'

const props = defineProps({ runId: String })

const graphData = ref(null)
const loading = ref(false)
const error = ref('')

const loadGraph = async () => {
  loading.value = true
  error.value = ''
  try {
    graphData.value = await getGraphJson(props.runId)
  } catch (err) {
    error.value = err?.response?.data?.detail || err.message || 'Failed to load graph'
    graphData.value = null
  } finally {
    loading.value = false
  }
}

onMounted(loadGraph)
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
  display: flex;
  overflow: hidden;
}

.graph-wrapper {
  flex: 1;
  position: relative;
  overflow: hidden;
}

.sidebar {
  width: 260px;
  flex-shrink: 0;
  background: #FFF;
  border-left: 1px solid #EAEAEA;
  display: flex;
  flex-direction: column;
  overflow-y: auto;
  padding: 20px;
}

.sidebar-header {
  margin-bottom: 20px;
}

.sidebar-title {
  font-size: 12px;
  font-weight: 700;
  font-family: var(--font-mono);
  color: #333;
  text-transform: uppercase;
  letter-spacing: 0.5px;
}

.stats-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 10px;
  margin-bottom: 20px;
}

.stat-card {
  background: #F8F9FA;
  border: 1px solid #E5E5E5;
  padding: 14px 12px;
  text-align: center;
  border-radius: 4px;
}

.stat-value {
  display: block;
  font-family: var(--font-mono);
  font-size: 22px;
  font-weight: 700;
  color: var(--black);
}

.stat-label {
  display: block;
  font-size: 9px;
  color: #999;
  text-transform: uppercase;
  margin-top: 4px;
  font-family: var(--font-mono);
}

.meta-block {
  display: flex;
  flex-direction: column;
  gap: 12px;
  border-top: 1px solid #F0F0F0;
  padding-top: 16px;
}

.meta-row {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.meta-label {
  font-size: 10px;
  color: #999;
  font-family: var(--font-mono);
  text-transform: uppercase;
}

.meta-value {
  font-size: 12px;
  color: #333;
}

.meta-value.mono {
  font-family: var(--font-mono);
  font-size: 10px;
  word-break: break-all;
}

.error-msg {
  color: #ef4444;
  font-size: 12px;
  font-family: var(--font-mono);
  padding: 10px;
  background: #FEE2E2;
  border-radius: 4px;
}

.empty-msg {
  color: #999;
  font-size: 12px;
  line-height: 1.6;
}
</style>
