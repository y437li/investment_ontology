<template>
  <div class="mtv">
    <nav class="mtv-nav">
      <router-link to="/" class="back">← Themes</router-link>
      <span class="mtv-title">{{ themeName }}</span>
      <span class="mtv-sub">{{ activeCommunityIds.length }} sub-themes</span>
    </nav>

    <!-- Filter the graph by main theme(s) -->
    <div class="theme-filter" v-if="allMainThemes.length">
      <span class="tf-label">Graph:</span>
      <button
        v-for="mt in allMainThemes"
        :key="mt.name"
        class="tf-chip"
        :class="{ active: selectedNames.has(mt.name) }"
        @click="toggleTheme(mt.name)"
      >{{ mt.name }}</button>
    </div>

    <div v-if="loading" class="mtv-state">
      <div class="spinner"></div>
      <p>Building the story… (first load can take ~20s)</p>
    </div>
    <div v-else-if="error" class="mtv-state error">
      <p>{{ error }}</p>
      <button @click="load">Retry</button>
    </div>

    <div v-else class="mtv-body">
      <!-- LEFT: layered graph -->
      <div class="graph-pane">
        <LayeredGraph :nodes="subgraph.nodes" :edges="subgraph.edges" :active-hop="activeHop" @node-click="openNode" />
        <div v-if="selectedNode" class="node-profile">
          <button class="np-close" @click="selectedNode = null">×</button>
          <div class="np-name">{{ selectedNode.name }}</div>
          <div class="np-meta">{{ selectedNode.entity_type }} · {{ selectedNode.level }} · {{ selectedNode.evidence_count }} evidence · degree {{ selectedNode.degree }}</div>
          <p v-if="selectedNode.definition" class="np-def">{{ selectedNode.definition }}</p>
          <div class="np-why">Why it's here:</div>
          <ul>
            <li v-for="(w, i) in (selectedNode.why_present || []).slice(0, 6)" :key="i">
              <em>{{ w.direction }}</em> {{ w.edge_type }} {{ w.other }} — {{ w.explanation }}
            </li>
          </ul>
        </div>
      </div>

      <!-- RIGHT: the one story -->
      <div class="story-pane">
        <div v-if="storyLoading" class="story-loading"><div class="spinner small"></div><span>Composing the story…</span></div>
        <div v-else-if="storyError" class="story-err">{{ storyError }}</div>
        <p v-else class="narrative">{{ story.narrative }}</p>

        <div class="walk-bar" v-if="steps.length">
          <span class="walk-label">Derivation</span>
          <div class="walk-ctrls">
            <button @click="prevStep" :disabled="activeIdx <= 0">‹</button>
            <span class="walk-count">{{ activeIdx >= 0 ? activeIdx + 1 : '–' }} / {{ steps.length }}</span>
            <button @click="nextStep" :disabled="activeIdx >= steps.length - 1">›</button>
            <button class="walk-play" @click="togglePlay">{{ playing ? '⏸' : '▶' }}</button>
            <button class="walk-clear" @click="clearStep" :disabled="activeIdx < 0">clear</button>
          </div>
        </div>

        <ol class="steps">
          <li v-for="(s, i) in steps" :key="i" :class="{ active: i === activeIdx }" @click="setStep(i)">
            <span class="step-num">{{ s.order }}</span>
            <div class="step-body">
              <div class="step-edge">
                <span class="src">{{ s.source }}</span>
                <span class="edge-dot" :style="{ background: edgeColor(s.edge_type) }"></span>
                <span class="etype" :style="{ color: edgeColor(s.edge_type) }">{{ s.edge_type }}</span>
                <span class="arr">→</span>
                <span class="tgt">{{ s.target }}</span>
                <span class="prov" :class="`prov-${s.provenance}`">{{ s.provenance === 'document_stated' ? 'evidence' : 'inferred' }}</span>
              </div>
              <div class="step-claim">{{ s.claim }}</div>
            </div>
          </li>
        </ol>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onBeforeUnmount, watch, nextTick } from 'vue'
import { useRoute } from 'vue-router'
import * as d3 from 'd3'
import { getSubgraph, getMainNarrative, getNodeProfile, getThemeHierarchy } from '../api/themes.js'
import LayeredGraph from '../components/LayeredGraph.vue'

const route = useRoute()
const runId = route.params.runId
const communityIds = computed(() => String(route.query.communities || '').split(',').filter(Boolean))

