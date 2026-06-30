<template>
  <div class="page-view">
    <RunNav :runId="runId" />
    <div class="content-area">
      <div class="page-body">
        <div class="page-header">
          <h1 class="page-title">{{ t('panel.title') }}</h1>
          <button class="refresh-btn" @click="loadData" :disabled="loading">
            {{ loading ? t('common.loading') : t('panel.refresh') }}
          </button>
        </div>

        <div v-if="loading" class="loading-msg">{{ t('panel.loading') }}</div>
        <div v-else-if="!hasAnyPanel" class="empty-msg">{{ t('panel.noData') }}</div>

        <div v-else>
          <p class="page-subtitle">{{ t('panel.subtitle') }}</p>

          <!-- ─── 1. THEME EMERGENCE TIMELINE ─────────────────────────── -->
          <section class="panel-section">
            <div class="section-header">
              <h2 class="section-title">{{ t('panel.lineageTitle') }}</h2>
              <span class="section-desc">{{ t('panel.lineageDesc') }}</span>
            </div>
            <div v-if="lineageError" class="error-msg">{{ lineageError }}</div>
            <div v-else-if="!lineageFamilies.length" class="empty-msg small">
              {{ t('panel.lineageEmpty') }}
            </div>
            <div v-else>
              <div class="legend">
                <span v-for="ev in lifecycleStates" :key="ev" class="legend-item">
                  <span class="legend-swatch" :style="{ background: stateColor(ev) }"></span>
                  {{ t('panel.state.' + ev) }}
                </span>
              </div>
              <div class="chart-scroll">
                <svg ref="lineageSvg" class="d3-chart"></svg>
              </div>
            </div>
          </section>

          <!-- ─── 2. COMPANY EXPOSURE TRAJECTORIES ────────────────────── -->
          <section class="panel-section">
            <div class="section-header">
              <h2 class="section-title">{{ t('panel.trajTitle') }}</h2>
              <span class="section-desc">{{ t('panel.trajDesc') }}</span>
            </div>
            <div v-if="trajError" class="error-msg">{{ trajError }}</div>
            <div v-else-if="!trajRows.length" class="empty-msg small">
              {{ t('panel.trajEmpty') }}
            </div>
            <div v-else>
              <div class="controls">
                <label class="control-label">{{ t('panel.groupBy') }}</label>
                <select v-model="trajMode" class="control-select">
                  <option value="company">{{ t('panel.byCompany') }}</option>
                  <option value="theme">{{ t('panel.byTheme') }}</option>
                </select>
                <label class="control-label">
                  {{ trajMode === 'company' ? t('panel.company') : t('panel.themeFamily') }}
                </label>
                <select v-model="trajSelected" class="control-select wide">
                  <option v-for="o in trajOptions" :key="o.value" :value="o.value">
                    {{ o.label }}
                  </option>
                </select>
              </div>
              <div class="legend" v-if="trajSeries.length">
                <span v-for="s in trajSeries" :key="s.key" class="legend-item">
                  <span class="legend-swatch" :style="{ background: s.color }"></span>
                  {{ s.label }}
                </span>
              </div>
              <div class="chart-scroll">
                <svg ref="trajSvg" class="d3-chart"></svg>
              </div>
            </div>
          </section>

          <!-- ─── 3. PER-POINT VALIDATION PANEL ───────────────────────── -->
          <section class="panel-section">
            <div class="section-header">
              <h2 class="section-title">{{ t('panel.valTitle') }}</h2>
              <span class="section-desc">{{ t('panel.valDesc') }}</span>
            </div>
            <div v-if="valError" class="error-msg">{{ valError }}</div>
            <div v-else-if="!validation" class="empty-msg small">
              {{ t('panel.valEmpty') }}
            </div>
            <div v-else>
              <div class="claim-row">
                <span class="claim-badge" :class="validation.claim_supported ? 'claim-yes' : 'claim-no'">
                  {{ validation.claim_supported ? t('panel.claimSupported') : t('panel.claimIllustrative') }}
                </span>
                <span class="claim-stat">
                  {{ t('panel.meanExcess') }}: <b>{{ fmtPct(validation.mean_excess) }}</b>
                </span>
                <span class="claim-stat">
                  {{ t('panel.hitRate') }}: <b>{{ fmtPct(validation.hit_rate) }}</b>
                </span>
                <span class="claim-stat">
                  {{ t('panel.nPoints') }}: <b>{{ validation.n_points }}</b> /
                  {{ validation.min_points_for_claim }}
                </span>
                <span class="claim-stat">
                  {{ t('panel.window') }}: <b>{{ validation.forward_window }}</b> ·
                  {{ t('panel.baseline') }}: <b>{{ validation.baseline }}</b>
                </span>
              </div>
              <div v-if="validation.illustrative" class="caveat-banner">
                {{ t('panel.illustrativeCaveat') }}
              </div>
              <div class="chart-scroll">
                <svg ref="valSvg" class="d3-chart"></svg>
              </div>
              <div v-if="skippedPoints.length" class="skipped-block">
                <div class="skipped-title">{{ t('panel.skippedPoints') }}</div>
                <table class="data-table">
                  <thead>
                    <tr>
                      <th>{{ t('panel.asOf') }}</th>
                      <th>{{ t('panel.reason') }}</th>
                    </tr>
                  </thead>
                  <tbody>
                    <tr v-for="p in skippedPoints" :key="p.as_of">
                      <td>{{ p.as_of }}</td>
                      <td class="reason-cell">{{ p.skipped_reason || '—' }}</td>
                    </tr>
                  </tbody>
                </table>
              </div>
            </div>
          </section>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, watch, nextTick } from 'vue'
