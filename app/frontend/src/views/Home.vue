<template>
  <div class="home-container">
    <!-- Navigation -->
    <nav class="navbar">
      <div class="nav-brand">THEME ENGINE</div>
      <div class="nav-links">
        <span class="snapshot-label" v-if="currentRun">
          Snapshot: <strong>{{ currentRun.as_of_date }}</strong>
          <span class="badge-frozen" v-if="currentRun.discovery_frozen">Frozen</span>
          <span class="badge-active" v-else>Live</span>
        </span>
        <router-link to="/admin" class="admin-link">Admin ↗</router-link>
      </div>
    </nav>

    <!-- Loading State -->
    <div v-if="loading" class="loading-state">
      <div class="loading-spinner"></div>
      <p class="loading-text">Loading theme snapshot...</p>
    </div>

    <!-- Error State -->
    <div v-else-if="loadError" class="error-state">
      <div class="error-icon">⚠</div>
      <p class="error-text">{{ loadError }}</p>
      <button class="retry-btn" @click="bootstrap">Retry</button>
    </div>

    <!-- Empty State: no runs at all -->
    <div v-else-if="!currentRun" class="empty-state">
      <div class="empty-icon">◇</div>
      <h2 class="empty-title">No Theme Snapshots Yet</h2>
      <p class="empty-desc">
        No pipeline runs have been completed yet. Visit the
        <router-link to="/admin" class="admin-inline-link">Admin area</router-link>
        to create a run and discover themes.
      </p>
    </div>

    <!-- Main Content: Theme Radar -->
    <div v-else class="main-content">
      <!-- Header: title + filter bar -->
      <div class="radar-header">
        <div class="radar-title-group">
          <div class="radar-eyebrow">
            <span class="accent-tag">THEME RADAR</span>
            <span class="snapshot-meta">as of {{ currentRun.as_of_date }}</span>
            <span class="community-count">{{ displayedCards.length }} themes</span>
          </div>
          <h1 class="radar-title">Emerging Themes</h1>
        </div>

        <div class="filter-bar">
          <div class="filter-tabs">
            <button
              class="filter-tab"
              :class="{ active: activeFilter === 'all' }"
              @click="activeFilter = 'all'"
            >
              All
            </button>
            <button
              class="filter-tab"
              :class="{ active: activeFilter === 'watched' }"
              @click="activeFilter = 'watched'"
            >
              ★ Watched
              <span class="watch-count" v-if="watchlist.size > 0">{{ watchlist.size }}</span>
            </button>
          </div>
          <div class="search-wrap">
            <input
              v-model="searchQuery"
              class="search-input"
              type="text"
              placeholder="Search themes, companies, entities..."
            />
            <span class="search-icon">⌕</span>
          </div>
        </div>
      </div>

      <!-- No results under current filter -->
      <div v-if="displayedCards.length === 0 && !loading" class="no-results">
        <span v-if="activeFilter === 'watched' && watchlist.size === 0">
          No watched themes yet — click ★ on a card to add one.
        </span>
        <span v-else>No themes match your search.</span>
      </div>

      <!-- Theme Cards Grid -->
      <div class="cards-grid">
        <div
          v-for="card in displayedCards"
          :key="card.community_id"
          class="theme-card"
          :class="{ watched: watchlist.has(card.community_id) }"
          @click="openTheme(card)"
        >
          <!-- Card top row -->
          <div class="card-top">
            <div class="card-id">{{ card.community_id }}</div>
            <button
              class="watch-btn"
              :class="{ active: watchlist.has(card.community_id) }"
              @click.stop="toggleWatch(card.community_id)"
              :title="watchlist.has(card.community_id) ? 'Remove from watchlist' : 'Add to watchlist'"
            >★</button>
          </div>

          <!-- Theme title -->
          <h3 class="card-title">{{ card.displayTitle }}</h3>

          <!-- Theme summary if available -->
          <p v-if="card.theme_summary" class="card-summary">{{ card.theme_summary }}</p>

          <!-- Companies chips -->
          <div v-if="card.top_companies?.length" class="chips-section">
            <div class="chips-label">Companies</div>
            <div class="chips-row">
              <span
                v-for="co in card.top_companies.slice(0, 5)"
                :key="co"
                class="chip chip-company"
              >{{ co }}</span>
              <span v-if="card.top_companies.length > 5" class="chip chip-more">
                +{{ card.top_companies.length - 5 }}
              </span>
            </div>
          </div>

          <!-- Entities chips -->
          <div v-if="card.top_entities?.length" class="chips-section">
            <div class="chips-label">Entities</div>
            <div class="chips-row">
              <span
                v-for="e in card.top_entities.slice(0, 4)"
                :key="e"
                class="chip chip-entity"
              >{{ e }}</span>
              <span v-if="card.top_entities.length > 4" class="chip chip-more">
                +{{ card.top_entities.length - 4 }}
              </span>
            </div>
          </div>

          <!-- Metrics badges row -->
          <div class="card-footer">
            <div class="size-badge">
              <span class="size-num">{{ card.size }}</span>
              <span class="size-label">nodes</span>
            </div>
            <div class="metric-badges" v-if="card.metrics">
              <span
                v-for="(val, key) in filteredMetrics(card.metrics)"
                :key="key"
                class="metric-badge"
                :class="`metric-${key}`"
                :title="key"
              >
                {{ metricShort(key) }} {{ formatPct(val) }}
              </span>
            </div>
            <div class="card-arrow">→</div>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { listRuns } from '../api/runs.js'
