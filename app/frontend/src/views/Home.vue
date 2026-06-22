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

    <!-- Main Content -->
    <div v-else class="main-content">

      <!-- ─── MAIN THEME VIEW (hierarchy exists) ─────────────────────── -->
      <template v-if="mainThemes.length > 0">
        <!-- Header -->
        <div class="radar-header">
          <div class="radar-title-group">
            <div class="radar-eyebrow">
              <span class="accent-tag">THEME RADAR</span>
              <span class="snapshot-meta">as of {{ currentRun.as_of_date }}</span>
              <span class="community-count">{{ displayedMainThemes.length }} themes</span>
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
                placeholder="Search main themes..."
              />
              <span class="search-icon">⌕</span>
            </div>
            <!-- Hide dormant toggle -->
            <label class="dormant-toggle" v-if="relevanceLoaded">
              <span class="dormant-toggle-label">Hide dormant</span>
              <span class="toggle-switch-wrap">
                <input type="checkbox" v-model="hideDormant" class="toggle-input" />
                <span class="toggle-slider"></span>
              </span>
            </label>

            <!-- Factor-level filter chips (only when /levels data is available) -->
            <div class="level-filter-chips" v-if="levelsData">
              <span class="level-chips-label">Level:</span>
              <button
                v-for="lvl in ['macro', 'industry', 'company', 'idiosyncratic']"
                :key="lvl"
                class="level-chip"
                :class="[`level-chip-${lvl}`, { active: activeLevelFilters.has(lvl) }]"
                @click="toggleLevelFilter(lvl)"
              >{{ lvl }}</button>
              <button
                class="level-chip level-chip-all"
                :class="{ active: activeLevelFilters.size === 4 }"
                title="Show all factor levels"
                @click="resetLevelFilters"
              >all</button>
            </div>
          </div>
        </div>

        <!-- No results -->
        <div v-if="displayedMainThemes.length === 0" class="no-results">
          <span v-if="activeFilter === 'watched' && watchlist.size === 0">
            No watched themes yet — click ★ on a card to add one.
          </span>
          <span v-else-if="hideDormant">
            No active themes match your filters.
            <button class="inline-link-btn" @click="hideDormant = false">Show dormant</button>
          </span>
          <span v-else>No themes match your search.</span>
        </div>

        <!-- Main Theme Cards Grid -->
        <div class="cards-grid">
          <div
            v-for="mt in displayedMainThemes"
            :key="mt.name"
            class="theme-card main-theme-card"
            :class="{ watched: watchlist.has(mt.name), expanded: expandedTheme === mt.name }"
            @click="toggleExpand(mt)"
          >
            <!-- Card top row -->
            <div class="card-top">
              <div class="card-sub-count">{{ mt.sub_theme_ids.length }} sub-themes</div>
              <div class="card-top-right">
                <!-- Factor-level badge (from /levels) -->
                <span
                  v-if="mt.dominant_level"
                  class="level-badge"
                  :class="`level-badge-${mt.dominant_level}`"
                  :title="`Dominant factor level: ${mt.dominant_level}`"
                >{{ mt.dominant_level }}</span>
                <!-- State badge (from relevance) -->
                <span
                  v-if="mt.state"
                  class="state-badge"
                  :class="`state-${mt.state}`"
                >{{ mt.state }}</span>
                <button
                  class="watch-btn"
                  :class="{ active: watchlist.has(mt.name) }"
                  @click.stop="toggleWatch(mt.name)"
                  :title="watchlist.has(mt.name) ? 'Remove from watchlist' : 'Add to watchlist'"
                >★</button>
              </div>
            </div>

            <!-- Theme title -->
            <h3 class="card-title">{{ mt.name }}</h3>

            <!-- Open the whole main theme as one story + graph -->
            <button class="view-story-btn" @click.stop="openMainTheme(mt)">View story &amp; graph →</button>

            <!-- Summary -->
            <p v-if="mt.summary" class="card-summary">{{ mt.summary }}</p>

            <!-- Metrics row -->
            <div class="card-footer">
              <div class="size-badge">
                <span class="size-num">{{ mt.size }}</span>
                <span class="size-label">nodes</span>
              </div>
              <!-- Relevance score bar -->
              <div class="relevance-bar-wrap" v-if="mt.relevance_score != null">
                <div class="relevance-bar">
                  <div
                    class="relevance-fill"
                    :class="`state-fill-${mt.state || 'unknown'}`"
                    :style="{ width: (mt.relevance_score * 100).toFixed(0) + '%' }"
                  ></div>
                </div>
                <span class="relevance-score">{{ (mt.relevance_score * 100).toFixed(0) }}</span>
              </div>
              <!-- Last evidence date -->
              <div class="last-evidence" v-if="mt.last_evidence_at">
                {{ formatDate(mt.last_evidence_at) }}
              </div>
              <div class="sub-theme-pills">
                <span
                  v-for="sid in mt.sub_theme_ids.slice(0, 3)"
                  :key="sid"
                  class="sub-pill"
                >{{ subThemeName(sid) }}</span>
                <span v-if="mt.sub_theme_ids.length > 3" class="chip chip-more">
                  +{{ mt.sub_theme_ids.length - 3 }} more
                </span>
              </div>
              <div class="card-arrow">{{ expandedTheme === mt.name ? '↑' : '↓' }}</div>
            </div>

            <!-- Expanded sub-themes list -->
            <div v-if="expandedTheme === mt.name" class="sub-themes-panel" @click.stop>
              <div class="sub-themes-header">
                Sub-themes
                <span v-if="levelsData && substantiveSubIds(mt).length < mt.sub_theme_ids.length" class="sub-themes-noise-note">
                  ({{ mt.sub_theme_ids.length - substantiveSubIds(mt).length }} low-signal hidden)
                </span>
              </div>
              <div class="sub-themes-list">
                <div
                  v-for="sid in substantiveSubIds(mt)"
                  :key="sid"
                  class="sub-theme-row"
                  @click.stop="openSubTheme(sid)"
                >
                  <div class="sub-theme-info">
                    <div class="sub-theme-name-row">
                      <div class="sub-theme-name">{{ subThemeName(sid) }}</div>
                      <!-- Sub-theme dominant_level badge -->
                      <span
                        v-if="subThemeDominantLevel(sid)"
                        class="level-badge level-badge-sm"
                        :class="`level-badge-${subThemeDominantLevel(sid)}`"
                        :title="`Dominant factor level: ${subThemeDominantLevel(sid)}`"
                      >{{ subThemeDominantLevel(sid) }}</span>
                      <!-- Sub-theme state badge from relevance -->
                      <span
                        v-if="subThemeState(sid)"
                        class="state-badge state-badge-sm"
                        :class="`state-${subThemeState(sid)}`"
                      >{{ subThemeState(sid) }}</span>
                    </div>
                    <div class="sub-theme-meta" v-if="subThemeMeta(sid)">
                      <span class="sub-meta-item">{{ subThemeMeta(sid).size }} nodes</span>
                      <span v-if="subThemeCompanies(sid).length" class="sub-meta-item">
                        {{ subThemeCompanies(sid).slice(0, 3).join(', ') }}
                        <span v-if="subThemeCompanies(sid).length > 3"> +{{ subThemeCompanies(sid).length - 3 }}</span>
                      </span>
                      <span v-if="subThemeLastEvidence(sid)" class="sub-meta-item sub-meta-date">
                        {{ formatDate(subThemeLastEvidence(sid)) }}
                      </span>
                    </div>
                    <p v-if="subThemeSummary(sid)" class="sub-theme-summary">{{ subThemeSummary(sid) }}</p>
                  </div>
                  <div class="sub-theme-arrow">→</div>
                </div>
              </div>
            </div>
          </div>
        </div>
      </template>

      <!-- ─── FALLBACK: flat community view + build action ───────────── -->
      <template v-else>
        <!-- Header with build action -->
        <div class="radar-header">
          <div class="radar-title-group">
            <div class="radar-eyebrow">
              <span class="accent-tag">THEME RADAR</span>
              <span class="snapshot-meta">as of {{ currentRun.as_of_date }}</span>
              <span class="community-count">{{ displayedCards.length }} communities</span>
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
                <span class="watch-count" v-if="flatWatchlist.size > 0">{{ flatWatchlist.size }}</span>
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

            <!-- Build main themes CTA (secondary, admin action) -->
            <button
              class="build-hierarchy-btn"
              @click="triggerBuildHierarchy"
              :disabled="buildingHierarchy"
              :title="buildingHierarchy ? 'Building main themes via LLM…' : 'Cluster communities into main themes (LLM, ~20s)'"
            >
              <span v-if="buildingHierarchy" class="build-spinner"></span>
              <span v-else>⊕</span>
              {{ buildingHierarchy ? 'Building themes…' : 'Build main themes' }}
            </button>
          </div>
        </div>

        <!-- Build error -->
        <div v-if="buildError" class="build-error-msg">
          {{ buildError }}
        </div>

        <!-- No results under current filter -->
        <div v-if="displayedCards.length === 0" class="no-results">
          <span v-if="activeFilter === 'watched' && flatWatchlist.size === 0">
            No watched themes yet — click ★ on a card to add one.
          </span>
          <span v-else>No themes match your search.</span>
        </div>

        <!-- Flat Community Cards Grid -->
        <div class="cards-grid">
          <div
            v-for="card in displayedCards"
            :key="card.community_id"
            class="theme-card"
            :class="{ watched: flatWatchlist.has(card.community_id) }"
            @click="openTheme(card)"
          >
            <!-- Card top row -->
            <div class="card-top">
              <div class="card-id">{{ card.community_id }}</div>
              <button
                class="watch-btn"
                :class="{ active: flatWatchlist.has(card.community_id) }"
                @click.stop="toggleFlatWatch(card.community_id)"
                :title="flatWatchlist.has(card.community_id) ? 'Remove from watchlist' : 'Add to watchlist'"
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
      </template>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { listRuns } from '../api/runs.js'