import { useI18n } from 'vue-i18n'
import * as d3 from 'd3'
import RunNav from '../components/RunNav.vue'
import {
  getPanelLineage,
  getPanelTrajectories,
  getPanelValidation,
} from '../api/panel.js'

const props = defineProps({ runId: String })
const { t } = useI18n()

const loading = ref(false)
const lineage = ref(null)
const trajRows = ref([])
const validation = ref(null)
const lineageError = ref('')
const trajError = ref('')
const valError = ref('')

const lineageSvg = ref(null)
const trajSvg = ref(null)
const valSvg = ref(null)

const lifecycleStates = ['emerged', 'persisted', 'split', 'merged', 'revived', 'dormant', 'absent']
const STATE_COLORS = {
  emerged: '#10B981',
  persisted: '#3B82F6',
  split: '#8B5CF6',
  merged: '#F59E0B',
  revived: '#14B8A6',
  dormant: '#9CA3AF',
  absent: '#E5E7EB',
}
const stateColor = (s) => STATE_COLORS[s] || '#CCC'
const PALETTE = d3.schemeTableau10

const hasAnyPanel = computed(
  () => lineageFamilies.value.length || trajRows.value.length || validation.value
)

// ─── Lineage data ─────────────────────────────────────────────────────────
const lineageFamilies = computed(() => lineage.value?.families || [])
const lineagePoints = computed(() => {
  if (lineage.value?.points?.length) return lineage.value.points
  const pts = new Set()
  for (const f of lineageFamilies.value) {
    Object.keys(f.states_by_point || {}).forEach((p) => pts.add(p))
  }
  return [...pts].sort()
})

// ─── Trajectory selection ───────────────────────────────────────────────────
const trajMode = ref('company')
const trajSelected = ref('')

const trajPoints = computed(() => {
  const pts = new Set(trajRows.value.map((r) => r.as_of_date))
  return [...pts].sort()
})

const trajOptions = computed(() => {
  const map = new Map()
  for (const r of trajRows.value) {
    if (trajMode.value === 'company') {
      const v = r.company_id
      if (v && !map.has(v)) map.set(v, r.ticker || r.company_id)
    } else {
      const v = r.theme_family_id
      if (v && !map.has(v)) map.set(v, r.theme_family_id)
    }
  }
  return [...map.entries()]
    .map(([value, label]) => ({ value, label }))
    .sort((a, b) => a.label.localeCompare(b.label))
})

watch(trajOptions, (opts) => {
  if (!opts.find((o) => o.value === trajSelected.value)) {
    trajSelected.value = opts.length ? opts[0].value : ''
  }
})
watch(trajMode, () => {
  trajSelected.value = trajOptions.value.length ? trajOptions.value[0].value : ''
})