import { getCommunitiesJson, getThemeSnapshots, getThemeMetrics, getCompanyThemeExposure } from '../api/artifacts.js'

const router = useRouter()

// ─── State ───────────────────────────────────────────────────────────────────
const loading = ref(false)
const loadError = ref('')
const currentRun = ref(null)

const communities = ref([])
const snapshots = ref([])
const metrics = ref([])
const exposures = ref([])

const activeFilter = ref('all')
const searchQuery = ref('')

// ─── Watchlist (localStorage) ─────────────────────────────────────────────────
const WATCHLIST_KEY = 'theme_engine_watchlist'
const watchlist = ref(new Set())

const loadWatchlist = () => {
  try {
    const raw = JSON.parse(localStorage.getItem(WATCHLIST_KEY) || '[]')
    watchlist.value = new Set(raw)
  } catch {
    watchlist.value = new Set()
  }
}

const saveWatchlist = () => {
  try {
    localStorage.setItem(WATCHLIST_KEY, JSON.stringify([...watchlist.value]))
  } catch {}
}

const toggleWatch = (communityId) => {
  const w = new Set(watchlist.value)
  if (w.has(communityId)) {
    w.delete(communityId)
  } else {
    w.add(communityId)
  }
  watchlist.value = w
  saveWatchlist()
}

// ─── Derived cards ───────────────────────────────────────────────────────────
/**
 * Build a map from community_id to metrics row.
 * theme_metrics rows are linked via snapshots: community_id -> snapshot -> metrics
 */
const metricsMap = computed(() => {
  const map = new Map()
  for (const snap of snapshots.value) {
    const m = metrics.value.find(r => r.theme_snapshot_id === snap.theme_snapshot_id)
    if (m) {
      map.set(snap.community_id, m)
    }
  }
  return map
})

const allCards = computed(() => {
  return communities.value
    .map(c => {
      // Build display title
      let displayTitle = c.theme_name && c.theme_name.trim() && c.theme_name !== c.community_id
        ? c.theme_name
        : (c.top_entities || []).slice(0, 3).join(' · ') || c.community_id

      // Merge top_companies from exposure data if community lacks them
      const expRows = exposures.value
        .filter(e => e.community_id === c.community_id)
        .sort((a, b) => Number(b.exposure_score || 0) - Number(a.exposure_score || 0))
        .slice(0, 10)
        .map(e => e.company_id)

      const top_companies = c.top_companies?.length ? c.top_companies : expRows

      return {
        ...c,
        displayTitle,
        top_companies,
        metrics: metricsMap.value.get(c.community_id) || null
      }
    })
    .sort((a, b) => (b.size || 0) - (a.size || 0))
})

const displayedCards = computed(() => {
  let cards = allCards.value

  // Filter: watched only
  if (activeFilter.value === 'watched') {
    cards = cards.filter(c => watchlist.value.has(c.community_id))
  }

  // Filter: text search
  const q = searchQuery.value.trim().toLowerCase()
  if (q) {
    cards = cards.filter(c => {
      const haystack = [
        c.displayTitle,
        c.theme_name,
        c.theme_summary,
        ...(c.top_companies || []),
        ...(c.top_entities || [])
      ].filter(Boolean).join(' ').toLowerCase()
      return haystack.includes(q)
    })
  }

  // Cap only the default unfiltered landing view; watched/search show all matches.
  if (activeFilter.value === 'all' && !q) {
    cards = cards.slice(0, 12)
  }
  return cards
})

