<template>
  <div class="page-view">
    <RunNav :runId="runId" />

    <div class="content-area">
      <!-- Loading state -->
      <div v-if="loading" class="center-msg">
        <div class="spinner"></div>
        <span>Loading company profile…</span>
      </div>

      <!-- Error state -->
      <div v-else-if="error" class="center-msg error-state">
        <div class="error-icon">⚠</div>
        <div class="error-title">Could not load company</div>
        <div class="error-msg">{{ error }}</div>
        <button class="retry-btn" @click="loadData">Retry</button>
      </div>

      <!-- Main content -->
      <div v-else-if="profile" class="company-page">

        <!-- ── Company header ─────────────────────────────────────── -->
        <div class="company-header">
          <div class="company-header-inner">
            <div class="company-avatar">{{ initials }}</div>
            <div class="company-identity">
              <h1 class="company-name">{{ profile.name }}</h1>
              <div class="company-meta">
                <span v-if="profile.ticker" class="ticker-badge">{{ profile.ticker }}</span>
                <span class="entity-type">{{ profile.entity_type }}</span>
                <span class="as-of">as of {{ profile.as_of_date }}</span>
              </div>
              <div class="company-id-mono">{{ profile.company_id }}</div>
            </div>
          </div>
        </div>

        <div class="two-column-layout">
          <!-- ── Left column ──────────────────────────────────────── -->
          <div class="left-col">

            <!-- ── Theme exposure list ───────────────────────────── -->
            <section class="card">
              <div class="card-header">
                <span class="card-title">Theme Exposures</span>
                <span class="count-badge" v-if="profile.themes?.length">{{ profile.themes.length }}</span>
              </div>
              <div v-if="!profile.themes?.length" class="empty-state">
                No theme exposures found for this company.
              </div>
              <div v-else class="theme-list">
                <div
                  v-for="theme in profile.themes"
                  :key="theme.community_id"
                  class="theme-row"
                  :class="{ active: activeTheme === theme.community_id }"
                  @click="selectTheme(theme)"
                >
                  <div class="theme-row-body">
                    <div class="theme-row-name">{{ theme.theme_name || theme.community_id }}</div>
                    <div class="theme-row-id">{{ theme.community_id }}</div>
                  </div>
                  <div class="theme-row-score">
                    <div class="score-bar-wrap">
                      <div class="score-bar" :style="{ width: pct(theme.exposure_score) }"></div>
                    </div>
                    <span class="score-val">{{ Number(theme.exposure_score).toFixed(3) }}</span>
                  </div>
                </div>
              </div>
            </section>

            <!-- ── Fundamentals (B1) ─────────────────────────────── -->
            <section class="card">
              <div class="card-header">
                <span class="card-title">As-Reported Fundamentals</span>
                <span class="source-note">XBRL / B1</span>
              </div>

              <!-- No-fundamentals explicit state — NEVER silently blank -->
              <div v-if="!profile.fundamentals?.available" class="empty-state">
                <div class="empty-icon">ℹ</div>
                <div class="empty-msg-text">
                  {{ profile.fundamentals?.message || `No as-reported fundamentals available at as_of=${profile.fundamentals?.as_of_date || profile.as_of_date}.` }}
                </div>
              </div>

              <div v-else>
                <table class="data-table" v-if="fundamentalsByMetric.length">
                  <thead>
                    <tr>
                      <th>Metric</th>
                      <th>Period</th>
                      <th class="num-col">Value</th>
                      <th>Unit</th>
                      <th>Filed</th>
                    </tr>
                  </thead>
                  <tbody>
                    <tr v-for="(row, idx) in fundamentalsByMetric" :key="idx">
                      <td class="metric-name">{{ row.metric_name }}</td>
                      <td class="mono">{{ row.period_end }}</td>
                      <td class="num-col mono">{{ formatNum(row.metric_value) }}</td>
                      <td class="mono unit-cell">{{ row.unit }}</td>
                      <td class="mono date-cell">{{ row.filing_date }}</td>
                    </tr>
                  </tbody>
                </table>
              </div>
            </section>

            <!-- ── Financial Facts (B2 LLM extracted) ───────────── -->
            <section class="card" v-if="profile.financial_facts?.length">
              <div class="card-header">
                <span class="card-title">Extracted Financial Facts</span>
                <span class="source-note">LLM / B2</span>
              </div>
              <div class="facts-list">
                <div
                  v-for="(fact, idx) in profile.financial_facts"
                  :key="idx"
                  class="fact-row"
                  :class="{ 'fact-guidance': fact.is_guidance }"
                >
                  <div class="fact-main">
                    <span class="fact-metric">{{ fact.metric_name }}</span>
                    <span class="fact-period">{{ fact.period }}</span>
                    <span class="fact-value">{{ formatNum(fact.value) }} {{ fact.unit }}</span>
                    <span v-if="fact.direction" class="fact-direction">{{ fact.direction }}</span>
                    <span class="fact-type-badge" :class="fact.is_guidance ? 'badge-guidance' : 'badge-actual'">
                      {{ fact.is_guidance ? 'guidance' : 'actual' }}
                    </span>
                  </div>
                  <div class="fact-provenance" v-if="fact.evidence_chunk_id">
                    <a class="source-link" @click="openSource(fact.evidence_chunk_id)">
                      read full source →
                    </a>
                  </div>
                </div>
              </div>
            </section>

            <!-- ── SENT-D: Management-sentiment panel ────────────────── -->
            <section class="card">
              <div class="card-header">
                <span class="card-title">Management Sentiment</span>
                <span class="source-note">SENT-D</span>
                <span
                  v-if="sentimentData?.fused_tone_summary"
                  class="sent-tone-badge"
                  :class="`sent-tone-${sentimentData.fused_tone_summary.dominant_tone_severity}`"
                >
                  {{ sentimentData.fused_tone_summary.dominant_tone_label }}
                </span>
              </div>

              <!-- Loading -->
              <div v-if="sentimentLoading" class="center-msg small">
                <div class="spinner small"></div>
                <span>Loading sentiment…</span>
              </div>

              <!-- Error -->
              <div v-else-if="sentimentError" class="evidence-error">
                <span class="error-icon-small">⚠</span>
                <span>{{ sentimentError }}</span>
              </div>

              <!-- Not available (artifact absent / no rows) -->
              <div v-else-if="!sentimentData?.available" class="empty-state">
                <div class="empty-icon">ℹ</div>
                <div class="empty-msg-text">
                  {{ sentimentData?.message || 'No management-sentiment data available. Run sentiment fusion first.' }}
                </div>
              </div>

              <!-- Sentiment panel content -->
              <div v-else>
                <!-- ── Summary bar ── -->
                <div class="sent-summary-bar">
                  <div class="sent-summary-tones">
                    <span
                      v-for="(count, tone) in sentimentData.fused_tone_summary.tone_counts"
                      :key="tone"
                      class="sent-tone-pill"
                      :class="`sent-tone-${tone}`"
                    >
                      {{ tone }} <strong>{{ count }}</strong>
                    </span>
                  </div>
                  <!-- Conflict / hedged alert flags — visually explicit, never hidden -->
                  <div class="sent-alert-flags">
                    <span
                      v-if="sentimentData.fused_tone_summary.has_conflict"
                      class="sent-flag sent-flag-conflict"
                      title="At least one reading shows a direct signal conflict between LLM and LM assessments."
                    >
                      ⚡ Signal conflict
                    </span>
                    <span
                      v-if="sentimentData.fused_tone_summary.has_hedged"
                      class="sent-flag sent-flag-hedged"
                      title="At least one reading shows hedged language — positive framing with uncertainty in the text."
                    >
                      ⚠ Hedged language
                    </span>
                  </div>
                </div>

                <!-- ── Per-reading list ── -->
                <div class="sent-reading-list">
                  <div
                    v-for="reading in sentimentData.readings"
                    :key="reading.fusion_id"
                    class="sent-reading"
                    :class="`sent-reading-${reading.agreement_severity}`"
                  >
                    <!-- Reading header row (always visible) -->
                    <div
                      class="sent-reading-header"
                      @click="toggleReading(reading.fusion_id)"
                    >
                      <!-- Fused tone badge — PRIMARY signal -->
                      <span
                        class="sent-reading-tone"
                        :class="`sent-tone-${reading.fused_tone_severity}`"
                      >{{ reading.fused_tone_label }}</span>

                      <!-- Agreement badge — EXPLICITLY shown for hedged/conflict -->
                      <span
                        class="sent-reading-agreement"
                        :class="`sent-agreement-${reading.agreement_severity}`"
                      >{{ reading.agreement_label }}</span>

                      <!-- Confidence -->
                      <span class="sent-reading-conf mono">
                        {{ (reading.fused_confidence * 100).toFixed(0) }}%
                      </span>

                      <!-- Date -->
                      <span class="sent-reading-date mono">{{ reading.available_at }}</span>

                      <!-- Expand chevron -->
                      <span class="sent-chevron">
                        {{ expandedReadings.has(reading.fusion_id) ? '▼' : '▶' }}
                      </span>
                    </div>

                    <!-- Reading detail (expanded) -->
                    <div v-if="expandedReadings.has(reading.fusion_id)" class="sent-reading-detail">

                      <!-- LLM vs LM signal breakdown -->
                      <div class="sent-signals">
                        <div class="sent-signal-row">
                          <span class="sent-signal-label">LLM read</span>
                          <span class="sent-signal-val">{{ reading.direction || '—' }}</span>
                          <span v-if="reading.forward_stance" class="sent-signal-sub">
                            {{ reading.forward_stance }}
                          </span>
                          <span v-if="reading.hedging" class="sent-hedging-flag">hedge</span>
                        </div>
                        <div class="sent-signal-row">
                          <span class="sent-signal-label">LM tone</span>
                          <span class="sent-signal-val">{{ reading.lm_direction || '—' }}</span>
                          <span class="sent-signal-sub mono">
                            +{{ (reading.tone_positive * 100).toFixed(1) }}%
                            −{{ (reading.tone_negative * 100).toFixed(1) }}%
                            ~{{ (reading.tone_uncertainty * 100).toFixed(1) }}%
                          </span>
                        </div>
                      </div>

                      <!-- Lexicon matched words -->
                      <div v-if="lexiconWords(reading.lexicon_hits).length" class="sent-lexicon">
                        <span class="sent-lexicon-label">Matched words</span>
                        <span
                          v-for="entry in lexiconWords(reading.lexicon_hits)"
                          :key="entry.cat"
                          class="sent-lexicon-cat"
                        >
                          <em class="sent-lex-cat-name">{{ entry.cat }}:</em>
                          {{ entry.words.join(', ') }}
                        </span>
                      </div>

                      <!-- Chunk text preview -->
                      <div v-if="reading.chunk_text" class="sent-chunk-text">
                        {{ reading.chunk_text }}
                      </div>

                      <!-- Source attribution + "read full source" -->
                      <div class="sent-chunk-meta">
                        <span v-if="reading.section_title" class="chunk-section">
                          {{ reading.section_title }}
                        </span>
                        <span v-if="reading.document?.title" class="chunk-source">
                          {{ reading.document.title }}
                        </span>
                        <span v-if="reading.document?.published_at" class="chunk-date mono">
                          {{ reading.document.published_at }}
                        </span>
                      </div>
                      <div class="chunk-actions" v-if="reading.evidence_chunk_id">
                        <a class="source-link" @click="openSource(reading.evidence_chunk_id)">
                          read full source →
                        </a>
                      </div>
                    </div>
                  </div>
                </div>
              </div>
            </section>

          </div><!-- /left-col -->

          <!-- ── Right column: evidence by theme ─────────────────── -->
          <div class="right-col">
            <section class="card evidence-card">
              <div class="card-header">
                <span class="card-title">Evidence by Theme</span>
                <span class="count-badge" v-if="evidenceGroups.length">{{ evidenceGroups.length }} themes</span>
              </div>

              <div v-if="evidenceLoading" class="center-msg small">
                <div class="spinner small"></div>
                <span>Loading evidence…</span>
              </div>
              <div v-else-if="evidenceError" class="evidence-error">
                <span class="error-icon-small">⚠</span>
                <span>{{ evidenceError }}</span>
              </div>
              <div v-else-if="!evidenceGroups.length" class="empty-state">
                No evidence groups found. Run provenance materialization first.
              </div>

              <div v-else class="evidence-groups">
                <div
                  v-for="group in filteredEvidenceGroups"
                  :key="group.community_id"
                  class="evidence-group"
                >
                  <div
                    class="evidence-group-header"
                    @click="toggleGroup(group.community_id)"
                  >
                    <span class="group-chevron">{{ expandedGroups.has(group.community_id) ? '▼' : '▶' }}</span>
                    <span class="group-theme-name">{{ group.theme_name }}</span>
                    <span class="group-count">{{ group.chunk_count }} evidence chunks</span>
                    <router-link
                      class="theme-link"
                      :to="{ name: 'Themes', params: { runId }, query: { community: group.community_id } }"
                      @click.stop
                    >
                      view theme →
                    </router-link>
                  </div>

                  <div v-if="expandedGroups.has(group.community_id)" class="chunk-list">
                    <div
                      v-for="chunk in group.chunks"
                      :key="chunk.chunk_id"
                      class="chunk-card"
                    >
                      <!-- EG-D: quantified fact label or sentence snippet -->
                      <div v-if="chunk.fact_label" class="fact-chip">
                        <span class="fact-chip-icon">&#9632;</span>
                        {{ chunk.fact_label }}
                      </div>
                      <div class="chunk-text">
                        {{ chunk.text ? (chunk.text.length > 320 ? chunk.text.slice(0, 317) + '…' : chunk.text) : '(no text)' }}
                      </div>
                      <div class="chunk-meta">
                        <span v-if="chunk.section_title" class="chunk-section">{{ chunk.section_title }}</span>
                        <span v-if="chunk.block_type" class="chunk-block-type mono">{{ chunk.block_type }}</span>
                        <span v-if="chunk.available_at" class="chunk-date mono">{{ chunk.available_at }}</span>
                        <span v-if="chunk.document?.title" class="chunk-source">{{ chunk.document.title }}</span>
                      </div>
                      <div class="chunk-actions">
                        <a class="source-link" @click="openSource(chunk.chunk_id)">read full source →</a>
                      </div>
                    </div>

                    <div v-if="!group.chunks?.length" class="empty-state small">
                      No evidence chunks for this theme after PIT filter.
                    </div>
                  </div>
                </div>
              </div>
            </section>
          </div><!-- /right-col -->
        </div><!-- /two-column-layout -->
      </div><!-- /company-page -->
    </div><!-- /content-area -->

    <!-- Full-text source modal -->
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
  </div>
