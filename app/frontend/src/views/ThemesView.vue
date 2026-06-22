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
            @click="selectCommunity(c)"
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

      <!-- Main: community detail + narrative panel -->
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

          <!-- ── CONNECT-THE-DOTS NARRATIVE PANEL ────────────────────── -->
          <div class="narrative-section">
            <div class="narrative-section-header">
              <div class="section-title">Connect-the-Dots Narrative</div>
              <button
                v-if="!narrative && !narrativeLoading && !narrativeError"
                class="load-narrative-btn"
                @click="loadNarrative"
              >
                Load narrative
              </button>
            </div>

            <!-- Loading (first call ~20s) -->
            <div v-if="narrativeLoading" class="narrative-loading">
              <div class="narrative-spinner"></div>
              <div class="narrative-loading-text">
                <span class="narrative-loading-title">Building narrative…</span>
                <span class="narrative-loading-sub">This can take up to 20 seconds on first access</span>
              </div>
            </div>

            <!-- LLM not configured (503) -->
            <div v-else-if="narrativeLlmUnconfigured" class="narrative-unavailable">
              <span class="unavailable-icon">ℹ</span>
              <div>
                <div class="unavailable-title">Narrative not available</div>
                <div class="unavailable-msg">The LLM service is not configured on this server. A system administrator can enable it in the backend settings.</div>
              </div>
            </div>

            <!-- Other error -->
            <div v-else-if="narrativeError" class="narrative-error">
              <span class="narrative-error-icon">⚠</span>
              <div>
                <div class="narrative-error-title">Could not load narrative</div>
                <div class="narrative-error-msg">{{ narrativeError }}</div>
                <button class="narrative-retry-btn" @click="loadNarrative">Retry</button>
              </div>
            </div>

            <!-- Narrative content -->
            <div v-else-if="narrative" class="narrative-body">
              <!-- Prose narrative -->
              <div class="narrative-prose">
                <p>{{ narrative.narrative }}</p>
              </div>

              <!-- Collapsible reasoning chain -->
              <div class="narrative-collapsible" v-if="narrative.reasoning_chain">
                <button
                  class="collapsible-toggle"
                  @click="reasoningOpen = !reasoningOpen"
                >
                  <span class="collapsible-label">Reasoning chain</span>
                  <span class="collapsible-arrow">{{ reasoningOpen ? '▲' : '▼' }}</span>
                </button>
                <div v-if="reasoningOpen" class="collapsible-content reasoning-content">
                  <pre class="reasoning-pre">{{ narrative.reasoning_chain }}</pre>
                </div>
              </div>

              <!-- Supporting relationships -->
              <div class="narrative-relationships" v-if="narrative.relationships?.length">
                <div class="relationships-title">Supporting relationships</div>
                <div class="relationships-list">
                  <div
                    v-for="(rel, idx) in narrative.relationships"
                    :key="idx"
                    class="relationship-row"
                  >
                    <div class="rel-edge">
                      <span class="rel-source">{{ rel.source }}</span>
                      <span class="rel-edge-type">{{ rel.edge_type }}</span>
                      <span class="rel-target">{{ rel.target }}</span>
                    </div>
                    <div v-if="rel.explanation" class="rel-explanation">{{ rel.explanation }}</div>
                    <div v-if="rel.evidence?.length" class="rel-evidence">
                      <span class="evidence-label">Evidence</span>
                      <span
                        v-for="(ev, ei) in rel.evidence.slice(0, 2)"
                        :key="ei"
                        class="evidence-snippet"
                      >"{{ typeof ev === 'string' ? ev : (ev.text || ev.snippet || JSON.stringify(ev)) }}"</span>
                      <span v-if="rel.evidence.length > 2" class="evidence-more">+{{ rel.evidence.length - 2 }} more</span>
                    </div>
                  </div>
                </div>
              </div>
            </div>

            <!-- Prompt to load (before first click) -->
            <div v-else class="narrative-prompt">
              <div class="narrative-prompt-text">
                A narrative connects this theme's sub-themes and surfaces the relationships linking them.
                Click "Load narrative" to generate it (requires LLM; first call takes ~20s).
              </div>
            </div>
          </div>
          <!-- ── END NARRATIVE PANEL ──────────────────────────────────── -->
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, watch } from 'vue'
import { useRoute } from 'vue-router'
import RunNav from '../components/RunNav.vue'
import { getCommunitiesJson, getThemeSnapshots, getThemeMetrics, getCompanyThemeExposure } from '../api/artifacts.js'
import { getCommunityNarrative } from '../api/themes.js'

