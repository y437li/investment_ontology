<template>
  <div class="lg-wrap">
    <div class="lg-filters" v-if="presentLevels.length || presentEdgeTypes.length">
      <div class="lg-row" v-if="presentLevels.length">
        <span class="lg-cap">levels</span>
        <button v-for="lvl in presentLevels" :key="lvl" class="lg-chip" :class="{ off: hiddenLevels.has(lvl) }" @click="toggleLevel(lvl)" :title="hiddenLevels.has(lvl) ? 'show ' + lvl : 'hide ' + lvl">
          <span class="lg-dot" :style="{ background: LEVEL_COLORS[lvl] }"></span>{{ lvl }}
        </button>
      </div>
      <div class="lg-row" v-if="presentEdgeTypes.length">
        <span class="lg-cap">edges</span>
        <button v-for="et in presentEdgeTypes" :key="et" class="lg-chip" :class="{ off: hiddenEdgeTypes.has(et) }" @click="toggleEdge(et)" :title="hiddenEdgeTypes.has(et) ? 'show ' + et : 'hide ' + et">
          <span class="lg-dash" :style="{ background: edgeColor(et) }"></span>{{ et }}
        </button>
      </div>
    </div>
    <svg ref="svgEl" class="lg-svg"></svg>
  </div>
</template>

<script setup>
import { ref, computed, watch, onMounted, onBeforeUnmount, nextTick } from 'vue'
import * as d3 from 'd3'

// Shared upgraded graph: layered by factor level, arrowheads coloured by edge type,
// node shadows + label halos, hover-highlight neighbours, walk-highlight a hop.
const props = defineProps({
  nodes: { type: Array, default: () => [] },        // [{ id, label, entity_type, level }]
  edges: { type: Array, default: () => [] },        // [{ source, target, edge_type }]
  activeHop: { type: Object, default: null },        // { source_id, target_id } to highlight, or null
})
const emit = defineEmits(['node-click'])

const LEVELS = ['macro', 'industry', 'company', 'idiosyncratic', 'contextual']
const LEVEL_COLORS = { macro: '#7c3aed', industry: '#2563eb', company: '#16a34a', idiosyncratic: '#ea580c', contextual: '#9ca3af', evidence: '#d1d5db' }
const LEVEL_Y = { macro: 0.12, industry: 0.33, company: 0.55, idiosyncratic: 0.77, contextual: 0.92, evidence: 0.97 }
const EDGE_COLORS = { benefits: '#16a34a', hurts: '#dc2626', causes: '#ea580c', exposed_to: '#2563eb', sensitive_to: '#7c3aed', located_in: '#9ca3af' }
const edgeColor = (t) => EDGE_COLORS[t] || '#cbd5e1'
// Fallback level from entity_type when a node carries no explicit level (raw graph.json).
const TYPE_LEVEL = { Company: 'company', Sector: 'industry', Commodity: 'macro', MacroIndicator: 'macro', Event: 'idiosyncratic', EconomicConcept: 'contextual', Geography: 'contextual', Document: 'evidence' }
const levelOf = (n) => {
  if (n && LEVEL_COLORS[n.level]) return n.level
  return (n && TYPE_LEVEL[n.entity_type]) || 'contextual'
}

const svgEl = ref(null)
let sim = null, nodeSel = null, linkSel = null, labelSel = null
const presentLevels = computed(() => LEVELS.filter((lv) => props.nodes.some((n) => levelOf(n) === lv)))
const presentEdgeTypes = computed(() => Array.from(new Set(props.edges.map((e) => e.edge_type))).sort())

// In-graph filters (reassign the Set so Vue re-renders)
const hiddenLevels = ref(new Set())
const hiddenEdgeTypes = ref(new Set())
function toggleLevel(lv) { const s = new Set(hiddenLevels.value); s.has(lv) ? s.delete(lv) : s.add(lv); hiddenLevels.value = s }
function toggleEdge(et) { const s = new Set(hiddenEdgeTypes.value); s.has(et) ? s.delete(et) : s.add(et); hiddenEdgeTypes.value = s }