import { getCommunitiesJson, getThemeSnapshots, getThemeMetrics, getCompanyThemeExposure } from '../api/artifacts.js'
import { getThemeHierarchy, buildThemeHierarchy, getThemeRelevance, getThemeLevels } from '../api/themes.js'

const router = useRouter()

// ─── State ───────────────────────────────────────────────────────────────────
const loading = ref(false)
const loadError = ref('')
const currentRun = ref(null)

// Flat community data (for fallback view and sub-theme resolution)
const communities = ref([])
const snapshots = ref([])
const metrics = ref([])
const exposures = ref([])

// Main theme hierarchy
const mainThemes = ref([])
const hierarchyNotBuilt = ref(false)

// Temporal relevance
const relevanceData = ref(null)   // full relevance response
const relevanceLoaded = ref(false)

// Factor levels
const levelsData = ref(null)      // full levels response
const activeLevelFilters = ref(new Set(['macro', 'industry', 'company', 'idiosyncratic']))

// Expand/drill-in state
const expandedTheme = ref(null)

// Build action
const buildingHierarchy = ref(false)
const buildError = ref('')

const activeFilter = ref('all')
const searchQuery = ref('')
const hideDormant = ref(true)

// ─── Watchlists (localStorage) ────────────────────────────────────────────────
// Main-theme watchlist (keyed by theme name)
const MAIN_WATCHLIST_KEY = 'theme_engine_main_watchlist'
const watchlist = ref(new Set())

