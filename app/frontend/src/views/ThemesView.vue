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
              <div class="metric-card" v-if="communityMetrics.strength">
                <div class="metric-value">{{ pct(communityMetrics.strength) }}</div>
                <div class="metric-label">Strength</div>
                <div class="metric-bar">
                  <div class="metric-fill" :style="{ width: pct(communityMetrics.strength) }"></div>
                </div>
              </div>
              <div class="metric-card" v-if="communityMetrics.cohesion">
                <div class="metric-value">{{ pct(communityMetrics.cohesion) }}</div>
                <div class="metric-label">Cohesion</div>
                <div class="metric-bar">
                  <div class="metric-fill" :style="{ width: pct(communityMetrics.cohesion) }"></div>
                </div>
              </div>
              <div class="metric-card" v-if="communityMetrics.saturation">
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

              <!-- ── DERIVATION CHAIN (推演) PANEL ───────────────────────── -->
              <div
                class="derivation-section"
                v-if="narrative.reasoning_steps?.length"
              >
                <!-- Section header with Walk-the-chain controls -->
                <div class="derivation-header">
                  <div class="derivation-title-row">
                    <span class="derivation-title">Derivation</span>
                    <span class="derivation-count">{{ narrative.reasoning_steps.length }} steps</span>
                  </div>
                  <!-- Walk the chain controls -->
                  <div class="walk-controls">
                    <button
                      class="walk-btn"
                      :disabled="walkStep <= 0"
                      @click="walkPrev"
                      title="Previous step"
                    >&#8592;</button>
                    <span class="walk-label">
                      <template v-if="walkStep >= 0">
                        {{ walkStep + 1 }} / {{ narrative.reasoning_steps.length }}
                      </template>
                      <template v-else>Walk the chain</template>
                    </span>
                    <button
                      class="walk-btn"
                      :disabled="walkStep >= narrative.reasoning_steps.length - 1"
                      @click="walkNext"
                      title="Next step"
                    >&#8594;</button>
                    <button
                      class="walk-reset-btn"
                      v-if="walkStep >= 0"
                      @click="walkReset"
                      title="Clear highlight"
                    >&#10005;</button>
                  </div>
                </div>

                <!-- Ordered step list -->
                <div class="derivation-list">
                  <div
                    v-for="step in sortedReasoningSteps"
                    :key="step.order"
                    class="derivation-row"
                    :class="{
                      'derivation-row--active': activeDerivationStep === step.order,
                      'derivation-row--dimmed': activeDerivationStep !== null && activeDerivationStep !== step.order
                    }"
                    @mouseenter="hoverDerivationStep(step)"
                    @mouseleave="unhoverDerivationStep"
                    @click="clickDerivationStep(step)"
                  >
                    <div class="derivation-step-num">{{ step.order }}</div>
                    <div class="derivation-step-body">
                      <div class="derivation-step-edge">
                        <span class="deriv-source">{{ step.source }}</span>
                        <span class="deriv-arrow">--</span>
                        <span class="deriv-edge-type">{{ step.edge_type }}</span>
                        <span class="deriv-arrow">--&gt;</span>
                        <span class="deriv-target">{{ step.target }}</span>
                        <span
                          v-if="step.provenance"
                          class="deriv-prov"
                          :class="`prov-${step.provenance}`"
                          :title="step.provenance === 'document_stated' ? 'Backed by document evidence' : 'Model inference (not directly stated)'"
                        >{{ step.provenance === 'document_stated' ? 'evidence' : 'inferred' }}</span>
                      </div>
                      <div class="derivation-step-claim">{{ step.claim }}</div>
                    </div>
                  </div>
                </div>
              </div>
              <!-- ── END DERIVATION CHAIN ─────────────────────────────── -->

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
                      <a
                        v-if="rel.evidence_chunk_ids?.length"
                        class="read-source-link"
                        @click="openSource(rel.evidence_chunk_ids[0])"
                      >read full source →</a>
                    </div>
                  </div>
                </div>
              </div>

              <!-- ── INTERACTIVE SUBGRAPH ────────────────────────────────── -->
              <div class="subgraph-section" v-if="narrative.relationships?.length">
                <div class="subgraph-header">
                  <div class="subgraph-title">Relationship Graph</div>
                  <span class="subgraph-hint">Click a node to view its profile</span>
                </div>
                <div class="subgraph-container" ref="subgraphContainer">
                  <LayeredGraph :nodes="sgNodes" :edges="sgEdges" :active-hop="hopHighlight" @node-click="(d) => fetchNodeProfile(d.id)" />
                </div>
              </div>
              <!-- ── END SUBGRAPH ─────────────────────────────────────────── -->
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

      <!-- ── NODE PROFILE PANEL (right sidebar) ──────────────────────── -->
      <div class="sidebar right-sidebar" v-if="nodeProfileOpen">
        <div class="sidebar-header">
          <span class="sidebar-title">Entity Profile</span>
          <button class="profile-close-btn" @click="closeNodeProfile" title="Close">×</button>
        </div>

        <!-- Loading -->
        <div v-if="nodeProfileLoading" class="profile-loading">
          <div class="profile-spinner"></div>
          <span class="profile-loading-text">Loading profile…</span>
        </div>

        <!-- Error -->
        <div v-else-if="nodeProfileError" class="profile-error">
          <span class="profile-error-icon">⚠</span>
          <div>
            <div class="profile-error-title">Could not load profile</div>
            <div class="profile-error-msg">{{ nodeProfileError }}</div>
            <button class="profile-retry-btn" @click="retryNodeProfile">Retry</button>
          </div>
        </div>

        <!-- Profile content -->
        <div v-else-if="nodeProfile" class="profile-content">
          <!-- Identity -->
          <div class="profile-identity">
            <div class="profile-type-row">
              <span class="profile-type-badge">{{ nodeProfile.entity_type }}</span>
              <span v-if="nodeProfile.level" class="profile-level">{{ nodeProfile.level }}</span>
            </div>
            <h3 class="profile-name">{{ nodeProfile.name || nodeProfile.entity_id }}</h3>
            <p v-if="nodeProfile.definition" class="profile-definition">{{ nodeProfile.definition }}</p>
          </div>

          <!-- Stats -->
          <div class="profile-stats">
            <div class="profile-stat">
              <span class="stat-num">{{ nodeProfile.evidence_count ?? '—' }}</span>
              <span class="stat-label">evidence</span>
            </div>
            <div class="profile-stat">
              <span class="stat-num">{{ nodeProfile.degree ?? '—' }}</span>
              <span class="stat-label">connections</span>
            </div>
            <div class="profile-stat" v-if="nodeProfile.first_seen_at">
              <span class="stat-num stat-date">{{ formatDate(nodeProfile.first_seen_at) }}</span>
              <span class="stat-label">first seen</span>
            </div>
          </div>

          <!-- Why present -->
          <div class="profile-section" v-if="nodeProfile.why_present?.length">
            <div class="profile-section-title">Why it appears in this theme</div>
            <div class="why-list">
              <div
                v-for="(wp, i) in nodeProfile.why_present"
                :key="i"
                class="why-row"
              >
                <div class="why-edge">
                  <span class="why-direction" :class="`dir-${wp.direction}`">{{ wp.direction }}</span>
                  <span class="why-edge-type">{{ wp.edge_type }}</span>
                  <span class="why-other">{{ wp.other }}</span>
                </div>
                <div v-if="wp.explanation" class="why-explanation">{{ wp.explanation }}</div>
              </div>
            </div>
          </div>

          <!-- Related entities -->
          <div class="profile-section" v-if="nodeProfile.related_entities?.length">
            <div class="profile-section-title">Related entities</div>
            <div class="related-tags">
              <span
                v-for="re in nodeProfile.related_entities"
                :key="re"
                class="related-tag"
              >{{ re }}</span>
            </div>
          </div>
        </div>
      </div>
      <!-- ── END NODE PROFILE PANEL ──────────────────────────────────── -->
    </div>
  </div>
    <!-- Full-text source viewer -->
    <div v-if="sourceDoc || sourceLoading" class="source-modal" @click.self="closeSource">
      <div class="source-card">
        <button class="source-close" @click="closeSource">×</button>
        <div v-if="sourceLoading" class="source-loading">Loading source…</div>
        <template v-else-if="sourceDoc">
          <div class="source-title">{{ sourceDoc.document?.title || 'Source document' }}</div>
          <div class="source-meta">
            <span v-if="sourceDoc.document?.source">{{ sourceDoc.document.source }}</span>
            <span v-if="sourceDoc.document?.published_at"> · {{ sourceDoc.document.published_at }}</span>
            <span v-if="sourceDoc.document?.document_type"> · {{ sourceDoc.document.document_type }}</span>
            <a v-if="sourceDoc.document?.source_url" :href="sourceDoc.document.source_url" target="_blank" rel="noopener" class="source-orig">open original ↗</a>
          </div>
          <div class="source-text">{{ sourceDoc.document_text }}</div>
        </template>
      </div>
    </div>
  </template>