// ─── Metrics helpers ──────────────────────────────────────────────────────────
const METRIC_KEYS = ['strength', 'cohesion', 'novelty', 'saturation']

const filteredMetrics = (m) => {
  if (!m) return {}
  const out = {}
  for (const k of METRIC_KEYS) {
    if (m[k] != null) out[k] = m[k]
  }
  return out
}

const metricShort = (key) => {
  const shorts = { strength: 'STR', cohesion: 'COH', novelty: 'NOV', saturation: 'SAT' }
  return shorts[key] || key.slice(0, 3).toUpperCase()
}

const formatPct = (val) => {
  if (val == null) return ''
  return (Number(val) * 100).toFixed(0) + '%'
}

// ─── Navigation ──────────────────────────────────────────────────────────────
const openTheme = (card) => {
  if (!currentRun.value) return
  router.push({ name: 'Themes', params: { runId: currentRun.value.run_id }, query: { community: card.community_id } })
}

// ─── Data loading ─────────────────────────────────────────────────────────────
const bootstrap = async () => {
  loading.value = true
  loadError.value = ''
  currentRun.value = null
  communities.value = []
  snapshots.value = []
  metrics.value = []
  exposures.value = []

  try {
    const runs = await listRuns()
    if (!runs || runs.length === 0) {
      loading.value = false
      return
    }

    // Pick the most recent frozen run; fall back to newest
    const frozen = runs.find(r => r.discovery_frozen)
    currentRun.value = frozen || runs[0]

    const runId = currentRun.value.run_id
    const [commRes, snapRes, metricsRes, expRes] = await Promise.allSettled([
      getCommunitiesJson(runId),
      getThemeSnapshots(runId),
      getThemeMetrics(runId),
      getCompanyThemeExposure(runId)
    ])

    communities.value = commRes.status === 'fulfilled' ? (commRes.value?.communities || []) : []
    snapshots.value = snapRes.status === 'fulfilled' ? (snapRes.value?.snapshots || []) : []
    metrics.value = metricsRes.status === 'fulfilled' ? (metricsRes.value || []) : []
    exposures.value = expRes.status === 'fulfilled' ? (expRes.value || []) : []
  } catch (err) {
    loadError.value = err?.response?.data?.detail || err.message || 'Failed to load snapshot'
  } finally {
    loading.value = false
  }
}

onMounted(() => {
  loadWatchlist()
  bootstrap()
})
</script>

<style scoped>
/* ── Layout ── */
.home-container {
  min-height: 100vh;
  background: var(--white);
  font-family: var(--font-sans);
  color: var(--black);
}

/* ── Navbar ── */
.navbar {
  height: 60px;
  background: var(--black);
  color: var(--white);
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 0 40px;
  position: sticky;
  top: 0;
  z-index: 100;
}

.nav-brand {
  font-family: var(--font-mono);
  font-weight: 800;
  letter-spacing: 1px;
  font-size: 1.2rem;
}

.nav-links {
  display: flex;
  align-items: center;
  gap: 20px;
}

.snapshot-label {
  font-family: var(--font-mono);
  font-size: 0.75rem;
  color: #aaa;
  display: flex;
  align-items: center;
  gap: 8px;
}

.snapshot-label strong {
  color: #fff;
}

.badge-frozen {
  background: #1a7a40;
  color: #fff;
  padding: 2px 7px;
  font-size: 0.65rem;
  font-family: var(--font-mono);
  font-weight: 700;
  border-radius: 2px;
  letter-spacing: 0.5px;
}

.badge-active {
  background: #3730a3;
  color: #fff;
  padding: 2px 7px;
  font-size: 0.65rem;
  font-family: var(--font-mono);
  font-weight: 700;
  border-radius: 2px;
}

.admin-link {
  font-family: var(--font-mono);
  font-size: 0.8rem;
  color: #aaa;
  text-decoration: none;
  transition: color 0.2s;
}

.admin-link:hover {
  color: #fff;
}