function render() {
  const el = svgEl.value
  if (!el) return
  const W = el.clientWidth || 700
  const H = el.clientHeight || 520
  // apply in-graph filters (hidden levels drop their nodes + incident edges)
  const visNodes = props.nodes.filter((n) => !hiddenLevels.value.has(levelOf(n)))
  const visIds = new Set(visNodes.map((n) => n.id))
  const visEdges = props.edges.filter((e) => !hiddenEdgeTypes.value.has(e.edge_type) && visIds.has(e.source) && visIds.has(e.target))
  const degree = {}
  visEdges.forEach((e) => { degree[e.source] = (degree[e.source] || 0) + 1; degree[e.target] = (degree[e.target] || 0) + 1 })
  const nodes = visNodes.map((n) => ({ ...n, deg: degree[n.id] || 0 }))
  const idset = new Set(nodes.map((n) => n.id))
  const links = visEdges.filter((e) => idset.has(e.source) && idset.has(e.target)).map((e) => ({ ...e }))

  const svg = d3.select(el)
  svg.selectAll('*').remove()
  svg.attr('viewBox', `0 0 ${W} ${H}`).style('background', '#fbfbfd')
  const g = svg.append('g')
  svg.call(d3.zoom().scaleExtent([0.2, 4]).on('zoom', (ev) => g.attr('transform', ev.transform)))

  const defs = svg.append('defs')
  defs.append('filter').attr('id', 'lgShadow').attr('x', '-50%').attr('y', '-50%').attr('width', '200%').attr('height', '200%')
    .append('feDropShadow').attr('dx', 0).attr('dy', 1).attr('stdDeviation', 1.5).attr('flood-color', '#000').attr('flood-opacity', 0.18)
  Array.from(new Set(links.map((l) => l.edge_type))).forEach((t) => {
    defs.append('marker').attr('id', `lgarr-${t}`).attr('viewBox', '0 -5 10 10').attr('refX', 20).attr('refY', 0)
      .attr('markerWidth', 6).attr('markerHeight', 6).attr('orient', 'auto')
      .append('path').attr('d', 'M0,-4L9,0L0,4').attr('fill', edgeColor(t)).attr('opacity', 0.8)
  })

  const bands = g.append('g')
  presentLevels.value.filter((lv) => !hiddenLevels.value.has(lv)).forEach((lv) => {
    const y = (LEVEL_Y[lv] ?? 0.9) * H
    bands.append('rect').attr('x', 0).attr('y', y - 36).attr('width', W).attr('height', 72).attr('fill', LEVEL_COLORS[lv]).attr('opacity', 0.04)
    bands.append('text').attr('x', 12).attr('y', y - 20).text(lv.toUpperCase()).attr('font-size', 11).attr('font-family', 'monospace').attr('font-weight', 700).attr('fill', LEVEL_COLORS[lv]).attr('opacity', 0.55)
  })

  const adj = {}
  links.forEach((l) => { (adj[l.source.id || l.source] ||= new Set()).add(l.target.id || l.target); (adj[l.target.id || l.target] ||= new Set()).add(l.source.id || l.source) })

  linkSel = g.append('g').selectAll('line').data(links).join('line')
    .attr('stroke', (d) => edgeColor(d.edge_type)).attr('stroke-width', 1.4).attr('stroke-opacity', 0.55)
    .attr('marker-end', (d) => `url(#lgarr-${d.edge_type})`)

  const node = g.append('g').selectAll('g').data(nodes).join('g').style('cursor', 'pointer')
    .on('click', (ev, d) => emit('node-click', d))
    .on('mouseover', (ev, d) => { if (!props.activeHop) hoverNode(d, adj) })
    .on('mouseout', () => { if (!props.activeHop) restore() })
    .call(d3.drag()
      .on('start', (ev, d) => { if (!ev.active) sim.alphaTarget(0.2).restart(); d.fx = d.x; d.fy = d.y })
      .on('drag', (ev, d) => { d.fx = ev.x; d.fy = ev.y })
      .on('end', (ev, d) => { if (!ev.active) sim.alphaTarget(0); d.fx = null; d.fy = null }))
  nodeSel = node.append('circle')
    .attr('r', (d) => 5 + Math.min(9, d.deg))
    .attr('fill', (d) => LEVEL_COLORS[levelOf(d)])
    .attr('stroke', '#fff').attr('stroke-width', 1.6).attr('filter', 'url(#lgShadow)')
  labelSel = node.append('text').text((d) => d.label).attr('x', (d) => 8 + Math.min(9, d.deg)).attr('y', 4)
    .attr('font-size', 10).attr('fill', '#222').attr('stroke', '#fbfbfd').attr('stroke-width', 3).attr('paint-order', 'stroke')
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
  if (props.activeHop) highlight(props.activeHop)
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
  nodeSel.attr('opacity', 1).attr('stroke', '#fff').attr('stroke-width', 1.6)
  labelSel.attr('opacity', (d) => (d.deg >= 1 ? 1 : 0.55))
  linkSel.attr('stroke-opacity', 0.55).attr('stroke-width', 1.4)
}
function highlight(hop) {
  if (!nodeSel) return
  if (!hop || !hop.source_id) { restore(); return }
  const hot = new Set([hop.source_id, hop.target_id])
  nodeSel.attr('opacity', (d) => (hot.has(d.id) ? 1 : 0.15))
    .attr('stroke', (d) => (d.id === hop.source_id ? '#f59e0b' : d.id === hop.target_id ? '#16a34a' : '#fff'))
    .attr('stroke-width', (d) => (hot.has(d.id) ? 3 : 1.6))
  labelSel.attr('opacity', (d) => (hot.has(d.id) ? 1 : 0.15))
  linkSel.attr('stroke-opacity', (l) => ((l.source.id === hop.source_id && l.target.id === hop.target_id) || (l.source.id === hop.target_id && l.target.id === hop.source_id) ? 1 : 0.06))
    .attr('stroke-width', (l) => ((l.source.id === hop.source_id && l.target.id === hop.target_id) || (l.source.id === hop.target_id && l.target.id === hop.source_id) ? 3.5 : 1.4))
}