<script setup>
import { ref, computed, onMounted, watch, nextTick } from 'vue'
import { useRoute } from 'vue-router'
import * as d3 from 'd3'
import RunNav from '../components/RunNav.vue'
import { getCommunitiesJson, getThemeSnapshots, getThemeMetrics, getCompanyThemeExposure } from '../api/artifacts.js'
import { getCommunityNarrative, getNodeProfile, getChunkSource } from '../api/themes.js'
import LayeredGraph from '../components/LayeredGraph.vue'

const props = defineProps({ runId: String })
const route = useRoute()

const loading = ref(false)
const error = ref('')

const communities = ref([])
const snapshots = ref([])
const metrics = ref([])
const exposures = ref([])
const selectedCommunity = ref(null)

// Full-text source viewer
const sourceDoc = ref(null)
const sourceLoading = ref(false)
async function openSource(chunkId) {
  if (!chunkId) return
  sourceLoading.value = true
  sourceDoc.value = null
  try {
    sourceDoc.value = await getChunkSource(props.runId, chunkId)
  } catch (e) {
    sourceDoc.value = { document: { title: 'Source unavailable' }, document_text: e?.response?.data?.detail || 'Failed to load source.' }
  } finally {
    sourceLoading.value = false
  }
}
function closeSource() { sourceDoc.value = null; sourceLoading.value = false }