const loadWatchlist = () => {
  try {
    const raw = JSON.parse(localStorage.getItem(MAIN_WATCHLIST_KEY) || '[]')
    watchlist.value = new Set(raw)
  } catch {
    watchlist.value = new Set()
  }
}

const saveWatchlist = () => {
  try {
    localStorage.setItem(MAIN_WATCHLIST_KEY, JSON.stringify([...watchlist.value]))
  } catch {}
}

const toggleWatch = (themeName) => {
  const w = new Set(watchlist.value)
  if (w.has(themeName)) w.delete(themeName)
  else w.add(themeName)
  watchlist.value = w
  saveWatchlist()
}

// Flat community watchlist (keyed by community_id)
const FLAT_WATCHLIST_KEY = 'theme_engine_watchlist'
const flatWatchlist = ref(new Set())

const loadFlatWatchlist = () => {
  try {
    const raw = JSON.parse(localStorage.getItem(FLAT_WATCHLIST_KEY) || '[]')
    flatWatchlist.value = new Set(raw)
  } catch {
    flatWatchlist.value = new Set()
  }
}

const saveFlatWatchlist = () => {
  try {
    localStorage.setItem(FLAT_WATCHLIST_KEY, JSON.stringify([...flatWatchlist.value]))
  } catch {}
}

