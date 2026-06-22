<template>
  <div class="page-view">
    <RunNav :runId="runId" />

    <div class="content-area">
      <!-- Sidebar: community list -->
      <div class="sidebar left-sidebar">
        <div class="sidebar-header">
          <span class="sidebar-title">Communities</span>
          <span class="count-badge" v-if="communities.length">{{ communities.length }}</span>
        </div>
        <div v-if="loading" class="empty-msg">Loading...</div>
        <div v-else-if="error" class="error-msg">{{ error }}</div>
        <div v-else-if="!communities.length" class="empty-msg">
          Run Theme Discovery to populate communities.
        </div>
        <div v-else class="community-list">
          <div
            v-for="c in communities"
            :key="c.community_id"
            class="community-card"
            :class="{ active: selectedCommunity?.community_id === c.community_id }"
            @click="selectedCommunity = c"
          >
            <div class="comm-id">{{ c.community_id }}</div>
            <div class="comm-name">{{ c.theme_name || '(unnamed)' }}</div>
            <div class="comm-meta">
              <span class="comm-size">{{ c.size }} nodes</span>
              <span class="comm-density">density {{ c.density?.toFixed(2) }}</span>
            </div>
          </div>
        </div>
      </div>

      <!-- Main: community detail + radar -->
      <div class="main-panel">
        <div v-if="!selectedCommunity && communities.length" class="placeholder-msg">
          Select a community to view details.
        </div>

        <div v-else-if="selectedCommunity" class="community-detail">
          <!-- Theme header -->
          <div class="theme-header">
            <div class="theme-state-badge" :class="`state-${snapshotState?.toLowerCase()}`">
              {{ snapshotState || 'Unknown' }}
            </div>
            <h2 class="theme-name">{{ selectedCommunity.theme_name || selectedCommunity.community_id }}</h2>
            <p class="theme-summary">{{ selectedCommunity.theme_summary || selectedSnapshot?.summary || '' }}</p>
          </div>

          <!-- Metrics radar -->
          <div class="metrics-section">
            <div class="metrics-title">Theme Metrics</div>
            <div class="metrics-grid" v-if="communityMetrics">
              <div class="metric-card">
                <div class="metric-value">{{ pct(communityMetrics.strength) }}</div>
                <div class="metric-label">Strength</div>
                <div class="metric-bar">
                  <div class="metric-fill" :style="{ width: pct(communityMetrics.strength) }"></div>
                </div>
              </div>
              <div class="metric-card">
                <div class="metric-value">{{ pct(communityMetrics.cohesion) }}</div>
                <div class="metric-label">Cohesion</div>
                <div class="metric-bar">
                  <div class="metric-fill" :style="{ width: pct(communityMetrics.cohesion) }"></div>
                </div>
              </div>
              <div class="metric-card">
                <div class="metric-value">{{ pct(communityMetrics.saturation) }}</div>
                <div class="metric-label">Saturation</div>
                <div class="metric-bar">
                  <div class="metric-fill" :style="{ width: pct(communityMetrics.saturation) }"></div>
                </div>
              </div>
            </div>
            <div v-else class="no-metrics">No metrics available for this community.</div>
          </div>

          <!-- Top entities & companies -->
          <div class="two-col">
            <div class="list-section">
              <div class="list-title">Top Entities</div>
              <div class="tag-list">
                <span v-for="e in (selectedCommunity.top_entities || [])" :key="e" class="entity-tag">{{ e }}</span>
                <span v-if="!selectedCommunity.top_entities?.length" class="no-data">None</span>
              </div>
            </div>
            <div class="list-section">
              <div class="list-title">Top Companies</div>
              <div class="tag-list">
                <span v-for="c in (selectedCommunity.top_companies || [])" :key="c" class="company-tag">{{ c }}</span>
                <span v-if="!selectedCommunity.top_companies?.length" class="no-data">None</span>
              </div>
            </div>
          </div>

          <!-- Exposure table -->
          <div class="exposure-section" v-if="communityExposures.length">
            <div class="section-title">Company-Theme Exposure</div>
            <table class="exposure-table">
              <thead>
                <tr>
                  <th>Company</th>
                  <th>Exposure Score</th>
                  <th>Evidence Count</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="row in communityExposures" :key="row.company_id">
                  <td>{{ row.company_id }}</td>
                  <td>{{ Number(row.exposure_score || 0).toFixed(3) }}</td>
                  <td>{{ row.evidence_edge_count || 0 }}</td>
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
import { ref, computed, onMounted, watch } from 'vue'
import RunNav from '../components/RunNav.vue'
import { getCommunitiesJson, getThemeSnapshots, getThemeMetrics, getCompanyThemeExposure } from '../api/artifacts.js'