// All main themes (filter chips) + the currently selected set
const allMainThemes = ref([])
const selectedNames = ref(new Set())
const nameToIds = computed(() => {
  const m = {}
  allMainThemes.value.forEach((mt) => { m[mt.name] = mt.sub_theme_ids || [] })
  return m
})
const activeCommunityIds = computed(() => {
  const ids = new Set()
  selectedNames.value.forEach((n) => (nameToIds.value[n] || []).forEach((c) => ids.add(c)))
  const arr = [...ids]
  return arr.length ? arr : communityIds.value
})
const themeName = computed(() => {
  const sel = [...selectedNames.value]
  if (sel.length === 1) return sel[0]
  if (sel.length > 1) return `${sel.length} themes`
  return route.query.name || 'Main theme'
})
const toggleTheme = (name) => {
  const next = new Set(selectedNames.value)
  if (next.has(name)) { if (next.size > 1) next.delete(name) } else next.add(name)
  selectedNames.value = next
}

const LEVELS = ['macro', 'industry', 'company', 'idiosyncratic', 'contextual']
const LEVEL_COLORS = { macro: '#7c3aed', industry: '#2563eb', company: '#16a34a', idiosyncratic: '#ea580c', contextual: '#9ca3af', evidence: '#d1d5db' }
const LEVEL_Y = { macro: 0.12, industry: 0.33, company: 0.55, idiosyncratic: 0.77, contextual: 0.92, evidence: 0.97 }
const EDGE_COLORS = { benefits: '#16a34a', hurts: '#dc2626', causes: '#ea580c', exposed_to: '#2563eb', sensitive_to: '#7c3aed', located_in: '#9ca3af' }
const edgeColor = (t) => EDGE_COLORS[t] || '#cbd5e1'
const levelOf = (n) => (n && LEVEL_COLORS[n.level] ? n.level : 'contextual')

const loading = ref(true)
const error = ref('')
const storyLoading = ref(false)
const storyError = ref('')
const subgraph = ref({ nodes: [], edges: [] })
const story = ref({ narrative: '', reasoning_steps: [] })
const steps = computed(() => (story.value.reasoning_steps || []).slice().sort((a, b) => (a.order || 0) - (b.order || 0)))
const selectedNode = ref(null)
const activeIdx = ref(-1)
// Walk → drive the shared LayeredGraph (replaces the inline highlight(idx) d3)
const activeHop = computed(() => {
  const s = steps.value[activeIdx.value]
  return (activeIdx.value >= 0 && s) ? { source_id: s.source_id, target_id: s.target_id } : null
})
const playing = ref(false)
let playTimer = null

const svgEl = ref(null)
let sim = null
let nodeSel = null
let linkSel = null
let labelSel = null

async function load() {
  loading.value = true
  error.value = ''
  const ids = activeCommunityIds.value
  if (!ids.length) { error.value = 'No sub-themes selected.'; loading.value = false; return }
  try {
    subgraph.value = (await getSubgraph(runId, ids)) || { nodes: [], edges: [] }
    loading.value = false
    await nextTick()
    renderGraph()
  } catch (e) {
    error.value = e?.response?.data?.detail || e?.message || 'Failed to load the graph.'
    loading.value = false
    return
  }
  loadStory(ids)   // graph is up; the story streams in separately
}

async function loadStory(ids) {
  storyLoading.value = true
  storyError.value = ''
  story.value = { narrative: '', reasoning_steps: [] }
  activeIdx.value = -1
  try {
    story.value = (await getMainNarrative(runId, ids)) || { narrative: '', reasoning_steps: [] }
  } catch (e) {
    storyError.value = e?.response?.status === 503 ? 'LLM not configured — story unavailable.' : (e?.message || 'Story failed to load.')
  } finally {
    storyLoading.value = false
  }
}