const toggleFlatWatch = (communityId) => {
  const w = new Set(flatWatchlist.value)
  if (w.has(communityId)) w.delete(communityId)
  else w.add(communityId)
  flatWatchlist.value = w
  saveFlatWatchlist()
}

// ─── Community lookup helpers ─────────────────────────────────────────────────
const communityMap = computed(() => {
  const m = new Map()
  for (const c of communities.value) m.set(c.community_id, c)
  return m
})

const subThemeName = (communityId) => {
  const c = communityMap.value.get(communityId)
  if (!c) return communityId
  return c.theme_name && c.theme_name.trim() && c.theme_name !== communityId
    ? c.theme_name
    : ((c.top_entities || []).slice(0, 3).join(' · ') || communityId)
}

const subThemeMeta = (communityId) => communityMap.value.get(communityId) || null

const subThemeCompanies = (communityId) => {
  const c = communityMap.value.get(communityId)
  if (c?.top_companies?.length) return c.top_companies
  const expRows = exposures.value
    .filter(e => e.community_id === communityId)
    .sort((a, b) => Number(b.exposure_score || 0) - Number(a.exposure_score || 0))
    .slice(0, 6)
    .map(e => e.company_id)
  return expRows
}

const subThemeSummary = (communityId) => {
  return communityMap.value.get(communityId)?.theme_summary || ''
}

// ─── Relevance lookup helpers ─────────────────────────────────────────────────
// Maps community_id -> relevance entry
const subThemeRelevanceMap = computed(() => {
  if (!relevanceData.value?.themes) return new Map()
  const m = new Map()
  for (const t of relevanceData.value.themes) {
    m.set(t.community_id, t)
  }
  return m
})

const subThemeState = (communityId) => {
  return subThemeRelevanceMap.value.get(communityId)?.state || null
}

const subThemeLastEvidence = (communityId) => {
  return subThemeRelevanceMap.value.get(communityId)?.last_evidence_at || null
}

// ─── Merge relevance into main themes ────────────────────────────────────────
const mainThemesWithRelevance = computed(() => {
  const themes = mainThemes.value
  if (!relevanceData.value?.main_themes) return themes

  const relMap = new Map()
  for (const r of relevanceData.value.main_themes) {
    relMap.set(r.name, r)
  }

  const merged = themes.map(t => {
    const rel = relMap.get(t.name)
    if (!rel) return t
    return {
      ...t,
      relevance_score: rel.relevance_score,
      state: rel.state,
      last_evidence_at: rel.last_evidence_at
    }
  })

  // Sort by relevance_score descending (themes without score go to end)
  merged.sort((a, b) => {
    const sa = a.relevance_score ?? -1
    const sb = b.relevance_score ?? -1
    return sb - sa
  })

  return merged
})

// ─── Factor levels lookup ─────────────────────────────────────────────────────
// Maps main-theme name -> dominant_level (from /levels response)
const mainThemeLevelMap = computed(() => {
  if (!levelsData.value?.main_themes) return new Map()
  const m = new Map()
  for (const t of levelsData.value.main_themes) {
    if (t.name && t.dominant_level) m.set(t.name, t.dominant_level)
  }
  return m
})

// Maps community_id -> levels entry (for sub-theme filtering + badging)
const subThemeLevelMap = computed(() => {
  if (!levelsData.value?.themes) return new Map()
  const m = new Map()
  for (const t of levelsData.value.themes) {
    m.set(t.community_id, t)
  }
  return m
})

const FACTOR_LEVELS = ['macro', 'industry', 'company', 'idiosyncratic']

const allLevelFiltersActive = computed(() =>
  FACTOR_LEVELS.every(l => activeLevelFilters.value.has(l))
)

const toggleLevelFilter = (level) => {
  const next = new Set(activeLevelFilters.value)
  if (next.has(level)) {
    // Don't allow deselecting the last active filter
    if (next.size === 1) return
    next.delete(level)
  } else {
    next.add(level)
  }
  activeLevelFilters.value = next
}

// Merge dominant_level into themes that already have relevance merged in
const mainThemesWithLevels = computed(() => {
  return mainThemesWithRelevance.value.map(t => ({
    ...t,
    dominant_level: mainThemeLevelMap.value.get(t.name) || null
  }))
})