// One line per "other dimension": filter by the selected entity, group lines by
// the complementary dimension (company->theme families; theme->companies).
const trajSeries = computed(() => {
  if (!trajSelected.value) return []
  const filterKey = trajMode.value === 'company' ? 'company_id' : 'theme_family_id'
  const groupKey = trajMode.value === 'company' ? 'theme_family_id' : 'company_id'
  const labelOf = (r) =>
    trajMode.value === 'company' ? r.theme_family_id : (r.ticker || r.company_id)
  const rows = trajRows.value.filter((r) => r[filterKey] === trajSelected.value)
  const groups = new Map()
  for (const r of rows) {
    const g = r[groupKey]
    if (!groups.has(g)) groups.set(g, { key: g, label: labelOf(r), byPoint: new Map() })
    // aggregate (sum) duplicate community rows at the same point
    const m = groups.get(g).byPoint
    m.set(r.as_of_date, (m.get(r.as_of_date) || 0) + (Number(r.exposure_score) || 0))
  }
  const series = [...groups.values()].sort((a, b) => String(a.label).localeCompare(String(b.label)))
  series.forEach((s, i) => {
    s.color = PALETTE[i % PALETTE.length]
    s.points = trajPoints.value.map((p) => ({ as_of: p, value: s.byPoint.has(p) ? s.byPoint.get(p) : null }))
  })
  return series
})

// ─── Validation ─────────────────────────────────────────────────────────────
const valPoints = computed(() => validation.value?.points || [])
const skippedPoints = computed(() => valPoints.value.filter((p) => !p.covered))

const fmtPct = (v) => (v === null || v === undefined ? '—' : (v * 100).toFixed(2) + '%')

// ─── d3 renderers ────────────────────────────────────────────────────────────
function renderLineage() {
  const svg = d3.select(lineageSvg.value)
  svg.selectAll('*').remove()
  const families = lineageFamilies.value
  const points = lineagePoints.value
  if (!families.length || !points.length) return

  const margin = { top: 28, right: 16, bottom: 16, left: 200 }
  const cell = 26
  const innerW = Math.max(points.length * 80, 240)
  const innerH = families.length * cell
  const width = innerW + margin.left + margin.right
  const height = innerH + margin.top + margin.bottom
  svg.attr('width', width).attr('height', height)
  const g = svg.append('g').attr('transform', `translate(${margin.left},${margin.top})`)

  const x = d3.scalePoint().domain(points).range([0, innerW]).padding(0.5)
  const step = points.length > 1 ? x(points[1]) - x(points[0]) : innerW

  // x labels (as_of points)
  g.selectAll('text.colhead')
    .data(points)
    .join('text')
    .attr('class', 'colhead')
    .attr('x', (d) => x(d))
    .attr('y', -10)
    .attr('text-anchor', 'middle')
    .attr('font-size', 10)
    .attr('font-family', 'monospace')
    .attr('fill', '#666')
    .text((d) => d)

  families.forEach((fam, row) => {
    const yTop = row * cell
    // family label
    g.append('text')
      .attr('x', -margin.left + 4)
      .attr('y', yTop + cell / 2 + 3)
      .attr('font-size', 11)
      .attr('fill', '#333')
      .text((fam.theme_name || fam.theme_family_id).slice(0, 30))

    // connecting baseline
    g.append('line')
      .attr('x1', 0).attr('x2', innerW)
      .attr('y1', yTop + cell / 2).attr('y2', yTop + cell / 2)
      .attr('stroke', '#F0F0F0')

    points.forEach((p) => {
      const state = (fam.states_by_point || {})[p] || 'absent'
      if (state === 'absent') return
      const cx = x(p)
      g.append('rect')
        .attr('x', cx - Math.min(step, 60) / 2 + 4)
        .attr('y', yTop + 4)
        .attr('width', Math.min(step, 60) - 8)
        .attr('height', cell - 8)
        .attr('rx', 3)
        .attr('fill', stateColor(state))
        .append('title')
        .text(`${fam.theme_name || fam.theme_family_id} @ ${p}: ${state}`)
    })
  })
}