function renderGraph() {
  const el = svgEl.value
  if (!el) return
  const W = el.clientWidth || 700
  const H = el.clientHeight || 560
  const degree = {}
  subgraph.value.edges.forEach((e) => { degree[e.source] = (degree[e.source] || 0) + 1; degree[e.target] = (degree[e.target] || 0) + 1 })
  const nodes = subgraph.value.nodes.map((n) => ({ ...n, deg: degree[n.id] || 0 }))
  const idset = new Set(nodes.map((n) => n.id))
  const links = subgraph.value.edges.filter((e) => idset.has(e.source) && idset.has(e.target)).map((e) => ({ ...e }))

  const svg = d3.select(el)
  svg.selectAll('*').remove()
  svg.attr('viewBox', `0 0 ${W} ${H}`).style('background', '#fbfbfd')
  const g = svg.append('g')
  svg.call(d3.zoom().scaleExtent([0.2, 4]).on('zoom', (ev) => g.attr('transform', ev.transform)))

  // defs: soft node shadow + one arrowhead marker per edge-type colour
  const defs = svg.append('defs')
  defs.append('filter').attr('id', 'nodeShadow').attr('x', '-50%').attr('y', '-50%').attr('width', '200%').attr('height', '200%')
    .append('feDropShadow').attr('dx', 0).attr('dy', 1).attr('stdDeviation', 1.5).attr('flood-color', '#000').attr('flood-opacity', 0.18)
  const edgeTypes = Array.from(new Set(links.map((l) => l.edge_type)))
  edgeTypes.forEach((t) => {
    defs.append('marker').attr('id', `arr-${t}`).attr('viewBox', '0 -5 10 10').attr('refX', 20).attr('refY', 0)
      .attr('markerWidth', 6).attr('markerHeight', 6).attr('orient', 'auto')
      .append('path').attr('d', 'M0,-4L9,0L0,4').attr('fill', edgeColor(t)).attr('opacity', 0.8)
  })

  // faint horizontal level bands + left labels (macro → idiosyncratic)
  const presentLevels = LEVELS.filter((lv) => nodes.some((n) => levelOf(n) === lv))
  const bands = g.append('g')
  presentLevels.forEach((lv) => {
    const y = (LEVEL_Y[lv] ?? 0.9) * H
    bands.append('rect').attr('x', 0).attr('y', y - 36).attr('width', W).attr('height', 72)
      .attr('fill', LEVEL_COLORS[lv]).attr('opacity', 0.04)
    bands.append('text').attr('x', 12).attr('y', y - 20).text(lv.toUpperCase())
      .attr('font-size', 11).attr('font-family', 'monospace').attr('font-weight', 700)
      .attr('fill', LEVEL_COLORS[lv]).attr('opacity', 0.55)
  })

  // adjacency for hover
  const adj = {}
  links.forEach((l) => { (adj[l.source.id || l.source] ||= new Set()).add(l.target.id || l.target); (adj[l.target.id || l.target] ||= new Set()).add(l.source.id || l.source) })

  linkSel = g.append('g').selectAll('line').data(links).join('line')
    .attr('stroke', (d) => edgeColor(d.edge_type)).attr('stroke-width', 1.4).attr('stroke-opacity', 0.55)
    .attr('marker-end', (d) => `url(#arr-${d.edge_type})`)

  const node = g.append('g').selectAll('g').data(nodes).join('g').style('cursor', 'pointer')
    .on('click', (ev, d) => openNode(d))
    .on('mouseover', (ev, d) => { if (activeIdx.value < 0) hoverNode(d, adj) })
    .on('mouseout', () => { if (activeIdx.value < 0) restore() })
    .call(d3.drag()
      .on('start', (ev, d) => { if (!ev.active) sim.alphaTarget(0.2).restart(); d.fx = d.x; d.fy = d.y })
      .on('drag', (ev, d) => { d.fx = ev.x; d.fy = ev.y })
      .on('end', (ev, d) => { if (!ev.active) sim.alphaTarget(0); d.fx = null; d.fy = null }))
  nodeSel = node.append('circle')
    .attr('r', (d) => 5 + Math.min(9, d.deg))
    .attr('fill', (d) => LEVEL_COLORS[levelOf(d)])
    .attr('stroke', '#fff').attr('stroke-width', 1.6)
    .attr('filter', 'url(#nodeShadow)')
  labelSel = node.append('text').text((d) => d.label).attr('x', (d) => 8 + Math.min(9, d.deg)).attr('y', 4)
    .attr('font-size', 10).attr('fill', '#222')
    .attr('stroke', '#fbfbfd').attr('stroke-width', 3).attr('paint-order', 'stroke')
    .attr('opacity', (d) => (d.deg >= 1 ? 1 : 0.55))

  sim = d3.forceSimulation(nodes)
    .force('link', d3.forceLink(links).id((d) => d.id).distance(50).strength(0.35))
    .force('charge', d3.forceManyBody().strength(-140))
    .force('x', d3.forceX(W / 2).strength(0.05))
    .force('y', d3.forceY((d) => (LEVEL_Y[levelOf(d)] ?? 0.9) * H).strength(0.9))
    .force('collide', d3.forceCollide().radius((d) => 14 + Math.min(9, d.deg)))
    .on('tick', () => {
      linkSel.attr('x1', (d) => d.source.x).attr('y1', (d) => d.source.y).attr('x2', (d) => d.target.x).attr('y2', (d) => d.target.y)
      node.attr('transform', (d) => `translate(${d.x},${d.y})`)
    })
}