const props = defineProps({ runId: String })
const route = useRoute()

const loading = ref(false)
const error = ref('')

const communities = ref([])
const snapshots = ref([])
const metrics = ref([])
const exposures = ref([])
const selectedCommunity = ref(null)

// ─── Narrative state ──────────────────────────────────────────────────────────
const narrative = ref(null)
const narrativeLoading = ref(false)
const narrativeError = ref('')
const narrativeLlmUnconfigured = ref(false)
const reasoningOpen = ref(false)

// ─── Community selection ──────────────────────────────────────────────────────
const selectCommunity = (c) => {
  selectedCommunity.value = c
  // Reset narrative state for new community
  narrative.value = null
  narrativeLoading.value = false
  narrativeError.value = ''
  narrativeLlmUnconfigured.value = false
  reasoningOpen.value = false
}

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

// ─── Narrative loading ────────────────────────────────────────────────────────
const loadNarrative = async () => {
  if (!selectedCommunity.value || narrativeLoading.value) return
  narrativeLoading.value = true
  narrativeError.value = ''
  narrativeLlmUnconfigured.value = false
  reasoningOpen.value = false
  try {
    const result = await getCommunityNarrative(props.runId, selectedCommunity.value.community_id)
    narrative.value = result
  } catch (err) {
    const status = err?.response?.status
    if (status === 503) {
      narrativeLlmUnconfigured.value = true
    } else {
      narrativeError.value = err?.response?.data?.detail || err.message || 'Failed to load narrative'
    }
  } finally {
    narrativeLoading.value = false
  }
}

// ─── Data loading ─────────────────────────────────────────────────────────────
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
    // Deep-link: auto-select the community passed from the landing (?community=...)
    const wanted = route.query.community
    if (wanted) {
      const found = communities.value.find(c => c.community_id === wanted)
      if (found) selectCommunity(found)
    }
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

/* ── Narrative section ── */
.narrative-section {
  margin-bottom: 32px;
  border: 1px solid #E5E5E5;
  border-radius: 8px;
  overflow: hidden;
  background: #FFF;
}

.narrative-section-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 14px 20px 12px;
  border-bottom: 1px solid #F0F0F0;
  background: #F8F9FA;
}

.narrative-section-header .section-title {
  margin-bottom: 0;
}