// ─── Narrative state ──────────────────────────────────────────────────────────
const narrative = ref(null)
const narrativeLoading = ref(false)
const narrativeError = ref('')
const narrativeLlmUnconfigured = ref(false)
const reasoningOpen = ref(false)

// ─── Derivation chain state ───────────────────────────────────────────────────
// activeDerivationStep: the step.order currently highlighted (null = none)
const activeDerivationStep = ref(null)
// walkStep: current walk position (-1 = not started, 0..n-1 = step index)
const walkStep = ref(-1)

const sortedReasoningSteps = computed(() => {
  if (!narrative.value?.reasoning_steps?.length) return []
  return [...narrative.value.reasoning_steps].sort((a, b) => a.order - b.order)
})

// ─── Subgraph refs ────────────────────────────────────────────────────────────
const subgraphContainer = ref(null)
const subgraphSvg = ref(null)
let subgraphSimulation = null
const hopHighlight = ref(null)   // {source_id,target_id} driving the shared LayeredGraph

// ─── Node profile state ───────────────────────────────────────────────────────
const nodeProfileOpen = ref(false)
const nodeProfileLoading = ref(false)
const nodeProfileError = ref('')
const nodeProfile = ref(null)
let lastProfileEntityId = null

// ─── Community selection ──────────────────────────────────────────────────────
const selectCommunity = (c) => {
  selectedCommunity.value = c
  // Reset narrative state for new community
  narrative.value = null
  narrativeLoading.value = false
  narrativeError.value = ''
  narrativeLlmUnconfigured.value = false
  reasoningOpen.value = false
  activeDerivationStep.value = null
  walkStep.value = -1
  clearSubgraph()
  closeNodeProfile()
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
  activeDerivationStep.value = null
  walkStep.value = -1
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

// ─── Derivation chain interactivity ──────────────────────────────────────────

/**
 * Highlight a derivation step on the subgraph by emphasizing source/target nodes
 * and the edge between them, dimming everything else.
 * @param {object|null} step - reasoning step or null to restore
 */
const highlightDerivationOnGraph = (step) => {
  // Drive the shared LayeredGraph via its activeHop prop. The legacy d3 below is dead
  // (subgraphSvg stays null now that the inline <svg> is the LayeredGraph component).
  hopHighlight.value = step ? { source_id: String(step.source_id ?? step.source), target_id: String(step.target_id ?? step.target) } : null
  if (!subgraphSvg.value) return
  const svg = d3.select(subgraphSvg.value)

  if (!step) {
    // Restore all nodes and edges to default appearance
    svg.selectAll('circle')
      .attr('opacity', 1)
      .attr('stroke', '#fff')
      .attr('stroke-width', 2)
      .attr('r', 9)
    svg.selectAll('line')
      .attr('opacity', 1)
      .attr('stroke', '#d0d0d0')
      .attr('stroke-width', 1.5)
    svg.selectAll('text.nl')
      .attr('opacity', 1)
    svg.selectAll('text.el')
      .attr('opacity', 1)
    svg.selectAll('rect.elb')
      .attr('opacity', 1)
    return
  }

  const srcId = step.source_id != null ? String(step.source_id) : step.source
  const tgtId = step.target_id != null ? String(step.target_id) : step.target

  // Dim all nodes, then highlight matched ones
  svg.selectAll('circle')
    .attr('opacity', (d) => {
      if (d.id === srcId || d.id === tgtId) return 1
      return 0.18
    })
    .attr('stroke', (d) => {
      if (d.id === srcId) return '#f59e0b'   // amber for source
      if (d.id === tgtId) return '#10b981'   // green for target
      return '#fff'
    })
    .attr('stroke-width', (d) => {
      if (d.id === srcId || d.id === tgtId) return 3.5
      return 2
    })
    .attr('r', (d) => {
      if (d.id === srcId || d.id === tgtId) return 11
      return 9
    })

  // Dim all node labels
  svg.selectAll('text.nl')
    .attr('opacity', (d) => {
      if (d.id === srcId || d.id === tgtId) return 1
      return 0.18
    })

  // Highlight the edge between source and target; dim others
  svg.selectAll('line')
    .attr('opacity', (d) => {
      const s = typeof d.source === 'object' ? d.source.id : d.source
      const t = typeof d.target === 'object' ? d.target.id : d.target
      if ((s === srcId && t === tgtId) || (s === tgtId && t === srcId)) return 1
      return 0.1
    })
    .attr('stroke', (d) => {
      const s = typeof d.source === 'object' ? d.source.id : d.source
      const t = typeof d.target === 'object' ? d.target.id : d.target
      if ((s === srcId && t === tgtId) || (s === tgtId && t === srcId)) return '#f59e0b'
      return '#d0d0d0'
    })
    .attr('stroke-width', (d) => {
      const s = typeof d.source === 'object' ? d.source.id : d.source
      const t = typeof d.target === 'object' ? d.target.id : d.target
      if ((s === srcId && t === tgtId) || (s === tgtId && t === srcId)) return 3
      return 1.5
    })

  // Dim edge labels
  svg.selectAll('text.el')
    .attr('opacity', (d) => {
      const s = typeof d.source === 'object' ? d.source.id : d.source
      const t = typeof d.target === 'object' ? d.target.id : d.target
      if ((s === srcId && t === tgtId) || (s === tgtId && t === srcId)) return 1
      return 0.1
    })
  svg.selectAll('rect.elb')
    .attr('opacity', (d) => {
      const s = typeof d.source === 'object' ? d.source.id : d.source
      const t = typeof d.target === 'object' ? d.target.id : d.target
      if ((s === srcId && t === tgtId) || (s === tgtId && t === srcId)) return 1
      return 0.1
    })
}

const hoverDerivationStep = (step) => {
  // Only highlight on hover if no step is actively clicked/walked
  if (activeDerivationStep.value === null) {
    highlightDerivationOnGraph(step)
  }
}

const unhoverDerivationStep = () => {
  // Only restore on unhover if no step is actively clicked/walked
  if (activeDerivationStep.value === null) {
    highlightDerivationOnGraph(null)
  }
}

const clickDerivationStep = (step) => {
  if (activeDerivationStep.value === step.order) {
    // Clicking the active step deselects it
    activeDerivationStep.value = null
    walkStep.value = -1
    highlightDerivationOnGraph(null)
  } else {
    activeDerivationStep.value = step.order
    // Sync walkStep index
    const idx = sortedReasoningSteps.value.findIndex(s => s.order === step.order)
    walkStep.value = idx
    highlightDerivationOnGraph(step)
  }
}

// ─── Walk the chain controls ──────────────────────────────────────────────────
const walkNext = () => {
  const steps = sortedReasoningSteps.value
  if (!steps.length) return
  const nextIdx = walkStep.value < 0 ? 0 : Math.min(walkStep.value + 1, steps.length - 1)
  walkStep.value = nextIdx
  const step = steps[nextIdx]
  activeDerivationStep.value = step.order
  highlightDerivationOnGraph(step)
}

const walkPrev = () => {
  const steps = sortedReasoningSteps.value
  if (!steps.length || walkStep.value <= 0) return
  const prevIdx = walkStep.value - 1
  walkStep.value = prevIdx
  const step = steps[prevIdx]
  activeDerivationStep.value = step.order
  highlightDerivationOnGraph(step)
}

const walkReset = () => {
  walkStep.value = -1
  activeDerivationStep.value = null
  highlightDerivationOnGraph(null)
}

// ─── Subgraph helpers ─────────────────────────────────────────────────────────
const clearSubgraph = () => {
  hopHighlight.value = null
  if (subgraphSimulation) {
    subgraphSimulation.stop()
    subgraphSimulation = null
  }
  if (subgraphSvg.value) {
    d3.select(subgraphSvg.value).selectAll('*').remove()
  }
}

const buildSubgraphData = (relationships) => {
  // Deduplicate nodes by id
  const nodeMap = new Map()
  for (const rel of relationships) {
    if (rel.source_id != null && !nodeMap.has(String(rel.source_id))) {
      nodeMap.set(String(rel.source_id), { id: String(rel.source_id), label: rel.source || String(rel.source_id), entity_type: rel.source_type })
    }
    if (rel.target_id != null && !nodeMap.has(String(rel.target_id))) {
      nodeMap.set(String(rel.target_id), { id: String(rel.target_id), label: rel.target || String(rel.target_id), entity_type: rel.target_type })
    }
    // Fallback: if ids missing, use names as ids
    if (rel.source_id == null && rel.source && !nodeMap.has(rel.source)) {
      nodeMap.set(rel.source, { id: rel.source, label: rel.source })
    }
    if (rel.target_id == null && rel.target && !nodeMap.has(rel.target)) {
      nodeMap.set(rel.target, { id: rel.target, label: rel.target })
    }
  }

  const nodes = Array.from(nodeMap.values())
  const nodeIdSet = new Set(nodes.map(n => n.id))

  const edges = relationships
    .map(rel => ({
      source: rel.source_id != null ? String(rel.source_id) : rel.source,
      target: rel.target_id != null ? String(rel.target_id) : rel.target,
      label: rel.edge_type || 'related',
      rawRel: rel
    }))
    .filter(e => nodeIdSet.has(e.source) && nodeIdSet.has(e.target))

  return { nodes, edges }
}

// LayeredGraph props for the community subgraph (level inferred from entity_type)
const sgNodes = computed(() => (narrative.value?.relationships?.length ? buildSubgraphData(narrative.value.relationships).nodes : []))
const sgEdges = computed(() => (narrative.value?.relationships?.length
  ? buildSubgraphData(narrative.value.relationships).edges.map((e) => ({ source: e.source, target: e.target, edge_type: e.label }))
  : []))

const SUBGRAPH_COLORS = [
  '#1a56db', '#7c3aed', '#059669', '#dc2626', '#d97706',
  '#0891b2', '#be185d', '#65a30d', '#0f766e', '#b45309'
]

const renderSubgraph = () => {
  if (!subgraphSvg.value || !subgraphContainer.value || !narrative.value?.relationships?.length) return
  clearSubgraph()

  const { nodes, edges } = buildSubgraphData(narrative.value.relationships)
  if (nodes.length === 0) return

  const containerEl = subgraphContainer.value
  const width = containerEl.clientWidth || 560
  const height = 320

  const svg = d3.select(subgraphSvg.value)
    .attr('width', width)
    .attr('height', height)
    .attr('viewBox', `0 0 ${width} ${height}`)
  svg.selectAll('*').remove()

  // Assign stable colors by node index
  const colorOf = (_, i) => SUBGRAPH_COLORS[i % SUBGRAPH_COLORS.length]

  const simulation = d3.forceSimulation(nodes)
    .force('link', d3.forceLink(edges).id(d => d.id).distance(100))
    .force('charge', d3.forceManyBody().strength(-220))
    .force('center', d3.forceCenter(width / 2, height / 2))
    .force('collide', d3.forceCollide(38))
    .force('x', d3.forceX(width / 2).strength(0.05))
    .force('y', d3.forceY(height / 2).strength(0.05))

  subgraphSimulation = simulation

  const g = svg.append('g')
  svg.call(
    d3.zoom()
      .extent([[0, 0], [width, height]])
      .scaleExtent([0.3, 3])
      .on('zoom', event => g.attr('transform', event.transform))
  )

  // Arrow marker
  svg.append('defs').append('marker')
    .attr('id', 'sg-arrow')
    .attr('viewBox', '0 -4 8 8')
    .attr('refX', 18)
    .attr('refY', 0)
    .attr('markerWidth', 6)
    .attr('markerHeight', 6)
    .attr('orient', 'auto')
    .append('path')
    .attr('d', 'M0,-4L8,0L0,4')
    .attr('fill', '#b0b0b0')

  const linkGroup = g.append('g')

  const link = linkGroup.selectAll('line')
    .data(edges).enter().append('line')
    .attr('stroke', '#d0d0d0')
    .attr('stroke-width', 1.5)
    .attr('marker-end', 'url(#sg-arrow)')

  // Edge labels
  const edgeLabelBg = linkGroup.selectAll('rect.elb')
    .data(edges).enter().append('rect')
    .attr('class', 'elb')
    .attr('fill', 'rgba(255,255,255,0.88)')
    .attr('rx', 2)

  const edgeLabel = linkGroup.selectAll('text.el')
    .data(edges).enter().append('text')
    .attr('class', 'el')
    .text(d => d.label)
    .attr('font-size', '9px')
    .attr('fill', '#888')
    .attr('text-anchor', 'middle')
    .attr('dominant-baseline', 'middle')
    .style('font-family', 'var(--font-mono, monospace)')
    .style('pointer-events', 'none')

  const nodeGroup = g.append('g')

  const node = nodeGroup.selectAll('circle')
    .data(nodes).enter().append('circle')
    .attr('r', 9)
    .attr('fill', colorOf)
    .attr('stroke', '#fff')
    .attr('stroke-width', 2)
    .style('cursor', 'pointer')
    .call(
      d3.drag()
        .on('start', (event, d) => { d.fx = d.x; d.fy = d.y; simulation.alphaTarget(0.3).restart() })
        .on('drag', (event, d) => { d.fx = event.x; d.fy = event.y })
        .on('end', (event, d) => { simulation.alphaTarget(0); d.fx = null; d.fy = null })
    )
    .on('click', (event, d) => {
      event.stopPropagation()
      // If a derivation step is active, clear it first on node click
      if (activeDerivationStep.value !== null) {
        activeDerivationStep.value = null
        walkStep.value = -1
      }
      node.attr('stroke', '#fff').attr('stroke-width', 2)
      d3.select(event.target).attr('stroke', '#1a56db').attr('stroke-width', 3.5)
      fetchNodeProfile(d.id)
    })

  const nodeLabels = nodeGroup.selectAll('text.nl')
    .data(nodes).enter().append('text')
    .attr('class', 'nl')
    .text(d => d.label.length > 14 ? d.label.slice(0, 13) + '…' : d.label)
    .attr('font-size', '10px')
    .attr('fill', '#333')
    .attr('font-weight', '500')
    .attr('dx', 12).attr('dy', 4)
    .style('pointer-events', 'none')
    .style('font-family', 'system-ui, sans-serif')

  simulation.on('tick', () => {
    link
      .attr('x1', d => d.source.x).attr('y1', d => d.source.y)
      .attr('x2', d => d.target.x).attr('y2', d => d.target.y)

    edgeLabel
      .attr('x', d => (d.source.x + d.target.x) / 2)
      .attr('y', d => (d.source.y + d.target.y) / 2)

    edgeLabelBg.each(function(d, i) {
      const textEl = edgeLabel.nodes()[i]
      try {
        const bbox = textEl.getBBox()
        d3.select(this)
          .attr('x', bbox.x - 2).attr('y', bbox.y - 1)
          .attr('width', bbox.width + 4).attr('height', bbox.height + 2)
      } catch {}
    })

    node.attr('cx', d => d.x).attr('cy', d => d.y)
    nodeLabels.attr('x', d => d.x).attr('y', d => d.y)
  })

  svg.on('click', () => {
    // Restore graph default only if no derivation step is holding the highlight
    if (activeDerivationStep.value === null) {
      node.attr('stroke', '#fff').attr('stroke-width', 2)
    }
  })

  // If a derivation step is already active when graph re-renders (e.g. after walk),
  // re-apply the highlight once the simulation has settled a bit
  if (activeDerivationStep.value !== null) {
    const step = sortedReasoningSteps.value.find(s => s.order === activeDerivationStep.value)
    if (step) {
      setTimeout(() => highlightDerivationOnGraph(step), 600)
    }
  }
}

// Watch for narrative changes to re-render subgraph
watch(narrative, async (val) => {
  if (val?.relationships?.length) {
    await nextTick()
    renderSubgraph()
  } else {
    clearSubgraph()
  }
})

// ─── Node profile loading ─────────────────────────────────────────────────────
const fetchNodeProfile = async (entityId) => {
  if (!entityId) return
  lastProfileEntityId = entityId
  nodeProfileOpen.value = true
  nodeProfileLoading.value = true
  nodeProfileError.value = ''
  nodeProfile.value = null
  try {
    const result = await getNodeProfile(props.runId, entityId)
    nodeProfile.value = result
  } catch (err) {
    nodeProfileError.value = err?.response?.data?.detail || err.message || 'Failed to load entity profile'
  } finally {
    nodeProfileLoading.value = false
  }
}

const retryNodeProfile = () => {
  if (lastProfileEntityId) fetchNodeProfile(lastProfileEntityId)
}

const closeNodeProfile = () => {
  nodeProfileOpen.value = false
  nodeProfile.value = null
  nodeProfileError.value = ''
  nodeProfileLoading.value = false
  lastProfileEntityId = null
}

// ─── Date formatting ──────────────────────────────────────────────────────────
const formatDate = (dateStr) => {
  if (!dateStr) return ''
  try {
    const d = new Date(dateStr)
    return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' })
  } catch {
    return dateStr
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

/* Right sidebar: node profile */
.right-sidebar {
  width: 320px;
  flex-shrink: 0;
  background: #FFF;
  border-left: 1px solid #EAEAEA;
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
  flex: 1;
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

.profile-close-btn {
  background: none;
  border: none;
  font-size: 18px;
  line-height: 1;
  color: #999;
  cursor: pointer;
  padding: 0 2px;
  transition: color 0.15s;
}

.profile-close-btn:hover {
  color: #333;
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

/* ── Derivation chain section ── */
.derivation-section {
  border: 1px solid #E5E5E5;
  border-radius: 8px;
  overflow: hidden;
  background: #FAFBFF;
}

.derivation-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 10px 16px;
  background: #F0F4FF;
  border-bottom: 1px solid #DDE4F8;
  gap: 12px;
  flex-wrap: wrap;
}

.derivation-title-row {
  display: flex;
  align-items: baseline;
  gap: 8px;
}

.derivation-title {
  font-size: 12px;
  font-weight: 700;
  font-family: var(--font-mono);
  text-transform: uppercase;
  letter-spacing: 0.5px;
  color: #3730A3;
}

.derivation-title-zh {
  font-family: system-ui, sans-serif;
  font-size: 11px;
  color: #7c7cb5;
  font-weight: 500;
  text-transform: none;
  letter-spacing: 0;
}

.derivation-count {
  font-family: var(--font-mono);
  font-size: 10px;
  color: #999;
  background: #E5E7FA;
  padding: 1px 7px;
  border-radius: 8px;
}

/* Walk the chain controls */
.walk-controls {
  display: flex;
  align-items: center;
  gap: 6px;
}

.walk-btn {
  background: #FFF;
  border: 1px solid #C7D2FE;
  color: #3730A3;
  width: 28px;
  height: 28px;
  border-radius: 4px;
  font-size: 14px;
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  transition: background 0.12s, color 0.12s;
  flex-shrink: 0;
}

.walk-btn:hover:not(:disabled) {
  background: #EEF2FF;
}

.walk-btn:disabled {
  opacity: 0.35;
  cursor: default;
}

.walk-label {
  font-family: var(--font-mono);
  font-size: 11px;
  color: #555;
  min-width: 90px;
  text-align: center;
  white-space: nowrap;
}

.walk-reset-btn {
  background: none;
  border: none;
  color: #aaa;
  font-size: 12px;
  cursor: pointer;
  padding: 2px 4px;
  border-radius: 3px;
  transition: color 0.12s;
  line-height: 1;
}

.walk-reset-btn:hover {
  color: #ef4444;
}

/* Derivation step list */
.derivation-list {
  display: flex;
  flex-direction: column;
  gap: 0;
}

.derivation-row {
  display: flex;
  align-items: flex-start;
  gap: 12px;
  padding: 11px 16px;
  border-bottom: 1px solid #EEF0FB;
  cursor: pointer;
  transition: background 0.12s, opacity 0.2s;
  background: transparent;
}

.derivation-row:last-child {
  border-bottom: none;
}

.derivation-row:hover {
  background: #EEF2FF;
}

.derivation-row--active {
  background: #EEF2FF;
  border-left: 3px solid #f59e0b;
}

.derivation-row--dimmed {
  opacity: 0.38;
}

.derivation-step-num {
  flex-shrink: 0;
  width: 24px;
  height: 24px;
  background: #DDE4F8;
  color: #3730A3;
  border-radius: 50%;
  font-family: var(--font-mono);
  font-size: 11px;
  font-weight: 700;
  display: flex;
  align-items: center;
  justify-content: center;
  margin-top: 1px;
}

.derivation-row--active .derivation-step-num {
  background: #f59e0b;
  color: #fff;
}

.derivation-step-body {
  flex: 1;
  display: flex;
  flex-direction: column;
  gap: 5px;
  min-width: 0;
}

.derivation-step-edge {
  display: flex;
  align-items: center;
  gap: 5px;
  flex-wrap: wrap;
}

.deriv-source,
.deriv-target {
  font-size: 12px;
  font-weight: 600;
  color: #222;
  white-space: nowrap;
}

.deriv-arrow {
  font-family: var(--font-mono);
  font-size: 11px;
  color: #bbb;
  white-space: nowrap;
}

.deriv-edge-type {
  font-family: var(--font-mono);
  font-size: 10px;
  background: #EEF2FF;
  color: #3730A3;
  border: 1px solid #C7D2FE;
  padding: 1px 7px;
  border-radius: 10px;
  white-space: nowrap;
}

.derivation-row--active .deriv-edge-type {
  background: #FEF3C7;
  color: #92400E;
  border-color: #FDE68A;
}

.derivation-step-claim {
  font-size: 12px;
  color: #555;
  line-height: 1.5;
  word-break: break-word;
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

.read-source-link { font-size: 11px; color: #2563eb; cursor: pointer; margin-left: 6px; white-space: nowrap; }
.read-source-link:hover { text-decoration: underline; }
.source-modal { position: fixed; inset: 0; background: rgba(0,0,0,0.45); display: flex; align-items: center; justify-content: center; z-index: 1000; }
.source-card { background: #fff; width: min(760px, 92vw); max-height: 82vh; overflow: auto; border-radius: 8px; padding: 22px 26px; position: relative; box-shadow: 0 10px 40px rgba(0,0,0,0.25); }
.source-close { position: absolute; top: 10px; right: 14px; border: none; background: none; font-size: 1.4rem; cursor: pointer; color: #999; }
.source-loading { color: #888; padding: 24px 0; }
.source-title { font-weight: 700; font-size: 1.05rem; padding-right: 24px; }
.source-meta { font-family: monospace; font-size: 0.74rem; color: #888; margin: 6px 0 14px; }
.source-orig { color: #2563eb; margin-left: 8px; }
.source-text { font-size: 0.9rem; line-height: 1.65; color: #1a1a1a; white-space: pre-wrap; }
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

/* ── Subgraph section ── */
.subgraph-section {
  border: 1px solid #E5E5E5;
  border-radius: 8px;
  overflow: hidden;
  background: #FAFAFA;
  background-image: radial-gradient(#D8D8D8 1px, transparent 1px);
  background-size: 20px 20px;
}

.subgraph-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 10px 16px;
  background: #F8F9FA;
  border-bottom: 1px solid #EEE;
  background-image: none;
}

.subgraph-title {
  font-size: 11px;
  font-weight: 700;
  font-family: var(--font-mono);
  text-transform: uppercase;
  color: #888;
  letter-spacing: 0.5px;
}

.subgraph-hint {
  font-size: 11px;
  color: #aaa;
  font-family: var(--font-mono);
}

.subgraph-container {
  width: 100%;
  height: 320px;
  position: relative;
  overflow: hidden;
}

.subgraph-svg {
  width: 100%;
  height: 100%;
  display: block;
}

/* ── Node Profile sidebar ── */
.profile-content {
  flex: 1;
  overflow-y: auto;
  padding: 16px;
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.profile-loading {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 24px 16px;
}

.profile-spinner {
  width: 22px;
  height: 22px;
  border: 2px solid #eee;
  border-top-color: var(--accent, #1a56db);
  border-radius: 50%;
  flex-shrink: 0;
  animation: spin 0.8s linear infinite;
}

.profile-loading-text {
  font-size: 12px;
  color: #999;
  font-family: var(--font-mono);
}

.profile-error {
  display: flex;
  align-items: flex-start;
  gap: 10px;
  padding: 16px;
  background: #FFF5F5;
}

.profile-error-icon {
  font-size: 1rem;
  color: #ef4444;
  flex-shrink: 0;
}

.profile-error-title {
  font-size: 12px;
  font-weight: 600;
  color: #c0392b;
  margin-bottom: 4px;
}

.profile-error-msg {
  font-size: 11px;
  color: #e74c3c;
  font-family: var(--font-mono);
  line-height: 1.5;
  margin-bottom: 8px;
  word-break: break-word;
}

.profile-retry-btn {
  background: transparent;
  border: 1px solid #ef4444;
  color: #ef4444;
  padding: 4px 10px;
  font-size: 11px;
  font-family: var(--font-mono);
  cursor: pointer;
  border-radius: 3px;
  transition: all 0.15s;
}

.profile-retry-btn:hover {
  background: #ef4444;
  color: #fff;
}

.profile-identity {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.profile-type-row {
  display: flex;
  align-items: center;
  gap: 8px;
}

.profile-type-badge {
  background: #EEF2FF;
  color: #3730A3;
  border: 1px solid #C7D2FE;
  font-family: var(--font-mono);
  font-size: 10px;
  font-weight: 700;
  padding: 2px 8px;
  border-radius: 2px;
  text-transform: uppercase;
}

.profile-level {
  font-family: var(--font-mono);
  font-size: 10px;
  color: #999;
  background: #F5F5F5;
  border: 1px solid #E5E5E5;
  padding: 2px 7px;
  border-radius: 2px;
}

.profile-name {
  font-size: 1.1rem;
  font-weight: 700;
  color: var(--black, #111);
  margin: 0;
  line-height: 1.3;
}

.profile-definition {
  font-size: 12px;
  color: #555;
  line-height: 1.6;
  margin: 0;
  background: #FAFBFF;
  border-left: 2px solid var(--accent, #1a56db);
  padding: 8px 10px;
  border-radius: 0 4px 4px 0;
}

.profile-stats {
  display: flex;
  gap: 12px;
  flex-wrap: wrap;
  padding: 10px 12px;
  background: #F8F9FA;
  border: 1px solid #EEE;
  border-radius: 6px;
}

.profile-stat {
  display: flex;
  flex-direction: column;
  gap: 2px;
  align-items: flex-start;
}

.stat-num {
  font-family: var(--font-mono);
  font-weight: 700;
  font-size: 1rem;
  color: var(--black, #111);
}

.stat-date {
  font-size: 0.72rem;
}

.stat-label {
  font-family: var(--font-mono);
  font-size: 0.65rem;
  color: #aaa;
  text-transform: uppercase;
}

.profile-section {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.profile-section-title {
  font-size: 11px;
  font-weight: 700;
  font-family: var(--font-mono);
  text-transform: uppercase;
  color: #888;
  letter-spacing: 0.4px;
}

.why-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.why-row {
  background: #F8F9FA;
  border: 1px solid #E8E8E8;
  border-radius: 5px;
  padding: 8px 10px;
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.why-edge {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
}

.why-direction {
  font-family: var(--font-mono);
  font-size: 9px;
  font-weight: 700;
  text-transform: uppercase;
  padding: 1px 6px;
  border-radius: 2px;
  letter-spacing: 0.3px;
}

.dir-out { background: #ECFDF5; color: #065F46; }
.dir-in  { background: #EEF2FF; color: #3730A3; }

/* Reasoning-step provenance badge: evidence-backed vs model inference. */
.deriv-prov { margin-left: 6px; padding: 1px 6px; border-radius: 3px; font-size: 0.65rem;
  font-family: var(--font-mono); font-weight: 700; text-transform: uppercase; letter-spacing: 0.3px; }
.prov-document_stated { background: #ECFDF5; color: #065F46; border: 1px solid #A7F3D0; }
.prov-llm_inferred    { background: #FFF7ED; color: #C2410C; border: 1px solid #FED7AA; }

.why-edge-type {
  font-family: var(--font-mono);
  font-size: 10px;
  background: #EEF2FF;
  color: #3730A3;
  border: 1px solid #C7D2FE;
  padding: 1px 7px;
  border-radius: 10px;
  white-space: nowrap;
}

.why-other {
  font-size: 12px;
  font-weight: 600;
  color: #222;
}

.why-explanation {
  font-size: 11px;
  color: #666;
  line-height: 1.5;
}

.related-tags {
  display: flex;
  flex-wrap: wrap;
  gap: 5px;
}

.related-tag {
  background: #F5F5F5;
  color: #555;
  border: 1px solid #E5E5E5;
  padding: 3px 9px;
  font-size: 11px;
  border-radius: 2px;
  white-space: nowrap;
  max-width: 200px;
  overflow: hidden;
  text-overflow: ellipsis;
}
</style>