function hoverNode(d, adj) {
  const hot = adj[d.id] ? new Set([...adj[d.id], d.id]) : new Set([d.id])
  nodeSel.attr('opacity', (n) => (hot.has(n.id) ? 1 : 0.12))
  labelSel.attr('opacity', (n) => (hot.has(n.id) ? 1 : 0.08))
  linkSel.attr('stroke-opacity', (l) => ((l.source.id === d.id || l.target.id === d.id) ? 0.95 : 0.04))
    .attr('stroke-width', (l) => ((l.source.id === d.id || l.target.id === d.id) ? 2.4 : 1.4))
}
function restore() {
  if (!nodeSel) return
  nodeSel.attr('opacity', 1)
  labelSel.attr('opacity', (d) => (d.deg >= 1 ? 1 : 0.55))
  linkSel.attr('stroke-opacity', 0.55).attr('stroke-width', 1.4)
}

async function openNode(d) {
  try {
    selectedNode.value = await getNodeProfile(runId, d.id)
  } catch {
    selectedNode.value = { name: d.label, entity_type: d.entity_type, level: d.level, why_present: [] }
  }
}

// ── derivation walk: highlight the active step's hop on the graph ──
function highlight(idx) {
  if (!nodeSel) return
  if (idx < 0 || idx >= steps.value.length) {
    nodeSel.attr('opacity', 1).attr('stroke', '#fff').attr('stroke-width', 1.5)
    linkSel.attr('stroke-opacity', 0.5).attr('stroke-width', 1.4)
    labelSel.attr('opacity', 1)
    return
  }
  const s = steps.value[idx]
  const hot = new Set([s.source_id, s.target_id])
  nodeSel.attr('opacity', (d) => (hot.has(d.id) ? 1 : 0.15))
    .attr('stroke', (d) => (d.id === s.source_id ? '#f59e0b' : d.id === s.target_id ? '#16a34a' : '#fff'))
    .attr('stroke-width', (d) => (hot.has(d.id) ? 3 : 1.5))
  labelSel.attr('opacity', (d) => (hot.has(d.id) ? 1 : 0.15))
  linkSel.attr('stroke-opacity', (d) => ((d.source.id === s.source_id && d.target.id === s.target_id) || (d.source.id === s.target_id && d.target.id === s.source_id) ? 1 : 0.06))
    .attr('stroke-width', (d) => ((d.source.id === s.source_id && d.target.id === s.target_id) || (d.source.id === s.target_id && d.target.id === s.source_id) ? 3.5 : 1.4))
}

function setStep(i) { activeIdx.value = i }
function prevStep() { if (activeIdx.value > 0) activeIdx.value-- }
function nextStep() { if (activeIdx.value < steps.value.length - 1) activeIdx.value++ }
function clearStep() { activeIdx.value = -1; stopPlay() }
function togglePlay() { playing.value ? stopPlay() : startPlay() }
function startPlay() {
  if (!steps.value.length) return
  playing.value = true
  if (activeIdx.value >= steps.value.length - 1) activeIdx.value = -1
  playTimer = setInterval(() => {
    if (activeIdx.value >= steps.value.length - 1) { stopPlay(); return }
    activeIdx.value++
  }, 1400)
}
function stopPlay() { playing.value = false; if (playTimer) { clearInterval(playTimer); playTimer = null } }

async function init() {
  try {
    const h = await getThemeHierarchy(runId)
    allMainThemes.value = h?.main_themes || []
  } catch { allMainThemes.value = [] }
  const clicked = route.query.name
  if (clicked && allMainThemes.value.some((m) => m.name === clicked)) selectedNames.value = new Set([clicked])
  else if (allMainThemes.value.length) selectedNames.value = new Set([allMainThemes.value[0].name])
  await load()
}

watch(activeIdx, (i) => highlight(i))
watch(selectedNames, () => { activeIdx.value = -1; selectedNode.value = null; load() })
onMounted(init)
onBeforeUnmount(() => { stopPlay(); if (sim) sim.stop() })
</script>