// ─── Main theme filters ───────────────────────────────────────────────────────
const displayedMainThemes = computed(() => {
  let themes = mainThemesWithLevels.value

  // Hide dormant (default ON, only when relevance data is available)
  if (hideDormant.value && relevanceLoaded.value) {
    themes = themes.filter(t => t.state !== 'dormant')
  }

  if (activeFilter.value === 'watched') {
    themes = themes.filter(t => watchlist.value.has(t.name))
  }

  const q = searchQuery.value.trim().toLowerCase()
  if (q) {
    themes = themes.filter(t => {
      const haystack = [t.name, t.summary].filter(Boolean).join(' ').toLowerCase()
      return haystack.includes(q)
    })
  }

  // Factor-level filter (only when levels data is available and not all selected)
  if (levelsData.value && !allLevelFiltersActive.value) {
    themes = themes.filter(t =>
      !t.dominant_level || activeLevelFilters.value.has(t.dominant_level)
    )
  }

  return themes
})

// ─── Sub-theme level helpers ──────────────────────────────────────────────────
// Returns the dominant_level for a sub-theme community
const subThemeDominantLevel = (communityId) => {
  return subThemeLevelMap.value.get(communityId)?.dominant_level || null
}

// Returns true if a sub-theme is substantive (no /levels data = assume substantive)
const subThemeIsSubstantive = (communityId) => {
  if (!levelsData.value) return true
  const entry = subThemeLevelMap.value.get(communityId)
  if (!entry) return true  // not in levels data, show it
  return entry.substantive !== false
}

// Filter a main theme's sub_theme_ids to only substantive ones
const substantiveSubIds = (mt) => {
  if (!levelsData.value) return mt.sub_theme_ids
  return mt.sub_theme_ids.filter(sid => subThemeIsSubstantive(sid))
}

// ─── Expand/drill-in ─────────────────────────────────────────────────────────
const toggleExpand = (mt) => {
  expandedTheme.value = expandedTheme.value === mt.name ? null : mt.name
}

const openSubTheme = (communityId) => {
  if (!currentRun.value) return
  router.push({
    name: 'Themes',
    params: { runId: currentRun.value.run_id },
    query: { community: communityId }
  })
}

// Open a whole main theme as one combined story + graph
const openMainTheme = (mt) => {
  if (!currentRun.value) return
  router.push({
    name: 'MainTheme',
    params: { runId: currentRun.value.run_id },
    query: { name: mt.name, communities: (mt.sub_theme_ids || []).join(',') }
  })
}

// Reset the factor-level filter to show all levels
const resetLevelFilters = () => {
  activeLevelFilters.value = new Set(['macro', 'industry', 'company', 'idiosyncratic'])
}

// ─── Build hierarchy action ───────────────────────────────────────────────────
const triggerBuildHierarchy = async () => {
  if (!currentRun.value || buildingHierarchy.value) return
  buildingHierarchy.value = true
  buildError.value = ''
  try {
    const result = await buildThemeHierarchy(currentRun.value.run_id)
    mainThemes.value = result?.main_themes || []
    hierarchyNotBuilt.value = false
  } catch (err) {
    buildError.value = err?.response?.data?.detail || err.message || 'Failed to build themes'
  } finally {
    buildingHierarchy.value = false
  }
}

// ─── Flat community cards (fallback view) ────────────────────────────────────
const metricsMap = computed(() => {
  const map = new Map()
  for (const snap of snapshots.value) {
    const m = metrics.value.find(r => r.theme_snapshot_id === snap.theme_snapshot_id)
    if (m) map.set(snap.community_id, m)
  }
  return map
})