/* ── Loading / Error / Empty ── */
.loading-state,
.error-state,
.empty-state {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  min-height: calc(100vh - 60px);
  gap: 16px;
  color: var(--gray-text);
  text-align: center;
  padding: 40px;
}

.loading-spinner {
  width: 36px;
  height: 36px;
  border: 3px solid #eee;
  border-top-color: var(--accent);
  border-radius: 50%;
  animation: spin 0.8s linear infinite;
}

@keyframes spin {
  to { transform: rotate(360deg); }
}

.loading-text {
  font-family: var(--font-mono);
  font-size: 0.85rem;
  color: #999;
}

.error-icon {
  font-size: 2rem;
  color: #c0392b;
}

.error-text {
  font-size: 0.95rem;
  color: #c0392b;
  max-width: 480px;
}

.retry-btn {
  background: var(--black);
  color: var(--white);
  border: none;
  padding: 10px 24px;
  font-family: var(--font-mono);
  font-size: 0.85rem;
  cursor: pointer;
  transition: background 0.2s;
}

.retry-btn:hover {
  background: var(--accent);
}

.empty-icon {
  font-size: 2.5rem;
  color: #ccc;
}

.empty-title {
  font-size: 1.6rem;
  font-weight: 600;
  color: var(--black);
}

.empty-desc {
  font-size: 0.95rem;
  color: var(--gray-text);
  max-width: 480px;
  line-height: 1.6;
}

.admin-inline-link {
  color: var(--accent);
  text-decoration: underline;
}

/* ── Main content ── */
.main-content {
  max-width: 1400px;
  margin: 0 auto;
  padding: 40px 40px 80px;
}

/* ── Radar header ── */
.radar-header {
  display: flex;
  justify-content: space-between;
  align-items: flex-end;
  gap: 30px;
  margin-bottom: 36px;
  flex-wrap: wrap;
}

.radar-eyebrow {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 10px;
  flex-wrap: wrap;
}

.accent-tag {
  background: var(--accent);
  color: var(--white);
  padding: 4px 10px;
  font-family: var(--font-mono);
  font-weight: 700;
  letter-spacing: 1px;
  font-size: 0.72rem;
}

.snapshot-meta {
  font-family: var(--font-mono);
  font-size: 0.78rem;
  color: #888;
}

.community-count {
  font-family: var(--font-mono);
  font-size: 0.78rem;
  color: #bbb;
}

.radar-title {
  font-size: 2.6rem;
  font-weight: 600;
  letter-spacing: -1.5px;
  line-height: 1.1;
}

/* ── Filter bar ── */
.filter-bar {
  display: flex;
  align-items: center;
  gap: 16px;
  flex-wrap: wrap;
}

.filter-tabs {
  display: flex;
  border: 1px solid var(--border);
}

.filter-tab {
  background: transparent;
  border: none;
  padding: 8px 18px;
  font-family: var(--font-mono);
  font-size: 0.8rem;
  cursor: pointer;
  color: #666;
  transition: all 0.15s;
  display: flex;
  align-items: center;
  gap: 6px;
}

.filter-tab:not(:last-child) {
  border-right: 1px solid var(--border);
}

.filter-tab.active {
  background: var(--black);
  color: var(--white);
}

.filter-tab:hover:not(.active) {
  background: var(--gray-light);
}

.watch-count {
  background: var(--accent);
  color: #fff;
  font-size: 0.68rem;
  padding: 1px 5px;
  border-radius: 8px;
  font-weight: 700;
}

.search-wrap {
  position: relative;
}

.search-input {
  padding: 9px 14px 9px 36px;
  border: 1px solid var(--border);
  background: #FAFAFA;
  font-family: var(--font-sans);
  font-size: 0.88rem;
  width: 260px;
  outline: none;
  transition: border-color 0.2s;
}

.search-input:focus {
  border-color: var(--accent);
  background: #fff;
}

.search-icon {
  position: absolute;
  left: 12px;
  top: 50%;
  transform: translateY(-50%);
  color: #aaa;
  font-size: 1rem;
  pointer-events: none;
}

/* ── No results ── */
.no-results {
  text-align: center;
  color: #999;
  font-size: 0.9rem;
  padding: 60px 20px;
  border: 1px dashed var(--border);
  font-family: var(--font-mono);
}

/* ── Cards grid ── */
.cards-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(340px, 1fr));
  gap: 20px;
}