<style scoped>
.mtv { min-height: 100vh; background: #fff; display: flex; flex-direction: column; }
.mtv-nav { height: 52px; display: flex; align-items: center; gap: 16px; padding: 0 24px; border-bottom: 1px solid #eee; }
.back { color: #2563eb; text-decoration: none; font-size: 0.85rem; }
.mtv-title { font-weight: 700; font-size: 1.05rem; }
.mtv-sub { color: #999; font-size: 0.78rem; font-family: monospace; }
.theme-filter { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; padding: 8px 24px; border-bottom: 1px solid #eee; background: #fafafa; }
.story-loading { display: flex; align-items: center; gap: 10px; color: #888; font-size: 0.85rem; padding: 20px 0; }
.spinner.small { width: 18px; height: 18px; border-width: 2px; }
.story-err { color: #c0392b; font-size: 0.85rem; padding: 14px; background: #fdf0ef; border-radius: 4px; }
.tf-label { font-family: monospace; font-size: 0.72rem; color: #888; text-transform: uppercase; }
.tf-chip { border: 1px solid #ddd; background: #fff; color: #777; font-size: 0.74rem; padding: 4px 10px; border-radius: 14px; cursor: pointer; transition: all 0.15s; max-width: 220px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.tf-chip:hover { border-color: #999; }
.tf-chip.active { background: #111; color: #fff; border-color: #111; }
.mtv-state { display: flex; flex-direction: column; align-items: center; gap: 14px; padding: 80px; color: #777; }
.mtv-state.error { color: #c0392b; }
.spinner { width: 34px; height: 34px; border: 3px solid #eee; border-top-color: #2563eb; border-radius: 50%; animation: spin 0.8s linear infinite; }
@keyframes spin { to { transform: rotate(360deg); } }
.mtv-body { display: grid; grid-template-columns: 1.2fr 1fr; flex: 1; min-height: 0; }
.graph-pane { position: relative; border-right: 1px solid #eee; }
.graph-svg { width: 100%; height: calc(100vh - 52px); display: block; }
.legend { position: absolute; top: 10px; left: 12px; display: flex; gap: 12px; flex-wrap: wrap; background: rgba(255,255,255,0.9); padding: 6px 10px; border: 1px solid #eee; border-radius: 4px; font-size: 0.7rem; font-family: monospace; }
.legend-item { display: flex; align-items: center; gap: 4px; }
.legend-dot { width: 9px; height: 9px; border-radius: 50%; display: inline-block; }
.node-profile { position: absolute; bottom: 12px; left: 12px; right: 12px; max-height: 42%; overflow: auto; background: #fff; border: 1px solid #ddd; border-radius: 6px; padding: 12px 14px; box-shadow: 0 4px 18px rgba(0,0,0,0.1); }
.np-close { position: absolute; top: 6px; right: 8px; border: none; background: none; font-size: 1.1rem; cursor: pointer; color: #999; }
.np-name { font-weight: 700; }
.np-meta { font-size: 0.72rem; color: #888; font-family: monospace; margin: 2px 0 6px; }
.np-def { font-size: 0.82rem; color: #444; }
.np-why { font-size: 0.72rem; color: #999; text-transform: uppercase; margin-top: 6px; }
.node-profile ul { margin: 4px 0 0; padding-left: 16px; font-size: 0.8rem; color: #444; }
.story-pane { padding: 20px 22px; overflow: auto; height: calc(100vh - 52px); }
.narrative { font-size: 0.95rem; line-height: 1.6; color: #1a1a1a; }
.walk-bar { display: flex; align-items: center; justify-content: space-between; margin: 18px 0 10px; border-top: 1px solid #eee; padding-top: 12px; }
.walk-label { font-family: monospace; font-size: 0.78rem; color: #666; text-transform: uppercase; }
.walk-ctrls { display: flex; align-items: center; gap: 6px; }
.walk-ctrls button { border: 1px solid #ddd; background: #fff; cursor: pointer; padding: 2px 9px; border-radius: 4px; }
.walk-count { font-family: monospace; font-size: 0.78rem; min-width: 48px; text-align: center; }
.steps { list-style: none; margin: 0; padding: 0; }
.steps li { display: flex; gap: 10px; padding: 9px 8px; border-radius: 6px; cursor: pointer; border: 1px solid transparent; }
.steps li:hover { background: #f7f7f7; }
.steps li.active { background: #eff6ff; border-color: #bfdbfe; }
.step-num { font-family: monospace; font-weight: 700; color: #999; min-width: 18px; }
.step-edge { display: flex; align-items: center; gap: 5px; flex-wrap: wrap; font-size: 0.84rem; }
.edge-dot { width: 8px; height: 8px; border-radius: 50%; }
.etype { font-style: italic; font-size: 0.76rem; }
.arr { color: #bbb; }
.src, .tgt { font-weight: 600; }
.prov { font-size: 0.6rem; font-family: monospace; font-weight: 700; text-transform: uppercase; padding: 1px 5px; border-radius: 3px; margin-left: 2px; }
.prov-document_stated { background: #ECFDF5; color: #065F46; }
.prov-llm_inferred { background: #FFF7ED; color: #C2410C; }
.step-claim { font-size: 0.8rem; color: #555; margin-top: 2px; }
@media (max-width: 900px) { .mtv-body { grid-template-columns: 1fr; } .graph-svg { height: 60vh; } }
</style>