</template>

<script setup>
import { ref, computed, onMounted, watch } from 'vue'
import { useRouter } from 'vue-router'
import RunNav from '../components/RunNav.vue'
import { getCompanyDetail, getCompanyEvidence, getChunkSource, getCompanySentiment } from '../api/themes.js'

const props = defineProps({
  runId: { type: String, required: true },
  companyId: { type: String, required: true },
})

const router = useRouter()

// ── Profile state ──────────────────────────────────────────────────────────
const loading = ref(false)
const error = ref('')
const profile = ref(null)

// ── Evidence state ─────────────────────────────────────────────────────────
const evidenceLoading = ref(false)
const evidenceError = ref('')
const evidenceGroups = ref([])
const expandedGroups = ref(new Set())
const activeTheme = ref(null)

// ── Source modal state ─────────────────────────────────────────────────────
const sourceDoc = ref(null)
const sourceLoading = ref(false)

// ── SENT-D: Management-sentiment panel state ───────────────────────────────
const sentimentLoading = ref(false)
const sentimentError = ref('')
const sentimentData = ref(null)
const expandedReadings = ref(new Set())

// ── Computed ───────────────────────────────────────────────────────────────
const initials = computed(() => {
  if (!profile.value?.name) return '?'
  return profile.value.name
    .split(/\s+/)
    .slice(0, 2)
    .map(w => w[0]?.toUpperCase() || '')
    .join('')
})

