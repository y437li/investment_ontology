<template>
  <div class="page-view">
    <RunNav :runId="runId" />

    <div class="content-area">

      <!-- ── Hypothetical banner (always visible, sticky) ─────────────── -->
      <div class="hypothetical-banner">
        <span class="hyp-icon">&#9651;</span>
        <div class="hyp-text">
          <strong>Projected scenarios — HYPOTHETICAL</strong>
          These projections are system-generated forward inferences, not
          stated facts from source documents. They represent model-derived
          causal chains and must not be treated as investment advice.
        </div>
      </div>

      <!-- ── Page header ──────────────────────────────────────────────── -->
      <div class="page-header">
        <h1 class="page-title">Projected Scenarios</h1>
        <div class="page-subtitle">
          Data-driven Event triggers &rarr; ranked company impacts via the causal graph.
          Select a trigger below to see which companies are reached and how.
        </div>
      </div>

      <!-- ── Trigger list loading / error ─────────────────────────────── -->
      <div v-if="triggersLoading" class="center-msg">
        <div class="spinner"></div>
        <span>Loading triggers…</span>
      </div>

      <div v-else-if="triggersError" class="center-msg error-state">
        <div class="error-icon">&#9888;</div>
        <div class="error-title">Could not load triggers</div>
        <div class="error-msg">{{ triggersError }}</div>
        <button class="retry-btn" @click="loadTriggers">Retry</button>
      </div>

      <!-- ── Main layout (trigger list + impact panel) ─────────────────── -->
      <div v-else class="scenario-layout">

        <!-- ── Left: trigger list ──────────────────────────────────────── -->
        <div class="trigger-panel card">
          <div class="card-header">
            <span class="card-title">Event Triggers</span>
            <span class="count-badge" v-if="triggers.length">{{ triggers.length }}</span>
          </div>

          <div v-if="!triggers.length" class="empty-state">
            <div class="empty-icon">&#9432;</div>
            <div class="empty-msg-text">
              No Event triggers found in projected_impacts.parquet.
              Run the FI-C projection step first.
            </div>
          </div>

          <div v-else class="trigger-list">
            <div
              v-for="trig in triggers"
              :key="trig.trigger_id"
              class="trigger-row"
              :class="{ active: selectedTriggerId === trig.trigger_id }"
              @click="selectTrigger(trig.trigger_id)"
            >
              <div class="trigger-row-label" :title="trig.trigger_id">
                {{ trig.label }}
              </div>
              <div class="trigger-row-meta">
                <span class="trigger-kind-badge">{{ trig.trigger_kind }}</span>
                <span class="trigger-company-count" :title="`reaches ${trig.company_count} companies`">
                  {{ trig.company_count }} co.
                </span>
              </div>
            </div>
          </div>
        </div>

        <!-- ── Right: impact panel ─────────────────────────────────────── -->
        <div class="impact-panel">

          <!-- No trigger selected yet -->
          <div v-if="!selectedTriggerId" class="center-msg muted">
            <div class="select-prompt-icon">&#10229;</div>
            <div>Select an Event trigger to view projected company impacts.</div>
          </div>

          <!-- Loading impacts -->
          <div v-else-if="impactsLoading" class="center-msg">
            <div class="spinner"></div>
            <span>Loading projected impacts…</span>
          </div>

          <!-- Error loading impacts -->
          <div v-else-if="impactsError" class="center-msg error-state">
            <div class="error-icon">&#9888;</div>
            <div class="error-title">Could not load projections</div>
            <div class="error-msg">{{ impactsError }}</div>
            <button class="retry-btn" @click="loadImpacts(selectedTriggerId)">Retry</button>
          </div>

          <!-- Impact results -->
          <template v-else-if="impactData">

            <!-- Trigger header -->
            <div class="trigger-result-header card">
              <div class="card-header">
                <span class="card-title">{{ impactData.trigger_label }}</span>
                <span class="trigger-kind-badge">{{ impactData.trigger_kind }}</span>
                <span class="count-badge" v-if="impactData.impact_count">
                  {{ impactData.impact_count }} companies
                </span>
                <span class="as-of-label mono">as of {{ impactData.as_of_date }}</span>
              </div>

              <!-- Hypothetical sub-label inside trigger header -->
              <div class="trigger-result-hyp">
                <span class="hyp-pill">HYPOTHETICAL PROJECTION</span>
                These are model-inferred causal impacts, not stated document facts.
              </div>
            </div>

            <!-- Sign-blind caveat (issue #110) — only shown if any impact is sign-blind -->
            <div v-if="anySignBlind" class="sign-blind-notice">
              <span class="sign-blind-icon">&#9432;</span>
              <div>
                <strong>Direction caveat (#110):</strong>
                One or more impacts below are derived solely from
                <code>causes</code>, <code>exposed_to</code>, or <code>sensitive_to</code>
                edges, whose direction is provisional (always +1 in v1).
                Impacts marked with <span class="prov-inline">prov.</span> have
                uncertain sign; positive and negative should both be considered possible.
              </div>
            </div>

            <!-- Explicit empty state — never silently blank -->
            <div v-if="!impactData.impacts.length" class="empty-state card-like">
              <div class="empty-icon">&#9432;</div>
              <div class="empty-msg-text">
                {{ impactData.empty_reason || 'No company impacts found for this trigger.' }}
              </div>
            </div>

            <!-- Impact cards -->
            <div v-else class="impact-list">
              <div
                v-for="(impact, idx) in impactData.impacts"
                :key="impact.company_id"
                class="impact-card"
                :class="impact.direction > 0 ? 'impact-positive' : 'impact-negative'"
              >
                <!-- ── Impact card header ─────────────────────────────── -->
                <div class="impact-card-header">
                  <!-- Rank -->
                  <span class="impact-rank">#{{ idx + 1 }}</span>

                  <!-- Direction badge -->
                  <span
                    class="direction-badge"
                    :class="impact.direction > 0 ? 'dir-positive' : 'dir-negative'"
                    :title="impact.sign_blind ? 'Direction provisional — see sign-blind caveat' : ''"
                  >
                    {{ impact.direction > 0 ? '+' : '−' }}
                    <span v-if="impact.sign_blind" class="prov-tag">prov.</span>
                  </span>

                  <!-- Company name -->
                  <div class="impact-company">
                    <router-link
                      class="company-link"
                      :to="{ name: 'Company', params: { runId, companyId: impact.company_id } }"
                    >
                      {{ impact.company_name }}
                    </router-link>
                    <span class="company-id-mono">{{ impact.company_id }}</span>
                  </div>

                  <!-- Strength bar -->
                  <div class="strength-wrap">
                    <div class="strength-label">strength</div>
                    <div class="strength-bar-bg">
                      <div
                        class="strength-bar-fill"
                        :class="impact.direction > 0 ? 'fill-positive' : 'fill-negative'"
                        :style="{ width: strengthPct(impact.strength) }"
                      ></div>
                    </div>
                    <span class="strength-val mono">{{ impact.strength.toFixed(3) }}</span>
                  </div>

                  <!-- Confidence -->
                  <div class="conf-wrap">
                    <span class="conf-label">conf.</span>
                    <span class="conf-val mono">{{ (impact.confidence * 100).toFixed(0) }}%</span>
                  </div>

                  <!-- Hypothetical pill on every card -->
                  <span class="hyp-pill-small">hypothetical</span>

                  <!-- Expand toggle -->
                  <button
                    class="expand-btn"
                    @click="toggleImpact(impact.company_id)"
                    :title="expandedImpacts.has(impact.company_id) ? 'Collapse' : 'Expand'"
                  >
                    {{ expandedImpacts.has(impact.company_id) ? '&#9650;' : '&#9660;' }}
                  </button>
                </div>

                <!-- ── Impact card detail (expanded) ─────────────────── -->
                <div v-if="expandedImpacts.has(impact.company_id)" class="impact-card-detail">

                  <!-- Edge path graph -->
                  <div class="path-section">
                    <div class="section-label">
                      Causal path
                      <span class="path-hop-count">({{ impact.path.length }} edge{{ impact.path.length !== 1 ? 's' : '' }})</span>
                    </div>
                    <div
                      v-if="impact.path_graph.nodes.length"
                      class="path-graph-wrap"
                    >
                      <LayeredGraph
                        :nodes="impact.path_graph.nodes"
                        :edges="impact.path_graph.edges"
                        :activeHop="null"
                      />
                    </div>
                    <div v-else class="path-empty">
                      Path graph unavailable (edge index may not contain these IDs).
                    </div>

                    <!-- Raw path edge IDs for transparency -->
                    <div class="path-edge-ids">
                      <span class="path-ids-label">edges:</span>
                      <span
                        v-for="eid in impact.path"
                        :key="eid"
                        class="path-eid-chip"
                      >{{ eid }}</span>
                    </div>
                  </div>

                  <!-- Evidence chunks -->
                  <div class="evidence-section">
                    <div class="section-label">
                      Evidence
                      <span class="evidence-count">({{ impact.evidence_chunk_ids.length }} chunk{{ impact.evidence_chunk_ids.length !== 1 ? 's' : '' }})</span>
                    </div>

                    <div v-if="!impact.evidence_chunk_ids.length" class="evidence-empty">
                      No evidence chunks associated with this path.
                    </div>

                    <div v-else class="evidence-list">
                      <div
                        v-for="chunkId in impact.evidence_chunk_ids"
                        :key="chunkId"
                        class="evidence-chunk-row"
                      >
                        <span class="chunk-id-mono">{{ chunkId }}</span>
                        <a
                          class="source-link"
                          @click="openSource(chunkId)"
                        >read full source &rarr;</a>
                      </div>
                    </div>
                  </div>

                </div><!-- /impact-card-detail -->
              </div><!-- /impact-card -->
            </div><!-- /impact-list -->

            <!-- Footer caveat -->
            <div class="scenario-footer">
              <span class="hyp-pill">HYPOTHETICAL</span>
              Projections generated by propagating event shocks through the
              PIT-filtered causal graph as of <span class="mono">{{ impactData.as_of_date }}</span>.
              Direction and strength are ordinal signals, not calibrated probabilities.
              <span v-if="anySignBlind">
                Provisional directions (<span class="prov-inline">prov.</span>) use
                +1 for all causal edges pending issue #110 resolution.
              </span>
            </div>

          </template><!-- /impact results -->

        </div><!-- /impact-panel -->

      </div><!-- /scenario-layout -->
    </div><!-- /content-area -->

    <!-- ── Full-text source modal ─────────────────────────────────────── -->
    <div v-if="sourceDoc || sourceLoading" class="source-modal" @click.self="closeSource">
      <div class="source-card">
        <button class="source-close" @click="closeSource">&times;</button>
        <div v-if="sourceLoading" class="source-loading">Loading source…</div>
        <template v-else-if="sourceDoc">
          <div class="source-title">{{ sourceDoc.document?.title || 'Source document' }}</div>
          <div class="source-meta">
            <span v-if="sourceDoc.document?.source">{{ sourceDoc.document.source }}</span>
            <span v-if="sourceDoc.document?.published_at"> &middot; {{ sourceDoc.document.published_at }}</span>
            <span v-if="sourceDoc.document?.document_type"> &middot; {{ sourceDoc.document.document_type }}</span>
            <a
              v-if="sourceDoc.document?.source_url"
              :href="sourceDoc.document.source_url"
              target="_blank"
              rel="noopener"
              class="source-orig"
            >open original &#8599;</a>
          </div>
          <div class="source-text">{{ sourceDoc.document_text }}</div>
        </template>
      </div>
    </div>

  </div>
</template>

<script setup>
import { ref, computed, onMounted, watch } from 'vue'
import RunNav from '../components/RunNav.vue'
import LayeredGraph from '../components/LayeredGraph.vue'
import { getProjectionTriggers, getProjections, getChunkSource } from '../api/themes.js'

const props = defineProps({
  runId: { type: String, required: true },
})

// ── Trigger list state ─────────────────────────────────────────────────────
const triggersLoading = ref(false)
const triggersError = ref('')
const triggers = ref([])
const asOfDate = ref('')

// ── Selected trigger + impact state ───────────────────────────────────────
const selectedTriggerId = ref(null)
const impactsLoading = ref(false)
const impactsError = ref('')
const impactData = ref(null)
const expandedImpacts = ref(new Set())

// ── Source modal state ─────────────────────────────────────────────────────
const sourceDoc = ref(null)
const sourceLoading = ref(false)

// ── Computed ───────────────────────────────────────────────────────────────

/** True when at least one impact has sign_blind = true. */
const anySignBlind = computed(() =>
  impactData.value?.impacts?.some(i => i.sign_blind) ?? false
)

/** Maximum absolute strength in the current impact list (for bar scaling). */
const maxStrength = computed(() => {
  const impacts = impactData.value?.impacts ?? []
  if (!impacts.length) return 1
  return Math.max(...impacts.map(i => Math.abs(i.strength)), 0.001)
})

// ── Helpers ────────────────────────────────────────────────────────────────

/** Render strength as a % of max for the bar (visual only — NOT a probability). */
const strengthPct = (val) => {
  const pct = (Math.abs(val) / maxStrength.value) * 100
  return Math.min(pct, 100).toFixed(1) + '%'
}

// ── Trigger selection ──────────────────────────────────────────────────────
const selectTrigger = async (triggerId) => {
  if (selectedTriggerId.value === triggerId) return
  selectedTriggerId.value = triggerId
  impactData.value = null
  expandedImpacts.value = new Set()
  await loadImpacts(triggerId)
}

// ── Impact expand toggle ───────────────────────────────────────────────────
const toggleImpact = (companyId) => {
  const s = new Set(expandedImpacts.value)
  if (s.has(companyId)) {
    s.delete(companyId)
  } else {
    s.add(companyId)
  }
  expandedImpacts.value = s
}

// ── Source modal ───────────────────────────────────────────────────────────
const openSource = async (chunkId) => {
  if (!chunkId) return
  sourceLoading.value = true
  sourceDoc.value = null
  try {
    sourceDoc.value = await getChunkSource(props.runId, chunkId)
  } catch (e) {
    sourceDoc.value = {
      document: { title: 'Source unavailable' },
      document_text: e?.response?.data?.detail || 'Failed to load source.',
    }
  } finally {
    sourceLoading.value = false
  }
}

const closeSource = () => {
  sourceDoc.value = null
  sourceLoading.value = false
}

// ── Data loading ───────────────────────────────────────────────────────────
const loadTriggers = async () => {
  triggersLoading.value = true
  triggersError.value = ''
  triggers.value = []
  try {
    const resp = await getProjectionTriggers(props.runId)
    triggers.value = resp.triggers ?? []
    asOfDate.value = resp.as_of_date ?? ''
  } catch (err) {
    const status = err?.response?.status
    if (status === 404) {
      triggersError.value =
        'projected_impacts.parquet not found. Run POST /api/fi/compute-projections first.'
    } else {
      triggersError.value =
        err?.response?.data?.detail || err.message || 'Failed to load triggers'
    }
  } finally {
    triggersLoading.value = false
  }
}

const loadImpacts = async (triggerId) => {
  impactsLoading.value = true
  impactsError.value = ''
  impactData.value = null
  try {
    impactData.value = await getProjections(props.runId, triggerId)
  } catch (err) {
    impactsError.value =
      err?.response?.data?.detail || err.message || 'Failed to load projections'
  } finally {
    impactsLoading.value = false
  }
}

onMounted(loadTriggers)

// Reload if runId changes
watch(() => props.runId, () => {
  selectedTriggerId.value = null
  impactData.value = null
  expandedImpacts.value = new Set()
  loadTriggers()
})
</script>

<style scoped>
/* ── Layout ── */
.page-view {
  display: flex;
  flex-direction: column;
  min-height: 100vh;
  background: #F8F9FA;
}

.content-area {
  flex: 1;
  padding: 0 24px 32px;
  max-width: 1500px;
  margin: 0 auto;
  width: 100%;
  box-sizing: border-box;
}

/* ── Hypothetical banner (always present, sticky to top of content) ── */
.hypothetical-banner {
  display: flex;
  align-items: flex-start;
  gap: 12px;
  background: #FFF3CD;
  border: 1.5px solid #FBBF24;
  border-radius: 8px;
  padding: 12px 18px;
  margin: 18px 0 12px;
  font-size: 13px;
  color: #92400E;
  line-height: 1.55;
}

.hyp-icon {
  font-size: 1.2rem;
  flex-shrink: 0;
  margin-top: 1px;
}

/* ── Page header ── */
.page-header {
  margin-bottom: 18px;
}

.page-title {
  font-size: 1.4rem;
  font-weight: 700;
  color: #111;
  margin: 0 0 6px 0;
}

.page-subtitle {
  font-size: 13px;
  color: #666;
  line-height: 1.5;
}

/* ── Main two-panel layout ── */
.scenario-layout {
  display: grid;
  grid-template-columns: 280px 1fr;
  gap: 20px;
  align-items: start;
}

@media (max-width: 900px) {
  .scenario-layout { grid-template-columns: 1fr; }
}

/* ── Card ── */
.card {
  background: #FFF;
  border: 1px solid #E5E5E5;
  border-radius: 8px;
  overflow: hidden;
  margin-bottom: 16px;
}

.card-header {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 10px 14px;
  background: #F8F9FA;
  border-bottom: 1px solid #EAEAEA;
}

.card-title {
  font-size: 12px;
  font-weight: 700;
  font-family: var(--font-mono);
  text-transform: uppercase;
  letter-spacing: 0.5px;
  color: #555;
  flex: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.count-badge {
  background: var(--accent, #1a56db);
  color: #FFF;
  border-radius: 10px;
  padding: 1px 7px;
  font-size: 10px;
  font-family: var(--font-mono);
  font-weight: 700;
  flex-shrink: 0;
}

.as-of-label {
  font-size: 10px;
  color: #aaa;
  flex-shrink: 0;
}

/* ── Trigger list ── */
.trigger-panel { position: sticky; top: 12px; }

.trigger-list { display: flex; flex-direction: column; }

.trigger-row {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 9px 14px;
  border-bottom: 1px solid #F5F5F5;
  cursor: pointer;
  transition: background 0.1s;
}

.trigger-row:last-child { border-bottom: none; }
.trigger-row:hover { background: #F0F4FF; }

.trigger-row.active {
  background: #EEF2FF;
  border-left: 3px solid var(--accent, #1a56db);
}

.trigger-row-label {
  font-size: 13px;
  font-weight: 500;
  color: #222;
  flex: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.trigger-row-meta {
  display: flex;
  align-items: center;
  gap: 5px;
  flex-shrink: 0;
}

.trigger-kind-badge {
  font-size: 9px;
  font-family: var(--font-mono);
  background: #E8EDF8;
  color: #555;
  border-radius: 3px;
  padding: 1px 5px;
}

.trigger-company-count {
  font-size: 10px;
  color: #888;
  font-family: var(--font-mono);
}

/* ── Center messages ── */
.center-msg {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 12px;
  padding: 40px 20px;
  color: #888;
  font-size: 14px;
  text-align: center;
}

.center-msg.muted { color: #bbb; }

.select-prompt-icon {
  font-size: 2rem;
  color: #ccc;
}

.spinner {
  width: 26px;
  height: 26px;
  border: 3px solid #eee;
  border-top-color: var(--accent, #1a56db);
  border-radius: 50%;
  animation: spin 0.8s linear infinite;
}

@keyframes spin { to { transform: rotate(360deg); } }

.error-state { color: #c0392b; }
.error-icon { font-size: 1.5rem; }
.error-title { font-weight: 700; }
.error-msg { font-size: 12px; font-family: var(--font-mono); }

.retry-btn {
  background: var(--accent, #1a56db);
  color: #FFF;
  border: none;
  padding: 7px 16px;
  border-radius: 4px;
  cursor: pointer;
  font-size: 13px;
}

/* ── Trigger result header ── */
.trigger-result-header { margin-bottom: 12px; }

.trigger-result-hyp {
  padding: 8px 14px;
  font-size: 12px;
  color: #92400E;
  background: #FFFBEB;
  border-top: 1px solid #FDE68A;
  display: flex;
  align-items: center;
  gap: 8px;
}

/* ── Hypothetical pills ── */
.hyp-pill {
  font-size: 9px;
  font-family: var(--font-mono);
  font-weight: 700;
  letter-spacing: 0.5px;
  text-transform: uppercase;
  background: #FEF3C7;
  color: #92400E;
  border: 1px solid #FDE68A;
  border-radius: 3px;
  padding: 2px 7px;
  flex-shrink: 0;
}

.hyp-pill-small {
  font-size: 8px;
  font-family: var(--font-mono);
  font-weight: 700;
  text-transform: uppercase;
  background: #FEF3C7;
  color: #92400E;
  border: 1px solid #FDE68A;
  border-radius: 3px;
  padding: 1px 5px;
  flex-shrink: 0;
}

/* ── Sign-blind caveat notice ── */
.sign-blind-notice {
  display: flex;
  align-items: flex-start;
  gap: 10px;
  background: #F0F4FF;
  border: 1px solid #C7D2FE;
  border-radius: 6px;
  padding: 10px 14px;
  font-size: 12px;
  color: #3730A3;
  margin-bottom: 14px;
  line-height: 1.55;
}

.sign-blind-icon { font-size: 1rem; flex-shrink: 0; }

.prov-inline {
  font-family: var(--font-mono);
  font-size: 10px;
  font-weight: 700;
  background: #EDE9FE;
  color: #5B21B6;
  border-radius: 3px;
  padding: 1px 5px;
}

/* ── Empty state ── */
.empty-state {
  padding: 20px 16px;
  display: flex;
  align-items: flex-start;
  gap: 10px;
  color: #888;
  font-size: 13px;
}

.card-like {
  background: #FFF;
  border: 1px solid #E5E5E5;
  border-radius: 8px;
}

.empty-icon { font-size: 1rem; flex-shrink: 0; }
.empty-msg-text { line-height: 1.5; }

/* ── Impact list ── */
.impact-list { display: flex; flex-direction: column; gap: 10px; }

.impact-card {
  background: #FFF;
  border: 1px solid #E5E5E5;
  border-radius: 8px;
  overflow: hidden;
}

/* Left-border colour by direction */
.impact-positive { border-left: 4px solid #10B981; }
.impact-negative { border-left: 4px solid #EF4444; }

.impact-card-header {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 10px 14px;
  flex-wrap: wrap;
}

/* Rank number */
.impact-rank {
  font-family: var(--font-mono);
  font-size: 10px;
  color: #bbb;
  flex-shrink: 0;
  width: 22px;
}

/* Direction badge */
.direction-badge {
  font-size: 16px;
  font-weight: 900;
  flex-shrink: 0;
  display: flex;
  align-items: center;
  gap: 3px;
  border-radius: 5px;
  padding: 2px 8px;
  font-family: var(--font-mono);
}

.dir-positive { background: #DCFCE7; color: #166534; }
.dir-negative { background: #FEE2E2; color: #991B1B; }

.prov-tag {
  font-size: 8px;
  font-family: var(--font-mono);
  font-weight: 700;
  background: #EDE9FE;
  color: #5B21B6;
  border-radius: 2px;
  padding: 0 3px;
  vertical-align: middle;
}

/* Company info */
.impact-company {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
}

.company-link {
  font-size: 14px;
  font-weight: 600;
  color: var(--accent, #1a56db);
  text-decoration: none;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.company-link:hover { text-decoration: underline; }

.company-id-mono {
  font-family: var(--font-mono);
  font-size: 10px;
  color: #ccc;
}

/* Strength bar */
.strength-wrap {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-shrink: 0;
}

.strength-label {
  font-size: 9px;
  color: #aaa;
  font-family: var(--font-mono);
  text-transform: uppercase;
}

.strength-bar-bg {
  width: 60px;
  height: 6px;
  background: #F0F0F0;
  border-radius: 3px;
  overflow: hidden;
}

.strength-bar-fill {
  height: 100%;
  border-radius: 3px;
  transition: width 0.3s ease;
}

.fill-positive { background: #10B981; }
.fill-negative { background: #EF4444; }

.strength-val {
  font-size: 11px;
  color: #444;
  min-width: 44px;
  text-align: right;
}

/* Confidence */
.conf-wrap {
  display: flex;
  align-items: center;
  gap: 4px;
  flex-shrink: 0;
}

.conf-label {
  font-size: 9px;
  color: #aaa;
  font-family: var(--font-mono);
  text-transform: uppercase;
}

.conf-val {
  font-size: 11px;
  color: #555;
}

/* Expand button */
.expand-btn {
  background: none;
  border: 1px solid #E2E2E2;
  border-radius: 4px;
  padding: 3px 8px;
  font-size: 10px;
  color: #888;
  cursor: pointer;
  flex-shrink: 0;
  transition: background 0.1s;
}

.expand-btn:hover { background: #F0F4FF; }

/* ── Impact card detail (expanded) ── */
.impact-card-detail {
  padding: 14px 16px;
  background: #FAFBFF;
  border-top: 1px solid #EAEAEA;
  display: flex;
  flex-direction: column;
  gap: 16px;
}

/* Section labels */
.section-label {
  font-size: 10px;
  font-family: var(--font-mono);
  text-transform: uppercase;
  letter-spacing: 0.4px;
  color: #999;
  margin-bottom: 8px;
  display: flex;
  align-items: center;
  gap: 6px;
}

.path-hop-count,
.evidence-count { color: #bbb; }

/* Path graph container */
.path-graph-wrap {
  height: 240px;
  background: #FFF;
  border: 1px solid #E8EDF8;
  border-radius: 6px;
  overflow: hidden;
  margin-bottom: 8px;
}

.path-empty {
  font-size: 12px;
  color: #aaa;
  padding: 8px 0;
}

/* Path edge IDs strip */
.path-edge-ids {
  display: flex;
  align-items: center;
  gap: 5px;
  flex-wrap: wrap;
  font-size: 10px;
}

.path-ids-label {
  font-family: var(--font-mono);
  color: #ccc;
  text-transform: uppercase;
  font-size: 9px;
}

.path-eid-chip {
  background: #F0F0F0;
  color: #666;
  font-family: var(--font-mono);
  font-size: 10px;
  border-radius: 3px;
  padding: 1px 6px;
}

/* Evidence list */
.evidence-empty {
  font-size: 12px;
  color: #aaa;
  padding: 4px 0;
}

.evidence-list { display: flex; flex-direction: column; gap: 6px; }

.evidence-chunk-row {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 6px 10px;
  background: #FFF;
  border: 1px solid #E8EDF8;
  border-radius: 5px;
}

.chunk-id-mono {
  font-family: var(--font-mono);
  font-size: 10px;
  color: #888;
  flex: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.source-link {
  font-size: 11px;
  color: var(--accent, #1a56db);
  cursor: pointer;
  text-decoration: none;
  flex-shrink: 0;
  white-space: nowrap;
}

.source-link:hover { text-decoration: underline; }

/* ── Scenario footer ── */
.scenario-footer {
  margin-top: 16px;
  padding: 10px 14px;
  font-size: 11px;
  color: #888;
  background: #FFF;
  border: 1px solid #E5E5E5;
  border-radius: 6px;
  line-height: 1.6;
  display: flex;
  align-items: flex-start;
  gap: 8px;
  flex-wrap: wrap;
}

.mono { font-family: var(--font-mono); }

/* ── Source modal ── */
.source-modal {
  position: fixed;
  inset: 0;
  background: rgba(0,0,0,0.45);
  z-index: 1000;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 24px;
}

.source-card {
  background: #FFF;
  border-radius: 10px;
  max-width: 720px;
  width: 100%;
  max-height: 80vh;
  overflow-y: auto;
  padding: 24px;
  position: relative;
  box-shadow: 0 20px 60px rgba(0,0,0,0.3);
}

.source-close {
  position: absolute;
  top: 12px;
  right: 16px;
  background: none;
  border: none;
  font-size: 20px;
  cursor: pointer;
  color: #999;
  line-height: 1;
}

.source-close:hover { color: #333; }

.source-loading { color: #888; font-size: 13px; padding: 20px 0; text-align: center; }

.source-title {
  font-size: 15px;
  font-weight: 700;
  color: #222;
  margin-bottom: 8px;
  padding-right: 24px;
}

.source-meta {
  font-size: 11px;
  color: #888;
  margin-bottom: 16px;
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
}

.source-orig {
  color: var(--accent, #1a56db);
  text-decoration: none;
}

.source-orig:hover { text-decoration: underline; }

.source-text {
  font-size: 13px;
  color: #333;
  line-height: 1.75;
  white-space: pre-wrap;
  word-break: break-word;
}
</style>