const allCards = computed(() => {
  return communities.value
    .map(c => {
      const displayTitle = c.theme_name && c.theme_name.trim() && c.theme_name !== c.community_id
        ? c.theme_name
        : (c.top_entities || []).slice(0, 3).join(' · ') || c.community_id

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

  if (activeFilter.value === 'watched') {
    cards = cards.filter(c => flatWatchlist.value.has(c.community_id))
  }

  const q = searchQuery.value.trim().toLowerCase()
  if (q) {
    cards = cards.filter(c => {
      const haystack = [
        c.displayTitle, c.theme_name, c.theme_summary,
        ...(c.top_companies || []), ...(c.top_entities || [])
      ].filter(Boolean).join(' ').toLowerCase()
      return haystack.includes(q)
    })
  }

  if (activeFilter.value === 'all' && !q) {
    cards = cards.slice(0, 12)
  }
  return cards
})

// ─── Flat card navigation ─────────────────────────────────────────────────────
const openTheme = (card) => {
  if (!currentRun.value) return
  router.push({
    name: 'Themes',
    params: { runId: currentRun.value.run_id },
    query: { community: card.community_id }
  })
}

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
const bootstrap = async () => {
  loading.value = true
  loadError.value = ''
  currentRun.value = null
  communities.value = []
  snapshots.value = []
  metrics.value = []
  exposures.value = []
  mainThemes.value = []
  hierarchyNotBuilt.value = false
  expandedTheme.value = null
  buildError.value = ''
  relevanceData.value = null
  relevanceLoaded.value = false
  levelsData.value = null
  activeLevelFilters.value = new Set(['macro', 'industry', 'company', 'idiosyncratic'])

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

    // Load communities in parallel with trying to get the hierarchy + relevance + levels
    const [commRes, snapRes, metricsRes, expRes, hierarchyRes, relevanceRes, levelsRes] = await Promise.allSettled([
      getCommunitiesJson(runId),
      getThemeSnapshots(runId),
      getThemeMetrics(runId),
      getCompanyThemeExposure(runId),
      getThemeHierarchy(runId),
      getThemeRelevance(runId),
      getThemeLevels(runId)
    ])

    communities.value = commRes.status === 'fulfilled' ? (commRes.value?.communities || []) : []
    snapshots.value = snapRes.status === 'fulfilled' ? (snapRes.value?.snapshots || []) : []
    metrics.value = metricsRes.status === 'fulfilled' ? (metricsRes.value || []) : []
    exposures.value = expRes.status === 'fulfilled' ? (expRes.value || []) : []

    if (hierarchyRes.status === 'fulfilled') {
      mainThemes.value = hierarchyRes.value?.main_themes || []
    } else {
      // 404 means not built yet; other errors are silent (fallback to flat view)
      hierarchyNotBuilt.value = true
    }

    // Relevance is optional — gracefully skip if unavailable
    if (relevanceRes.status === 'fulfilled' && relevanceRes.value) {
      relevanceData.value = relevanceRes.value
      relevanceLoaded.value = true
    }

    // Levels is optional — gracefully skip if unavailable
    if (levelsRes.status === 'fulfilled' && levelsRes.value) {
      levelsData.value = levelsRes.value
    }
  } catch (err) {
    loadError.value = err?.response?.data?.detail || err.message || 'Failed to load snapshot'
  } finally {
    loading.value = false
  }
}

onMounted(() => {
  loadWatchlist()
  loadFlatWatchlist()
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

/* ── Dormant toggle ── */
.dormant-toggle {
  display: flex;
  align-items: center;
  gap: 8px;
  cursor: pointer;
  user-select: none;
}

.dormant-toggle-label {
  font-family: var(--font-mono);
  font-size: 0.78rem;
  color: #666;
  white-space: nowrap;
}

.toggle-switch-wrap {
  position: relative;
  display: inline-block;
  width: 36px;
  height: 20px;
}

.toggle-input {
  opacity: 0;
  width: 0;
  height: 0;
  position: absolute;
}

.toggle-slider {
  position: absolute;
  cursor: pointer;
  top: 0; left: 0; right: 0; bottom: 0;
  background: #ddd;
  border-radius: 20px;
  transition: 0.25s;
}

.toggle-slider:before {
  position: absolute;
  content: "";
  height: 14px; width: 14px;
  left: 3px; bottom: 3px;
  background: #fff;
  border-radius: 50%;
  transition: 0.25s;
}

.toggle-input:checked + .toggle-slider {
  background: var(--accent);
}

.toggle-input:checked + .toggle-slider:before {
  transform: translateX(16px);
}

/* ── State badges ── */
.state-badge {
  display: inline-block;
  padding: 2px 8px;
  font-family: var(--font-mono);
  font-size: 0.65rem;
  font-weight: 700;
  letter-spacing: 0.4px;
  border-radius: 2px;
  text-transform: uppercase;
  white-space: nowrap;
}

.state-badge-sm {
  font-size: 0.6rem;
  padding: 1px 6px;
}

.state-emerging  { background: #d1fae5; color: #065f46; }
.state-mature    { background: #dbeafe; color: #1e40af; }
.state-declining { background: #fef3c7; color: #92400e; }
.state-dormant   { background: #f3f4f6; color: #6b7280; }

/* ── Factor-level filter chips ── */
.level-filter-chips {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
}

.level-chips-label {
  font-family: var(--font-mono);
  font-size: 0.72rem;
  color: #999;
  white-space: nowrap;
}

.level-chip {
  padding: 5px 10px;
  border: 1px solid var(--border);
  background: transparent;
  font-family: var(--font-mono);
  font-size: 0.72rem;
  text-transform: lowercase;
  cursor: pointer;
  color: #888;
  border-radius: 2px;
  transition: all 0.15s;
  white-space: nowrap;
}
.level-chip.active { color: #111; font-weight: 700; }
.level-chip-all.active { background: #111; color: #fff; border-color: #111; }
.view-story-btn {
  margin: 8px 0 2px;
  padding: 6px 12px;
  border: 1px solid var(--accent, #1a56db);
  background: transparent;
  color: var(--accent, #1a56db);
  font-family: var(--font-mono);
  font-size: 0.75rem;
  cursor: pointer;
  border-radius: 3px;
  transition: all 0.15s;
}
.view-story-btn:hover { background: var(--accent, #1a56db); color: #fff; }

.level-chip:hover:not(.active) {
  border-color: #999;
  color: #555;
}

.level-chip-macro.active         { background: #eff6ff; border-color: #3b82f6; color: #1d4ed8; }
.level-chip-industry.active      { background: #f0fdf4; border-color: #22c55e; color: #15803d; }
.level-chip-company.active       { background: #fdf4ff; border-color: #a855f7; color: #7e22ce; }
.level-chip-idiosyncratic.active { background: #fff7ed; border-color: #f97316; color: #c2410c; }

/* ── Factor-level badge (on cards and sub-theme rows) ── */
.level-badge {
  display: inline-block;
  padding: 2px 7px;
  font-family: var(--font-mono);
  font-size: 0.6rem;
  font-weight: 700;
  letter-spacing: 0.3px;
  border-radius: 2px;
  text-transform: lowercase;
  white-space: nowrap;
  border: 1px solid transparent;
}

.level-badge-sm {
  font-size: 0.58rem;
  padding: 1px 5px;
}

.level-badge-macro         { background: #eff6ff; border-color: #bfdbfe; color: #1d4ed8; }
.level-badge-industry      { background: #f0fdf4; border-color: #bbf7d0; color: #15803d; }
.level-badge-company       { background: #fdf4ff; border-color: #e9d5ff; color: #7e22ce; }
.level-badge-idiosyncratic { background: #fff7ed; border-color: #fed7aa; color: #c2410c; }

/* ── Sub-themes noise note ── */
.sub-themes-noise-note {
  font-family: var(--font-mono);
  font-size: 0.62rem;
  color: #bbb;
  font-weight: 400;
  text-transform: none;
  letter-spacing: 0;
  margin-left: 6px;
}

/* ── Relevance bar ── */
.relevance-bar-wrap {
  display: flex;
  align-items: center;
  gap: 5px;
  flex: 1;
  min-width: 60px;
  max-width: 90px;
}

.relevance-bar {
  flex: 1;
  height: 4px;
  background: #eee;
  border-radius: 2px;
  overflow: hidden;
}

.relevance-fill {
  height: 100%;
  border-radius: 2px;
  transition: width 0.5s ease;
  background: var(--accent);
}

.state-fill-emerging  { background: #10b981; }
.state-fill-mature    { background: #3b82f6; }
.state-fill-declining { background: #f59e0b; }
.state-fill-dormant   { background: #9ca3af; }
.state-fill-unknown   { background: var(--accent, #1a56db); }

.relevance-score {
  font-family: var(--font-mono);
  font-size: 0.68rem;
  color: #888;
  flex-shrink: 0;
}

.last-evidence {
  font-family: var(--font-mono);
  font-size: 0.68rem;
  color: #aaa;
  white-space: nowrap;
}

/* ── Build hierarchy button ── */
.build-hierarchy-btn {
  background: transparent;
  border: 1px dashed #ccc;
  color: #888;
  padding: 8px 14px;
  font-family: var(--font-mono);
  font-size: 0.75rem;
  cursor: pointer;
  transition: all 0.15s;
  display: flex;
  align-items: center;
  gap: 6px;
  white-space: nowrap;
}

.build-hierarchy-btn:hover:not(:disabled) {
  border-color: var(--accent);
  color: var(--accent);
  background: #f0f4ff;
}

.build-hierarchy-btn:disabled {
  opacity: 0.6;
  cursor: not-allowed;
}

.build-spinner {
  display: inline-block;
  width: 12px;
  height: 12px;
  border: 2px solid #ddd;
  border-top-color: var(--accent);
  border-radius: 50%;
  animation: spin 0.8s linear infinite;
}

.build-error-msg {
  background: #fee2e2;
  color: #991b1b;
  border: 1px solid #fca5a5;
  padding: 10px 16px;
  font-family: var(--font-mono);
  font-size: 0.8rem;
  margin-bottom: 20px;
}

/* ── No results ── */
.no-results {
  text-align: center;
  color: #999;
  font-size: 0.9rem;
  padding: 60px 20px;
  border: 1px dashed var(--border);
  font-family: var(--font-mono);
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  flex-wrap: wrap;
}

.inline-link-btn {
  background: none;
  border: none;
  color: var(--accent);
  font-family: var(--font-mono);
  font-size: 0.9rem;
  cursor: pointer;
  text-decoration: underline;
  padding: 0;
}

/* ── Cards grid ── */
.cards-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(340px, 1fr));
  gap: 20px;
}

/* ── Theme card (shared) ── */
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

/* Main theme card — slightly bolder visual weight */
.main-theme-card {
  border-width: 1.5px;
}

.main-theme-card.expanded {
  border-color: var(--accent);
  box-shadow: 0 4px 24px rgba(26, 86, 219, 0.1);
  transform: none;
  grid-column: span 1; /* don't span; sub-themes panel flows below */
}

/* Card top row */
.card-top {
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.card-top-right {
  display: flex;
  align-items: center;
  gap: 8px;
}

.card-id {
  font-family: var(--font-mono);
  font-size: 0.72rem;
  color: #bbb;
}

.card-sub-count {
  font-family: var(--font-mono);
  font-size: 0.72rem;
  color: #aaa;
  background: #f5f5f5;
  border: 1px solid #eee;
  padding: 2px 8px;
  border-radius: 10px;
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

/* Sub-theme pills in footer */
.sub-theme-pills {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
  flex: 1;
}

.sub-pill {
  background: #eef2ff;
  color: #3730a3;
  border: 1px solid #c7d2fe;
  padding: 2px 8px;
  font-size: 0.72rem;
  border-radius: 2px;
  white-space: nowrap;
  max-width: 120px;
  overflow: hidden;
  text-overflow: ellipsis;
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

/* ── Sub-themes panel (expanded) ── */
.sub-themes-panel {
  border-top: 1px solid #eee;
  padding-top: 14px;
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.sub-themes-header {
  font-family: var(--font-mono);
  font-size: 0.68rem;
  text-transform: uppercase;
  letter-spacing: 0.5px;
  color: #bbb;
  margin-bottom: 4px;
}

.sub-themes-list {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.sub-theme-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 10px 12px;
  border: 1px solid #f0f0f0;
  background: #fafafa;
  cursor: pointer;
  transition: background 0.12s, border-color 0.12s;
  gap: 12px;
}

.sub-theme-row:hover {
  background: #eef2ff;
  border-color: #c7d2fe;
}

.sub-theme-info {
  display: flex;
  flex-direction: column;
  gap: 3px;
  min-width: 0;
}

.sub-theme-name-row {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
}

.sub-theme-name {
  font-size: 0.88rem;
  font-weight: 600;
  color: var(--black);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.sub-theme-meta {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
}

.sub-meta-item {
  font-family: var(--font-mono);
  font-size: 0.7rem;
  color: #999;
}

.sub-meta-date {
  color: #bbb;
}

.sub-theme-summary {
  font-size: 0.76rem;
  color: var(--gray-text);
  line-height: 1.45;
  display: -webkit-box;
  -webkit-line-clamp: 1;
  -webkit-box-orient: vertical;
  overflow: hidden;
}

.sub-theme-arrow {
  color: #ccc;
  font-size: 0.9rem;
  flex-shrink: 0;
  transition: color 0.12s;
}

.sub-theme-row:hover .sub-theme-arrow {
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