// Sort fundamentals: newest period first, then by metric name
const fundamentalsByMetric = computed(() => {
  if (!profile.value?.fundamentals?.rows?.length) return []
  return [...profile.value.fundamentals.rows].sort((a, b) => {
    const pd = (b.period_end || '').localeCompare(a.period_end || '')
    if (pd !== 0) return pd
    return (a.metric_name || '').localeCompare(b.metric_name || '')
  })
})

// When a theme is active, show only that theme's evidence group
const filteredEvidenceGroups = computed(() => {
  if (!activeTheme.value) return evidenceGroups.value
  return evidenceGroups.value.filter(g => g.community_id === activeTheme.value)
})

// ── Helpers ────────────────────────────────────────────────────────────────
const pct = (val) => {
  if (val == null) return '0%'
  return (Number(val) * 100).toFixed(1) + '%'
}

const formatNum = (val) => {
  if (val == null) return '—'
  const n = Number(val)
  if (isNaN(n)) return String(val)
  if (Math.abs(n) >= 1e9) return (n / 1e9).toFixed(2) + 'B'
  if (Math.abs(n) >= 1e6) return (n / 1e6).toFixed(2) + 'M'
  if (Math.abs(n) >= 1e3) return (n / 1e3).toFixed(2) + 'K'
  return n.toFixed(2)
}