/* ── Theme card ── */
.theme-card {
  border: 1px solid var(--border);
  padding: 22px 22px 18px;
  background: #fff;
  cursor: pointer;
  transition: border-color 0.18s, box-shadow 0.18s, transform 0.15s;
  display: flex;
  flex-direction: column;
  gap: 14px;
  position: relative;
}

.theme-card:hover {
  border-color: var(--accent);
  box-shadow: 0 4px 20px rgba(26, 86, 219, 0.08);
  transform: translateY(-2px);
}

.theme-card.watched {
  border-color: #f59e0b;
  background: #fffbf0;
}

.theme-card.watched:hover {
  border-color: #d97706;
  box-shadow: 0 4px 20px rgba(245, 158, 11, 0.12);
}

/* Card top row */
.card-top {
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.card-id {
  font-family: var(--font-mono);
  font-size: 0.72rem;
  color: #bbb;
}

.watch-btn {
  background: transparent;
  border: none;
  font-size: 1.2rem;
  cursor: pointer;
  color: #ddd;
  line-height: 1;
  padding: 2px 4px;
  transition: color 0.15s, transform 0.1s;
}

.watch-btn:hover {
  color: #f59e0b;
  transform: scale(1.2);
}

.watch-btn.active {
  color: #f59e0b;
}

/* Card title */
.card-title {
  font-size: 1.05rem;
  font-weight: 700;
  line-height: 1.35;
  color: var(--black);
  letter-spacing: -0.3px;
}

/* Card summary */
.card-summary {
  font-size: 0.82rem;
  color: var(--gray-text);
  line-height: 1.55;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}

/* Chips */
.chips-section {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.chips-label {
  font-family: var(--font-mono);
  font-size: 0.68rem;
  color: #bbb;
  text-transform: uppercase;
  letter-spacing: 0.5px;
}

.chips-row {
  display: flex;
  flex-wrap: wrap;
  gap: 5px;
}

.chip {
  padding: 3px 9px;
  font-size: 0.75rem;
  border-radius: 2px;
  white-space: nowrap;
  max-width: 160px;
  overflow: hidden;
  text-overflow: ellipsis;
}

.chip-company {
  background: #ecfdf5;
  color: #065f46;
  border: 1px solid #a7f3d0;
}

.chip-entity {
  background: #eef2ff;
  color: #3730a3;
  border: 1px solid #c7d2fe;
}

.chip-more {
  background: #f5f5f5;
  color: #999;
  border: 1px solid #e5e5e5;
}

/* Card footer */
.card-footer {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
  margin-top: auto;
  padding-top: 2px;
  border-top: 1px solid #f0f0f0;
}

.size-badge {
  display: flex;
  align-items: baseline;
  gap: 3px;
}

.size-num {
  font-family: var(--font-mono);
  font-weight: 700;
  font-size: 0.95rem;
  color: var(--black);
}

.size-label {
  font-family: var(--font-mono);
  font-size: 0.68rem;
  color: #aaa;
}

.metric-badges {
  display: flex;
  flex-wrap: wrap;
  gap: 5px;
  flex: 1;
}

.metric-badge {
  font-family: var(--font-mono);
  font-size: 0.68rem;
  padding: 2px 7px;
  border-radius: 2px;
  font-weight: 700;
  background: #f0f0f0;
  color: #555;
}

.metric-strength  { background: #e0f2fe; color: #0369a1; }
.metric-cohesion  { background: #f0fdf4; color: #15803d; }
.metric-novelty   { background: #fdf4ff; color: #7e22ce; }
.metric-saturation { background: #fff7ed; color: #c2410c; }

.card-arrow {
  margin-left: auto;
  color: #ccc;
  font-size: 0.9rem;
  transition: color 0.15s;
}

.theme-card:hover .card-arrow {
  color: var(--accent);
}

/* ── Responsive ── */
@media (max-width: 900px) {
  .main-content {
    padding: 24px 20px 60px;
  }

  .radar-header {
    flex-direction: column;
    align-items: flex-start;
    gap: 16px;
  }

  .radar-title {
    font-size: 2rem;
  }

  .cards-grid {
    grid-template-columns: 1fr;
  }

  .search-input {
    width: 200px;
  }

  .navbar {
    padding: 0 20px;
  }
}
</style>