function renderTrajectories() {
  const svg = d3.select(trajSvg.value)
  svg.selectAll('*').remove()
  const series = trajSeries.value
  const points = trajPoints.value
  if (!series.length || !points.length) return

  const margin = { top: 16, right: 24, bottom: 40, left: 56 }
  const innerW = Math.max(points.length * 90, 320)
  const innerH = 280
  svg.attr('width', innerW + margin.left + margin.right)
    .attr('height', innerH + margin.top + margin.bottom)
  const g = svg.append('g').attr('transform', `translate(${margin.left},${margin.top})`)

  const x = d3.scalePoint().domain(points).range([0, innerW]).padding(0.5)
  const maxY = d3.max(series, (s) => d3.max(s.points, (p) => p.value || 0)) || 1
  const y = d3.scaleLinear().domain([0, maxY]).nice().range([innerH, 0])

  g.append('g').attr('transform', `translate(0,${innerH})`)
    .call(d3.axisBottom(x))
    .selectAll('text').attr('font-size', 10).attr('font-family', 'monospace')
  g.append('g').call(d3.axisLeft(y).ticks(5))
    .selectAll('text').attr('font-size', 10).attr('font-family', 'monospace')

  const line = d3.line()
    .defined((d) => d.value !== null)
    .x((d) => x(d.as_of))
    .y((d) => y(d.value))

  series.forEach((s) => {
    g.append('path')
      .datum(s.points)
      .attr('fill', 'none')
      .attr('stroke', s.color)
      .attr('stroke-width', 2)
      .attr('d', line)
    g.selectAll(null)
      .data(s.points.filter((p) => p.value !== null))
      .join('circle')
      .attr('cx', (d) => x(d.as_of))
      .attr('cy', (d) => y(d.value))
      .attr('r', 3)
      .attr('fill', s.color)
      .append('title')
      .text((d) => `${s.label} @ ${d.as_of}: ${d.value.toFixed(4)}`)
  })
}

function renderValidation() {
  const svg = d3.select(valSvg.value)
  svg.selectAll('*').remove()
  const pts = valPoints.value
  if (!pts.length) return

  const margin = { top: 16, right: 24, bottom: 44, left: 56 }
  const innerW = Math.max(pts.length * 80, 320)
  const innerH = 240
  svg.attr('width', innerW + margin.left + margin.right)
    .attr('height', innerH + margin.top + margin.bottom)
  const g = svg.append('g').attr('transform', `translate(${margin.left},${margin.top})`)

  const x = d3.scaleBand().domain(pts.map((p) => p.as_of)).range([0, innerW]).padding(0.3)
  const vals = pts.map((p) => p.excess).filter((v) => v !== null && v !== undefined)
  const ext = d3.extent(vals.length ? vals : [0])
  const y = d3.scaleLinear().domain([Math.min(0, ext[0]), Math.max(0, ext[1])]).nice().range([innerH, 0])

  g.append('g').attr('transform', `translate(0,${innerH})`)
    .call(d3.axisBottom(x))
    .selectAll('text').attr('font-size', 10).attr('font-family', 'monospace')
    .attr('transform', 'rotate(-25)').attr('text-anchor', 'end')
  g.append('g').call(d3.axisLeft(y).ticks(5).tickFormat((d) => (d * 100).toFixed(0) + '%'))
    .selectAll('text').attr('font-size', 10).attr('font-family', 'monospace')
  g.append('line')
    .attr('x1', 0).attr('x2', innerW).attr('y1', y(0)).attr('y2', y(0))
    .attr('stroke', '#999').attr('stroke-dasharray', '3,3')

  pts.forEach((p) => {
    if (!p.covered || p.excess === null || p.excess === undefined) {
      // skipped marker
      g.append('text')
        .attr('x', x(p.as_of) + x.bandwidth() / 2)
        .attr('y', y(0) - 4)
        .attr('text-anchor', 'middle')
        .attr('font-size', 16)
        .attr('fill', '#CBD5E1')
        .text('×')
        .append('title').text(p.skipped_reason || 'skipped')
      return
    }
    const yv = y(p.excess)
    g.append('rect')
      .attr('x', x(p.as_of))
      .attr('width', x.bandwidth())
      .attr('y', Math.min(yv, y(0)))
      .attr('height', Math.abs(yv - y(0)))
      .attr('fill', p.excess >= 0 ? '#10B981' : '#EF4444')
      .append('title')
      .text(`${p.as_of}: excess ${(p.excess * 100).toFixed(2)}% (basket ${fmtPct(p.basket_return)} vs baseline ${fmtPct(p.baseline_return)})`)
  })
}

function renderAll() {
  nextTick(() => {
    renderLineage()
    renderTrajectories()
    renderValidation()
  })
}

watch([lineage], renderAll)
watch([trajSeries], () => nextTick(renderTrajectories))
watch([validation], () => nextTick(renderValidation))