watch(() => [props.nodes, props.edges, hiddenLevels.value, hiddenEdgeTypes.value], () => nextTick(render), { deep: false })
watch(() => props.activeHop, (h) => highlight(h))
onMounted(() => nextTick(render))
onBeforeUnmount(() => { if (sim) sim.stop() })
</script>

<style scoped>
.lg-wrap { position: relative; width: 100%; height: 100%; }
.lg-svg { width: 100%; height: 100%; display: block; min-height: 360px; }
.lg-filters { position: absolute; top: 8px; left: 10px; display: flex; flex-direction: column; gap: 4px; background: rgba(255,255,255,0.92); padding: 6px 9px; border: 1px solid #eee; border-radius: 5px; font-size: 0.66rem; font-family: monospace; z-index: 2; max-width: calc(100% - 24px); }
.lg-row { display: flex; align-items: center; gap: 5px; flex-wrap: wrap; }
.lg-cap { color: #aaa; text-transform: uppercase; letter-spacing: 0.04em; margin-right: 2px; }
.lg-chip { display: inline-flex; align-items: center; gap: 4px; border: 1px solid #e2e2e2; background: #fff; border-radius: 10px; padding: 1px 7px; font-size: 0.66rem; font-family: monospace; color: #555; cursor: pointer; transition: opacity .12s; }
.lg-chip:hover { border-color: #bbb; }
.lg-chip.off { opacity: 0.34; text-decoration: line-through; }
.lg-dot { width: 8px; height: 8px; border-radius: 50%; display: inline-block; }
.lg-dash { width: 11px; height: 3px; border-radius: 2px; display: inline-block; }
</style>