.load-narrative-btn {
  background: var(--accent, #1a56db);
  color: #fff;
  border: none;
  padding: 6px 14px;
  font-family: var(--font-mono);
  font-size: 0.78rem;
  cursor: pointer;
  border-radius: 4px;
  transition: opacity 0.15s;
}

.load-narrative-btn:hover {
  opacity: 0.85;
}

/* Loading state */
.narrative-loading {
  display: flex;
  align-items: center;
  gap: 16px;
  padding: 24px 20px;
}

.narrative-spinner {
  width: 28px;
  height: 28px;
  border: 3px solid #eee;
  border-top-color: var(--accent, #1a56db);
  border-radius: 50%;
  flex-shrink: 0;
  animation: spin 0.8s linear infinite;
}

@keyframes spin {
  to { transform: rotate(360deg); }
}

.narrative-loading-text {
  display: flex;
  flex-direction: column;
  gap: 3px;
}

.narrative-loading-title {
  font-size: 14px;
  font-weight: 600;
  color: #333;
}

.narrative-loading-sub {
  font-size: 12px;
  color: #999;
  font-family: var(--font-mono);
}

/* Unavailable (503) */
.narrative-unavailable {
  display: flex;
  align-items: flex-start;
  gap: 12px;
  padding: 20px;
  background: #F8F9FA;
  color: #666;
}

.unavailable-icon {
  font-size: 1.2rem;
  color: #999;
  flex-shrink: 0;
  margin-top: 1px;
}

.unavailable-title {
  font-size: 13px;
  font-weight: 600;
  color: #555;
  margin-bottom: 4px;
}

.unavailable-msg {
  font-size: 12px;
  color: #888;
  line-height: 1.5;
}

/* Error state */
.narrative-error {
  display: flex;
  align-items: flex-start;
  gap: 12px;
  padding: 20px;
  background: #FFF5F5;
}

.narrative-error-icon {
  font-size: 1.1rem;
  color: #ef4444;
  flex-shrink: 0;
  margin-top: 1px;
}

.narrative-error-title {
  font-size: 13px;
  font-weight: 600;
  color: #c0392b;
  margin-bottom: 4px;
}

.narrative-error-msg {
  font-size: 12px;
  color: #e74c3c;
  font-family: var(--font-mono);
  margin-bottom: 10px;
  line-height: 1.5;
}

.narrative-retry-btn {
  background: transparent;
  border: 1px solid #ef4444;
  color: #ef4444;
  padding: 5px 12px;
  font-size: 12px;
  font-family: var(--font-mono);
  cursor: pointer;
  border-radius: 3px;
  transition: all 0.15s;
}

.narrative-retry-btn:hover {
  background: #ef4444;
  color: #fff;
}

/* Prompt to load */
.narrative-prompt {
  padding: 20px;
}

.narrative-prompt-text {
  font-size: 13px;
  color: #999;
  line-height: 1.6;
}

/* Narrative body */
.narrative-body {
  padding: 20px;
  display: flex;
  flex-direction: column;
  gap: 20px;
}

.narrative-prose {
  font-size: 14px;
  line-height: 1.75;
  color: #222;
  background: #FAFBFF;
  border-left: 3px solid var(--accent, #1a56db);
  padding: 16px 18px;
  border-radius: 0 6px 6px 0;
}

.narrative-prose p {
  margin: 0;
}

/* Collapsible reasoning chain */
.narrative-collapsible {
  border: 1px solid #E5E5E5;
  border-radius: 6px;
  overflow: hidden;
}

.collapsible-toggle {
  width: 100%;
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 11px 16px;
  background: #F8F9FA;
  border: none;
  cursor: pointer;
  transition: background 0.12s;
}

.collapsible-toggle:hover {
  background: #EEF2FF;
}

.collapsible-label {
  font-size: 12px;
  font-weight: 700;
  font-family: var(--font-mono);
  text-transform: uppercase;
  letter-spacing: 0.5px;
  color: #555;
}

.collapsible-arrow {
  font-size: 10px;
  color: #aaa;
}

.collapsible-content {
  border-top: 1px solid #E5E5E5;
}

.reasoning-content {
  padding: 14px 16px;
  max-height: 320px;
  overflow-y: auto;
  background: #FAFAFA;
}

.reasoning-pre {
  font-family: var(--font-mono);
  font-size: 12px;
  color: #444;
  white-space: pre-wrap;
  word-break: break-word;
  line-height: 1.6;
  margin: 0;
}

/* Relationships list */
.narrative-relationships {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.relationships-title {
  font-size: 11px;
  font-weight: 700;
  font-family: var(--font-mono);
  text-transform: uppercase;
  color: #888;
  letter-spacing: 0.5px;
}

.relationships-list {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.relationship-row {
  background: #F8F9FA;
  border: 1px solid #E8E8E8;
  border-radius: 6px;
  padding: 12px 14px;
  display: flex;
  flex-direction: column;
  gap: 7px;
}

.rel-edge {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

.rel-source,
.rel-target {
  font-size: 13px;
  font-weight: 600;
  color: #222;
}

.rel-edge-type {
  font-family: var(--font-mono);
  font-size: 11px;
  background: #EEF2FF;
  color: #3730A3;
  border: 1px solid #C7D2FE;
  padding: 2px 8px;
  border-radius: 10px;
  white-space: nowrap;
}

.rel-explanation {
  font-size: 12px;
  color: #555;
  line-height: 1.5;
}

.rel-evidence {
  display: flex;
  flex-wrap: wrap;
  align-items: baseline;
  gap: 6px;
}

.evidence-label {
  font-family: var(--font-mono);
  font-size: 10px;
  text-transform: uppercase;
  color: #bbb;
  letter-spacing: 0.5px;
  flex-shrink: 0;
}

.evidence-snippet {
  font-size: 11px;
  color: #777;
  font-style: italic;
  background: #FFF;
  border: 1px solid #E5E5E5;
  padding: 3px 8px;
  border-radius: 3px;
  max-width: 400px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.evidence-more {
  font-family: var(--font-mono);
  font-size: 11px;
  color: #aaa;
}
</style>