// ─── Data loading ────────────────────────────────────────────────────────────
async function loadData() {
  loading.value = true
  lineageError.value = trajError.value = valError.value = ''
  const [lin, traj, val] = await Promise.allSettled([
    getPanelLineage(props.runId),
    getPanelTrajectories(props.runId),
    getPanelValidation(props.runId),
  ])

  lineage.value = lin.status === 'fulfilled' ? lin.value : null
  if (lin.status === 'rejected' && lin.reason?.response?.status !== 404) {
    lineageError.value = lin.reason?.response?.data?.detail || lin.reason?.message || 'Failed to load lineage'
  }

  trajRows.value = traj.status === 'fulfilled' ? traj.value : []
  if (traj.status === 'rejected' && traj.reason?.response?.status !== 404) {
    trajError.value = traj.reason?.response?.data?.detail || traj.reason?.message || 'Failed to load trajectories'
  }

  validation.value = val.status === 'fulfilled' ? val.value : null
  if (val.status === 'rejected' && val.reason?.response?.status !== 404) {
    valError.value = val.reason?.response?.data?.detail || val.reason?.message || 'Failed to load validation'
  }

  loading.value = false
  renderAll()
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

.content-area { flex: 1; overflow-y: auto; }

.page-body { max-width: 1200px; margin: 0 auto; padding: 32px 24px; }

.page-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 8px;
}

.page-title { font-size: 1.5rem; font-weight: 700; }
.page-subtitle { color: #888; font-size: 13px; margin-bottom: 24px; }

.refresh-btn {
  background: var(--black);
  color: var(--white);
  border: none;
  padding: 8px 16px;
  font-family: var(--font-mono);
  font-size: 12px;
  font-weight: 700;
  cursor: pointer;
  border-radius: 3px;
  transition: opacity 0.2s;
}
.refresh-btn:hover:not(:disabled) { opacity: 0.8; }
.refresh-btn:disabled { background: #CCC; cursor: not-allowed; }

.panel-section {
  background: #FFF;
  border: 1px solid #E5E5E5;
  border-radius: 8px;
  padding: 20px 24px;
  margin-bottom: 24px;
}

.section-header { margin-bottom: 16px; }
.section-title { font-size: 1.1rem; font-weight: 700; }
.section-desc { display: block; font-size: 12px; color: #888; margin-top: 2px; }

.controls {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 14px;
  flex-wrap: wrap;
}
.control-label {
  font-size: 10px;
  text-transform: uppercase;
  color: #888;
  font-family: var(--font-mono);
}
.control-select {
  font-family: var(--font-mono);
  font-size: 12px;
  padding: 5px 8px;
  border: 1px solid #D1D5DB;
  border-radius: 4px;
  background: #FFF;
}
.control-select.wide { min-width: 220px; }

.legend {
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
  margin-bottom: 12px;
}
.legend-item {
  display: flex;
  align-items: center;
  gap: 5px;
  font-size: 11px;
  font-family: var(--font-mono);
  color: #555;
}
.legend-swatch {
  width: 12px; height: 12px; border-radius: 3px; display: inline-block;
}

.chart-scroll { overflow-x: auto; }
.d3-chart { display: block; }

.claim-row {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 16px;
  margin-bottom: 14px;
}
.claim-badge {
  padding: 4px 12px;
  border-radius: 4px;
  font-size: 11px;
  font-weight: 700;
  font-family: var(--font-mono);
  text-transform: uppercase;
}
.claim-yes { background: #ECFDF5; color: #065F46; }
.claim-no { background: #FEF9C3; color: #92400E; }
.claim-stat { font-size: 12px; color: #444; }

.caveat-banner {
  padding: 10px 14px;
  background: #FFF7ED;
  border: 1px solid #FED7AA;
  border-radius: 6px;
  font-size: 12px;
  color: #9A3412;
  margin-bottom: 16px;
  line-height: 1.5;
}

.skipped-block { margin-top: 18px; }
.skipped-title {
  font-size: 11px;
  text-transform: uppercase;
  color: #888;
  font-family: var(--font-mono);
  margin-bottom: 8px;
}

.data-table { width: 100%; border-collapse: collapse; font-size: 12px; }
.data-table th {
  background: #F8F9FA;
  padding: 8px 12px;
  text-align: left;
  font-size: 10px;
  font-family: var(--font-mono);
  text-transform: uppercase;
  color: #666;
  border-bottom: 1px solid #E5E5E5;
}
.data-table td {
  padding: 8px 12px;
  border-bottom: 1px solid #F5F5F5;
  color: #333;
}
.reason-cell { font-family: var(--font-mono); color: #92400E; }

.loading-msg, .empty-msg {
  padding: 40px;
  text-align: center;
  color: #999;
  font-size: 14px;
}
.empty-msg.small { padding: 20px; font-size: 13px; }

.error-msg {
  padding: 14px;
  color: #ef4444;
  background: #FEE2E2;
  border-radius: 6px;
  font-size: 12px;
  font-family: var(--font-mono);
  margin-bottom: 12px;
}
</style>