const props = defineProps({ runId: String })

const loading = ref(false)
const error = ref('')

const communities = ref([])
const snapshots = ref([])
const metrics = ref([])
const exposures = ref([])
const selectedCommunity = ref(null)

const selectedSnapshot = computed(() => {
  if (!selectedCommunity.value) return null
  return snapshots.value.find(s => s.community_id === selectedCommunity.value.community_id) || null
})

const snapshotState = computed(() => selectedSnapshot.value?.state || null)

const communityMetrics = computed(() => {
  if (!selectedCommunity.value) return null
  const snap = selectedSnapshot.value
  if (!snap) return null
  return metrics.value.find(m => m.theme_snapshot_id === snap.theme_snapshot_id) || null
})

const communityExposures = computed(() => {
  if (!selectedCommunity.value) return []
  return exposures.value
    .filter(e => e.community_id === selectedCommunity.value.community_id)
    .sort((a, b) => Number(b.exposure_score || 0) - Number(a.exposure_score || 0))
    .slice(0, 20)
})

const pct = (val) => {
  if (val == null) return 'N/A'
  return (Number(val) * 100).toFixed(1) + '%'
}

const loadData = async () => {
  loading.value = true
  error.value = ''
  try {
    const [commDoc, snapDoc, metricsRows, expRows] = await Promise.allSettled([
      getCommunitiesJson(props.runId),
      getThemeSnapshots(props.runId),
      getThemeMetrics(props.runId),
      getCompanyThemeExposure(props.runId)
    ])
    communities.value = commDoc.status === 'fulfilled' ? (commDoc.value.communities || []) : []
    snapshots.value = snapDoc.status === 'fulfilled' ? (snapDoc.value.snapshots || []) : []
    metrics.value = metricsRows.status === 'fulfilled' ? (metricsRows.value || []) : []
    exposures.value = expRows.status === 'fulfilled' ? (expRows.value || []) : []
  } catch (err) {
    error.value = err?.response?.data?.detail || err.message || 'Failed to load theme data'
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
  display: flex;
  overflow: hidden;
}

.left-sidebar {
  width: 280px;
  flex-shrink: 0;
  background: #FFF;
  border-right: 1px solid #EAEAEA;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

.sidebar-header {
  padding: 16px 20px;
  border-bottom: 1px solid #F0F0F0;
  display: flex;
  align-items: center;
  gap: 10px;
  flex-shrink: 0;
}

.sidebar-title {
  font-size: 12px;
  font-weight: 700;
  font-family: var(--font-mono);
  text-transform: uppercase;
  letter-spacing: 0.5px;
}

.count-badge {
  background: var(--accent, #1a56db);
  color: #FFF;
  border-radius: 10px;
  padding: 1px 7px;
  font-size: 10px;
  font-family: var(--font-mono);
  font-weight: 700;
}

.community-list {
  flex: 1;
  overflow-y: auto;
  padding: 12px;
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.community-card {
  padding: 12px 14px;
  border: 1px solid #EAEAEA;
  border-radius: 6px;
  cursor: pointer;
  transition: all 0.15s;
  background: #FFF;
}

.community-card:hover {
  border-color: var(--accent, #1a56db);
  background: #EEF2FF;
}

.community-card.active {
  border-color: var(--accent, #1a56db);
  background: #EEF2FF;
}

.comm-id {
  font-family: var(--font-mono);
  font-size: 10px;
  color: #999;
  margin-bottom: 4px;
}

.comm-name {
  font-size: 13px;
  font-weight: 600;
  color: #333;
  margin-bottom: 6px;
}

.comm-meta {
  display: flex;
  gap: 10px;
  font-size: 11px;
  color: #888;
}

.main-panel {
  flex: 1;
  overflow-y: auto;
  padding: 24px;
}

.placeholder-msg {
  text-align: center;
  color: #999;
  font-size: 13px;
  margin-top: 60px;
}

.theme-header {
  margin-bottom: 24px;
}

.theme-state-badge {
  display: inline-block;
  padding: 3px 10px;
  border-radius: 3px;
  font-size: 11px;
  font-family: var(--font-mono);
  font-weight: 700;
  text-transform: uppercase;
  margin-bottom: 10px;
  background: #EEF2FF;
  color: var(--accent, #1a56db);
}

.state-emerging { background: #ECFDF5; color: #065F46; }
.state-expanding { background: #EEF2FF; color: #3730A3; }
.state-mature { background: #F5F3FF; color: #6D28D9; }
.state-crowded { background: #FEF9C3; color: #92400E; }
.state-declining { background: #FEE2E2; color: #991B1B; }
.state-dormant { background: #F5F5F5; color: #666; }
.state-revived { background: #FFF7ED; color: #C2410C; }

.theme-name {
  font-size: 1.6rem;
  font-weight: 700;
  margin: 0 0 10px 0;
  color: var(--black);
}

.theme-summary {
  color: #555;
  font-size: 14px;
  line-height: 1.6;
  margin: 0;
}

.metrics-section {
  margin-bottom: 24px;
}

.metrics-title {
  font-size: 12px;
  font-weight: 700;
  font-family: var(--font-mono);
  text-transform: uppercase;
  color: #888;
  margin-bottom: 14px;
}

.metrics-grid {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 14px;
}

.metric-card {
  background: #FFF;
  border: 1px solid #E5E5E5;
  border-radius: 8px;
  padding: 16px;
}

.metric-value {
  font-family: var(--font-mono);
  font-size: 1.4rem;
  font-weight: 700;
  color: var(--black);
  margin-bottom: 4px;
}

.metric-label {
  font-size: 11px;
  color: #888;
  text-transform: uppercase;
  margin-bottom: 10px;
}

.metric-bar {
  height: 4px;
  background: #F0F0F0;
  border-radius: 2px;
  overflow: hidden;
}

.metric-fill {
  height: 100%;
  background: var(--accent, #1a56db);
  border-radius: 2px;
  transition: width 0.5s ease;
}

.two-col {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 20px;
  margin-bottom: 24px;
}

.list-section {
  background: #FFF;
  border: 1px solid #E5E5E5;
  border-radius: 8px;
  padding: 16px;
}

.list-title {
  font-size: 11px;
  font-weight: 700;
  color: #888;
  text-transform: uppercase;
  margin-bottom: 12px;
  font-family: var(--font-mono);
}

.tag-list {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

.entity-tag, .company-tag {
  padding: 4px 10px;
  border-radius: 12px;
  font-size: 12px;
}

.entity-tag {
  background: #EEF2FF;
  color: var(--accent, #1a56db);
  border: 1px solid #C7D2FE;
}

.company-tag {
  background: #ECFDF5;
  color: #065F46;
  border: 1px solid #A7F3D0;
}

.no-data { color: #CCC; font-size: 12px; }

.exposure-section {
  margin-bottom: 24px;
}

.section-title {
  font-size: 12px;
  font-weight: 700;
  font-family: var(--font-mono);
  text-transform: uppercase;
  color: #888;
  margin-bottom: 12px;
}

.exposure-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 12px;
  background: #FFF;
  border: 1px solid #E5E5E5;
  border-radius: 6px;
  overflow: hidden;
}

.exposure-table th {
  background: #F8F9FA;
  padding: 10px 14px;
  text-align: left;
  font-size: 11px;
  font-family: var(--font-mono);
  text-transform: uppercase;
  color: #888;
  border-bottom: 1px solid #E5E5E5;
}

.exposure-table td {
  padding: 10px 14px;
  border-bottom: 1px solid #F5F5F5;
  color: #333;
}

.exposure-table tr:last-child td { border-bottom: none; }

.no-metrics { color: #999; font-size: 12px; }

.empty-msg {
  padding: 20px;
  color: #999;
  font-size: 12px;
  line-height: 1.6;
}

.error-msg {
  margin: 12px;
  padding: 10px;
  color: #ef4444;
  background: #FEE2E2;
  border-radius: 4px;
  font-size: 12px;
  font-family: var(--font-mono);
}
</style>