// ── Theme selection (filters evidence groups) ──────────────────────────────
const selectTheme = (theme) => {
  if (activeTheme.value === theme.community_id) {
    activeTheme.value = null
  } else {
    activeTheme.value = theme.community_id
    // Auto-expand the selected theme's evidence group
    const s = new Set(expandedGroups.value)
    s.add(theme.community_id)
    expandedGroups.value = s
  }
}

// ── Evidence group toggle ──────────────────────────────────────────────────
const toggleGroup = (communityId) => {
  const s = new Set(expandedGroups.value)
  if (s.has(communityId)) {
    s.delete(communityId)
  } else {
    s.add(communityId)
  }
  expandedGroups.value = s
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

// ── SENT-D: Sentiment helpers ──────────────────────────────────────────────
const toggleReading = (fusionId) => {
  const s = new Set(expandedReadings.value)
  if (s.has(fusionId)) {
    s.delete(fusionId)
  } else {
    s.add(fusionId)
  }
  expandedReadings.value = s
}

const lexiconWords = (hits) => {
  if (!hits || typeof hits !== 'object') return []
  return Object.entries(hits)
    .filter(([, words]) => Array.isArray(words) && words.length)
    .map(([cat, words]) => ({ cat, words }))
}

// ── Data loading ───────────────────────────────────────────────────────────
const loadData = async () => {
  loading.value = true
  error.value = ''
  profile.value = null
  evidenceGroups.value = []
  sentimentData.value = null
  sentimentError.value = ''
  expandedReadings.value = new Set()
  try {
    profile.value = await getCompanyDetail(props.runId, props.companyId)
  } catch (err) {
    error.value = err?.response?.data?.detail || err.message || 'Failed to load company profile'
  } finally {
    loading.value = false
  }

  // Load evidence separately (may require provenance to be materialized)
  evidenceLoading.value = true
  evidenceError.value = ''
  try {
    evidenceGroups.value = await getCompanyEvidence(props.runId, props.companyId)
    // Auto-expand first group for convenience
    if (evidenceGroups.value.length > 0) {
      expandedGroups.value = new Set([evidenceGroups.value[0].community_id])
    }
  } catch (err) {
    const status = err?.response?.status
    if (status === 404) {
      evidenceError.value = 'Evidence not materialized yet. Run POST /api/provenance/materialize first.'
    } else {
      evidenceError.value = err?.response?.data?.detail || err.message || 'Failed to load evidence'
    }
  } finally {
    evidenceLoading.value = false
  }

  // SENT-D: Load management-sentiment panel (graceful: available=false is not an error)
  sentimentLoading.value = true
  sentimentError.value = ''
  try {
    sentimentData.value = await getCompanySentiment(props.runId, props.companyId)
  } catch (err) {
    // 404 from missing run is a real error; other failures degrade gracefully
    sentimentError.value = err?.response?.data?.detail || err.message || 'Failed to load sentiment data'
  } finally {
    sentimentLoading.value = false
  }
}

onMounted(loadData)

// Reload if companyId changes (e.g. navigating from one company node to another)
watch(() => props.companyId, () => {
  activeTheme.value = null
  expandedGroups.value = new Set()
  expandedReadings.value = new Set()
  loadData()
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
  padding: 24px;
  max-width: 1400px;
  margin: 0 auto;
  width: 100%;
  box-sizing: border-box;
}

/* ── Loading / error center ── */
.center-msg {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 12px;
  margin-top: 80px;
  color: #888;
  font-size: 14px;
}

.center-msg.small { margin-top: 16px; }

.spinner {
  width: 28px;
  height: 28px;
  border: 3px solid #eee;
  border-top-color: var(--accent, #1a56db);
  border-radius: 50%;
  animation: spin 0.8s linear infinite;
}

.spinner.small { width: 20px; height: 20px; }

@keyframes spin { to { transform: rotate(360deg); } }

.error-state { color: #c0392b; }
.error-icon { font-size: 2rem; }
.error-title { font-size: 16px; font-weight: 700; }
.error-msg { font-size: 13px; font-family: var(--font-mono); }
.retry-btn {
  background: var(--accent, #1a56db);
  color: #fff;
  border: none;
  padding: 8px 18px;
  border-radius: 4px;
  cursor: pointer;
  font-size: 13px;
  margin-top: 8px;
}

/* ── Company header ── */
.company-page { display: flex; flex-direction: column; gap: 24px; }

.company-header {
  background: #FFF;
  border: 1px solid #E5E5E5;
  border-radius: 10px;
  padding: 20px 24px;
}

.company-header-inner {
  display: flex;
  align-items: center;
  gap: 18px;
}

.company-avatar {
  width: 52px;
  height: 52px;
  background: var(--accent, #1a56db);
  color: #FFF;
  border-radius: 10px;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 18px;
  font-weight: 700;
  flex-shrink: 0;
}

.company-name {
  font-size: 1.5rem;
  font-weight: 700;
  margin: 0 0 6px 0;
  color: #111;
}

.company-meta {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 4px;
}

.ticker-badge {
  background: #EEF2FF;
  color: var(--accent, #1a56db);
  border: 1px solid #C7D2FE;
  border-radius: 4px;
  padding: 2px 8px;
  font-size: 12px;
  font-family: var(--font-mono);
  font-weight: 700;
}

.entity-type {
  font-size: 12px;
  color: #888;
  background: #F5F5F5;
  border-radius: 3px;
  padding: 2px 7px;
}

.as-of {
  font-size: 11px;
  color: #aaa;
  font-family: var(--font-mono);
}

.company-id-mono {
  font-family: var(--font-mono);
  font-size: 10px;
  color: #bbb;
}

/* ── Two-column layout ── */
.two-column-layout {
  display: grid;
  grid-template-columns: 360px 1fr;
  gap: 20px;
  align-items: start;
}

@media (max-width: 900px) {
  .two-column-layout { grid-template-columns: 1fr; }
}

/* ── Card ── */
.card {
  background: #FFF;
  border: 1px solid #E5E5E5;
  border-radius: 8px;
  overflow: hidden;
  margin-bottom: 20px;
}

.card-header {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 12px 16px;
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
}

.source-note {
  font-size: 10px;
  color: #bbb;
  font-family: var(--font-mono);
  background: #F0F0F0;
  padding: 1px 6px;
  border-radius: 3px;
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

/* ── Empty state ── */
.empty-state {
  padding: 20px 16px;
  display: flex;
  align-items: flex-start;
  gap: 10px;
  color: #888;
  font-size: 13px;
}

.empty-state.small { padding: 12px 16px; }

.empty-icon {
  font-size: 1rem;
  flex-shrink: 0;
}

.empty-msg-text {
  line-height: 1.5;
}

/* ── Theme list ── */
.theme-list {
  display: flex;
  flex-direction: column;
}

.theme-row {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 10px 14px;
  border-bottom: 1px solid #F5F5F5;
  cursor: pointer;
  transition: background 0.12s;
}

.theme-row:last-child { border-bottom: none; }

.theme-row:hover { background: #F8F9FF; }

.theme-row.active {
  background: #EEF2FF;
  border-left: 3px solid var(--accent, #1a56db);
}

.theme-row-body { flex: 1; min-width: 0; }

.theme-row-name {
  font-size: 13px;
  font-weight: 600;
  color: #222;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.theme-row-id {
  font-size: 10px;
  color: #bbb;
  font-family: var(--font-mono);
}

.theme-row-score {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-shrink: 0;
}

.score-bar-wrap {
  width: 60px;
  height: 6px;
  background: #F0F0F0;
  border-radius: 3px;
  overflow: hidden;
}

.score-bar {
  height: 100%;
  background: var(--accent, #1a56db);
  border-radius: 3px;
  transition: width 0.3s ease;
}

.score-val {
  font-family: var(--font-mono);
  font-size: 11px;
  color: #555;
  min-width: 40px;
  text-align: right;
}

/* ── Data table (fundamentals) ── */
.data-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 12px;
}

.data-table th {
  background: #F8F9FA;
  padding: 9px 12px;
  text-align: left;
  font-size: 10px;
  font-family: var(--font-mono);
  text-transform: uppercase;
  color: #999;
  border-bottom: 1px solid #EAEAEA;
  font-weight: 600;
}

.data-table td {
  padding: 9px 12px;
  border-bottom: 1px solid #F5F5F5;
  color: #333;
  vertical-align: middle;
}

.data-table tr:last-child td { border-bottom: none; }
.data-table tr:hover td { background: #FAFBFF; }

.metric-name { font-weight: 600; color: #222; }
.mono { font-family: var(--font-mono); font-size: 11px; }
.num-col { text-align: right; }
.unit-cell { color: #888; }
.date-cell { color: #aaa; }

/* ── Financial facts (B2) ── */
.facts-list {
  display: flex;
  flex-direction: column;
}

.fact-row {
  padding: 10px 14px;
  border-bottom: 1px solid #F5F5F5;
}

.fact-row:last-child { border-bottom: none; }

.fact-row.fact-guidance { background: #FFFBF0; }

.fact-main {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
  margin-bottom: 4px;
}

.fact-metric {
  font-size: 13px;
  font-weight: 600;
  color: #222;
}

.fact-period {
  font-family: var(--font-mono);
  font-size: 11px;
  color: #888;
  background: #F5F5F5;
  padding: 1px 6px;
  border-radius: 3px;
}

.fact-value {
  font-family: var(--font-mono);
  font-size: 12px;
  color: #333;
  font-weight: 600;
}

.fact-direction {
  font-size: 11px;
  color: #059669;
  background: #ECFDF5;
  border-radius: 3px;
  padding: 1px 6px;
}

.fact-type-badge {
  font-size: 10px;
  padding: 1px 6px;
  border-radius: 3px;
  font-family: var(--font-mono);
}

.badge-guidance { background: #FEF9C3; color: #92400E; }
.badge-actual { background: #ECFDF5; color: #065F46; }

.fact-provenance {
  font-size: 11px;
}

/* ── Evidence groups ── */
.evidence-card { overflow: visible; }

.evidence-groups {
  display: flex;
  flex-direction: column;
}

.evidence-group {
  border-bottom: 1px solid #EAEAEA;
}

.evidence-group:last-child { border-bottom: none; }

.evidence-group-header {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 12px 16px;
  cursor: pointer;
  transition: background 0.12s;
  background: #FAFAFA;
}

.evidence-group-header:hover { background: #F0F4FF; }

.group-chevron {
  font-size: 10px;
  color: #aaa;
  flex-shrink: 0;
  width: 12px;
}

.group-theme-name {
  font-size: 13px;
  font-weight: 600;
  color: #222;
  flex: 1;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.group-count {
  font-size: 11px;
  color: #888;
  font-family: var(--font-mono);
  flex-shrink: 0;
}

.theme-link {
  font-size: 11px;
  color: var(--accent, #1a56db);
  text-decoration: none;
  flex-shrink: 0;
}

.theme-link:hover { text-decoration: underline; }

/* ── Chunk cards ── */
.chunk-list {
  padding: 12px 14px;
  display: flex;
  flex-direction: column;
  gap: 10px;
  background: #FFF;
}

.chunk-card {
  background: #FAFBFF;
  border: 1px solid #E8EDF8;
  border-radius: 6px;
  padding: 12px 14px;
}

/* EG-D: quantified fact chip */
.fact-chip {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  background: #EEF2FF;
  border: 1px solid #C7D2FE;
  color: #3730A3;
  border-radius: 4px;
  padding: 4px 10px;
  font-size: 12px;
  font-family: var(--font-mono);
  font-weight: 600;
  margin-bottom: 8px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  max-width: 100%;
}

.fact-chip-icon {
  font-size: 8px;
  color: var(--accent, #1a56db);
  flex-shrink: 0;
}

.chunk-text {
  font-size: 13px;
  color: #333;
  line-height: 1.6;
  margin-bottom: 8px;
  word-break: break-word;
}

.chunk-meta {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
  margin-bottom: 6px;
}

.chunk-section {
  font-size: 11px;
  color: #555;
  background: #F0F0F0;
  padding: 1px 6px;
  border-radius: 3px;
}

.chunk-block-type {
  font-size: 10px;
  color: #888;
  background: #F5F5F5;
  padding: 1px 5px;
  border-radius: 3px;
}

.chunk-date {
  font-size: 10px;
  color: #aaa;
}

.chunk-source {
  font-size: 11px;
  color: #999;
  font-style: italic;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  max-width: 200px;
}

.chunk-actions { font-size: 11px; }

/* ── Source link ── */
.source-link {
  font-size: 11px;
  color: var(--accent, #1a56db);
  cursor: pointer;
  text-decoration: none;
}

.source-link:hover { text-decoration: underline; }

/* ── Evidence error ── */
.evidence-error {
  padding: 14px 16px;
  display: flex;
  align-items: center;
  gap: 8px;
  color: #c0392b;
  font-size: 12px;
  background: #FFF5F5;
}

.error-icon-small { font-size: 1rem; }

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

/* ── SENT-D: Management-sentiment panel ── */

/* Tone badge in card header */
.sent-tone-badge {
  font-size: 10px;
  font-family: var(--font-mono);
  font-weight: 700;
  padding: 2px 8px;
  border-radius: 10px;
  text-transform: uppercase;
  letter-spacing: 0.3px;
  flex-shrink: 0;
}

/* Fused-tone colour tokens — must be visually distinct */
.sent-tone-positive  { background: #DCFCE7; color: #166534; border: 1px solid #BBF7D0; }
.sent-tone-negative  { background: #FEE2E2; color: #991B1B; border: 1px solid #FECACA; }
.sent-tone-neutral   { background: #F3F4F6; color: #374151; border: 1px solid #E5E7EB; }
/* hedged: amber — CLEARLY not a clean positive */
.sent-tone-hedged    { background: #FEF3C7; color: #92400E; border: 1px solid #FDE68A; }

/* Summary bar */
.sent-summary-bar {
  display: flex;
  align-items: flex-start;
  gap: 12px;
  padding: 10px 14px;
  border-bottom: 1px solid #F0F0F0;
  flex-wrap: wrap;
}

.sent-summary-tones {
  display: flex;
  gap: 6px;
  flex-wrap: wrap;
  flex: 1;
}

.sent-tone-pill {
  font-size: 11px;
  font-family: var(--font-mono);
  padding: 2px 8px;
  border-radius: 10px;
  white-space: nowrap;
}

/* Alert flags for conflict / hedged — ALWAYS shown when present */
.sent-alert-flags {
  display: flex;
  gap: 6px;
  flex-wrap: wrap;
  flex-shrink: 0;
}

.sent-flag {
  font-size: 11px;
  font-weight: 600;
  padding: 3px 8px;
  border-radius: 4px;
  white-space: nowrap;
  cursor: default;
}

/* conflict: red background — unmistakable warning */
.sent-flag-conflict {
  background: #FEE2E2;
  color: #991B1B;
  border: 1px solid #FECACA;
}

/* hedged: amber — clearly distinct from positive */
.sent-flag-hedged {
  background: #FEF3C7;
  color: #92400E;
  border: 1px solid #FDE68A;
}

/* Reading list */
.sent-reading-list {
  display: flex;
  flex-direction: column;
}

.sent-reading {
  border-bottom: 1px solid #F5F5F5;
}

.sent-reading:last-child { border-bottom: none; }

/* Left-border colour by agreement severity — visual scan affordance */
.sent-reading-conflict { border-left: 3px solid #EF4444; }
.sent-reading-hedged   { border-left: 3px solid #F59E0B; }
.sent-reading-agree    { border-left: 3px solid #10B981; }

.sent-reading-header {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 9px 14px;
  cursor: pointer;
  transition: background 0.12s;
  flex-wrap: wrap;
}

.sent-reading-header:hover { background: #FAFBFF; }

.sent-reading-tone {
  font-size: 11px;
  font-family: var(--font-mono);
  font-weight: 700;
  padding: 2px 7px;
  border-radius: 3px;
  flex-shrink: 0;
}

.sent-reading-agreement {
  font-size: 10px;
  font-family: var(--font-mono);
  padding: 2px 6px;
  border-radius: 3px;
  flex-shrink: 0;
}

/* Agreement badge colours */
.sent-agreement-agree    { background: #DCFCE7; color: #166534; }
.sent-agreement-hedged   { background: #FEF3C7; color: #92400E; }
/* conflict: bright red so it can never be mistaken for positive */
.sent-agreement-conflict { background: #FEE2E2; color: #991B1B; font-weight: 700; }

.sent-reading-conf {
  font-size: 10px;
  color: #888;
  flex-shrink: 0;
}

.sent-reading-date {
  font-size: 10px;
  color: #aaa;
  flex: 1;
  text-align: right;
  min-width: 70px;
}

.sent-chevron {
  font-size: 10px;
  color: #bbb;
  flex-shrink: 0;
}

/* Reading detail (expanded) */
.sent-reading-detail {
  padding: 10px 14px 12px 14px;
  background: #FAFBFF;
  border-top: 1px solid #F0F0F0;
  display: flex;
  flex-direction: column;
  gap: 8px;
}

/* Signal breakdown rows */
.sent-signals {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.sent-signal-row {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 12px;
  flex-wrap: wrap;
}

.sent-signal-label {
  font-size: 10px;
  color: #888;
  font-family: var(--font-mono);
  text-transform: uppercase;
  width: 56px;
  flex-shrink: 0;
}

.sent-signal-val {
  font-weight: 600;
  color: #222;
  font-size: 12px;
}

.sent-signal-sub {
  font-size: 10px;
  color: #888;
  font-family: var(--font-mono);
}

.sent-hedging-flag {
  font-size: 10px;
  background: #FEF3C7;
  color: #92400E;
  border-radius: 3px;
  padding: 1px 5px;
  font-family: var(--font-mono);
  font-weight: 700;
}

/* Lexicon hits */
.sent-lexicon {
  display: flex;
  align-items: flex-start;
  gap: 8px;
  flex-wrap: wrap;
  font-size: 11px;
  color: #555;
}

.sent-lexicon-label {
  font-size: 10px;
  color: #bbb;
  font-family: var(--font-mono);
  text-transform: uppercase;
  flex-shrink: 0;
  padding-top: 1px;
}

.sent-lexicon-cat {
  background: #F0F4FF;
  border: 1px solid #DDE5FA;
  border-radius: 3px;
  padding: 1px 7px;
}

.sent-lex-cat-name {
  font-style: normal;
  color: #888;
  font-size: 10px;
  margin-right: 3px;
  font-family: var(--font-mono);
}

/* Chunk text preview in reading detail */
.sent-chunk-text {
  font-size: 12px;
  color: #444;
  line-height: 1.6;
  word-break: break-word;
  background: #FFF;
  border: 1px solid #E8EDF8;
  border-radius: 4px;
  padding: 8px 10px;
}

/* Source meta in reading detail — reuses chunk-meta classes */
.sent-chunk-meta {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}
</style>
