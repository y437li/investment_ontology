<template>
  <div class="graph-panel">
    <div class="panel-header">
      <span class="panel-title">Knowledge Graph</span>
      <div class="header-tools">
        <button class="tool-btn" @click="emit('refresh')" :disabled="loading" title="Refresh graph">
          <span class="icon-refresh" :class="{ spinning: loading }">↻</span>
          <span class="btn-text">Refresh</span>
        </button>
      </div>
    </div>

    <div class="graph-container" ref="graphContainer">
      <div v-if="graphData" class="graph-view">
        <svg ref="graphSvg" class="graph-svg"></svg>

        <!-- Node/edge detail panel -->
        <div v-if="selectedItem" class="detail-panel">
          <div class="detail-header">
            <span class="detail-title">
              {{ selectedItem.type === 'node' ? 'Entity' : 'Relationship' }}
            </span>
            <button class="detail-close" @click="selectedItem = null">×</button>
          </div>
          <div class="detail-content">
            <template v-if="selectedItem.type === 'node'">
              <div class="detail-row">
                <span class="detail-label">Label:</span>
                <span class="detail-value">{{ selectedItem.data.label }}</span>
              </div>
              <div class="detail-row">
                <span class="detail-label">Type:</span>
                <span class="detail-value">{{ selectedItem.data.entity_type }}</span>
              </div>
              <div class="detail-row">
                <span class="detail-label">ID:</span>
                <span class="detail-value mono-text">{{ selectedItem.data.entity_id }}</span>
              </div>
            </template>
            <template v-else>
              <div class="detail-row">
                <span class="detail-label">Type:</span>
                <span class="detail-value">{{ selectedItem.data.edge_type }}</span>
              </div>
              <div class="detail-row">
                <span class="detail-label">Weight:</span>
                <span class="detail-value">{{ selectedItem.data.weight }}</span>
              </div>
              <div class="detail-row">
                <span class="detail-label">Method:</span>
                <span class="detail-value">{{ selectedItem.data.extraction_method }}</span>
              </div>
            </template>
          </div>
        </div>
      </div>

      <div v-else-if="loading" class="graph-state">
        <div class="loading-spinner"></div>
        <p>Loading graph data...</p>
      </div>

      <div v-else class="graph-state">
        <div class="empty-icon">❖</div>
        <p class="empty-text">No graph data yet. Run the pipeline first.</p>
      </div>
    </div>

    <!-- Legend -->
    <div v-if="graphData && entityTypes.length" class="graph-legend">
      <span class="legend-title">Entity Types</span>
      <div class="legend-items">
        <div class="legend-item" v-for="t in entityTypes" :key="t.name">
          <span class="legend-dot" :style="{ background: t.color }"></span>
          <span class="legend-label">{{ t.name }}</span>
        </div>
      </div>
    </div>

    <!-- Edge label toggle -->
    <div v-if="graphData" class="edge-toggle">
      <label class="toggle-switch">
        <input type="checkbox" v-model="showEdgeLabels" />
        <span class="slider"></span>
      </label>
      <span class="toggle-label">Edge Labels</span>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, watch, onMounted, onUnmounted, nextTick } from 'vue'
import * as d3 from 'd3'

const props = defineProps({
  graphData: Object,
  loading: Boolean
})

const emit = defineEmits(['refresh'])

const graphContainer = ref(null)
const graphSvg = ref(null)
const selectedItem = ref(null)
const showEdgeLabels = ref(true)

let currentSimulation = null
let linkLabelsRef = null
let linkLabelBgRef = null

const COLORS = [
  '#1a56db', '#7c3aed', '#059669', '#dc2626', '#d97706',
  '#0891b2', '#be185d', '#65a30d', '#0f766e', '#b45309'
]

const entityTypes = computed(() => {
  if (!props.graphData?.nodes) return []
  const typeMap = {}
  props.graphData.nodes.forEach(n => {
    const t = n.entity_type || 'Entity'
    if (!typeMap[t]) {
      typeMap[t] = { name: t, count: 0, color: COLORS[Object.keys(typeMap).length % COLORS.length] }
    }
    typeMap[t].count++
  })
  return Object.values(typeMap)
})

const renderGraph = () => {
  if (!graphSvg.value || !props.graphData) return
  if (currentSimulation) currentSimulation.stop()

  const container = graphContainer.value
  const width = container.clientWidth
  const height = container.clientHeight

  const svg = d3.select(graphSvg.value)
    .attr('width', width)
    .attr('height', height)
    .attr('viewBox', `0 0 ${width} ${height}`)
  svg.selectAll('*').remove()

  const nodesData = props.graphData.nodes || []
  const edgesData = props.graphData.edges || []
  if (nodesData.length === 0) return

  const colorMap = {}
  entityTypes.value.forEach(t => { colorMap[t.name] = t.color })
  const getColor = t => colorMap[t] || '#999'

  const nodeById = {}
  nodesData.forEach(n => { nodeById[n.entity_id] = n })

  const nodes = nodesData.map(n => ({
    id: n.entity_id,
    label: n.label || n.entity_id,
    type: n.entity_type || 'Entity',
    rawData: n
  }))

  const nodeIdSet = new Set(nodes.map(n => n.id))
  const edges = edgesData
    .filter(e => nodeIdSet.has(e.source_entity_id) && nodeIdSet.has(e.target_entity_id))
    .map(e => ({
      source: e.source_entity_id,
      target: e.target_entity_id,
      label: e.edge_type || 'related',
      rawData: e
    }))

  const simulation = d3.forceSimulation(nodes)
    .force('link', d3.forceLink(edges).id(d => d.id).distance(140))
    .force('charge', d3.forceManyBody().strength(-350))
    .force('center', d3.forceCenter(width / 2, height / 2))
    .force('collide', d3.forceCollide(45))
    .force('x', d3.forceX(width / 2).strength(0.04))
    .force('y', d3.forceY(height / 2).strength(0.04))

  currentSimulation = simulation

  const g = svg.append('g')
  svg.call(
    d3.zoom()
      .extent([[0, 0], [width, height]])
      .scaleExtent([0.1, 4])
      .on('zoom', event => g.attr('transform', event.transform))
  )

  const linkGroup = g.append('g').attr('class', 'links')

  const link = linkGroup.selectAll('line')
    .data(edges).enter().append('line')
    .attr('stroke', '#C0C0C0')
    .attr('stroke-width', 1.5)
    .style('cursor', 'pointer')
    .on('click', (event, d) => {
      event.stopPropagation()
      linkGroup.selectAll('line').attr('stroke', '#C0C0C0').attr('stroke-width', 1.5)
      d3.select(event.target).attr('stroke', '#1a56db').attr('stroke-width', 3)
      selectedItem.value = { type: 'edge', data: d.rawData }
    })

  const linkLabelBg = linkGroup.selectAll('rect')
    .data(edges).enter().append('rect')
    .attr('fill', 'rgba(255,255,255,0.9)')
    .attr('rx', 2)
    .style('display', showEdgeLabels.value ? 'block' : 'none')

  const linkLabels = linkGroup.selectAll('text')
    .data(edges).enter().append('text')
    .text(d => d.label)
    .attr('font-size', '9px')
    .attr('fill', '#666')
    .attr('text-anchor', 'middle')
    .attr('dominant-baseline', 'middle')
    .style('font-family', 'var(--font-mono, monospace)')
    .style('display', showEdgeLabels.value ? 'block' : 'none')
    .style('pointer-events', 'none')

  linkLabelsRef = linkLabels
  linkLabelBgRef = linkLabelBg

  const nodeGroup = g.append('g').attr('class', 'nodes')

  const node = nodeGroup.selectAll('circle')
    .data(nodes).enter().append('circle')
    .attr('r', 10)
    .attr('fill', d => getColor(d.type))
    .attr('stroke', '#fff')
    .attr('stroke-width', 2.5)
    .style('cursor', 'pointer')
    .call(
      d3.drag()
        .on('start', (event, d) => { d.fx = d.x; d.fy = d.y })
        .on('drag', (event, d) => {
          simulation.alphaTarget(0.3).restart()
          d.fx = event.x; d.fy = event.y
        })
        .on('end', (event, d) => {
          simulation.alphaTarget(0)
          d.fx = null; d.fy = null
        })
    )
    .on('click', (event, d) => {
      event.stopPropagation()
      node.attr('stroke', '#fff').attr('stroke-width', 2.5)
      d3.select(event.target).attr('stroke', '#1a56db').attr('stroke-width', 4)
      selectedItem.value = { type: 'node', data: d.rawData }
    })

  const nodeLabels = nodeGroup.selectAll('text')
    .data(nodes).enter().append('text')
    .text(d => d.label.length > 10 ? d.label.slice(0, 10) + '…' : d.label)
    .attr('font-size', '11px')
    .attr('fill', '#333')
    .attr('font-weight', '500')
    .attr('dx', 14).attr('dy', 4)
    .style('pointer-events', 'none')
    .style('font-family', 'system-ui, sans-serif')

  simulation.on('tick', () => {
    link
      .attr('x1', d => d.source.x).attr('y1', d => d.source.y)
      .attr('x2', d => d.target.x).attr('y2', d => d.target.y)

    linkLabels
      .attr('x', d => (d.source.x + d.target.x) / 2)
      .attr('y', d => (d.source.y + d.target.y) / 2)

    linkLabelBg.each(function(d, i) {
      const textEl = linkLabels.nodes()[i]
      try {
        const bbox = textEl.getBBox()
        d3.select(this)
          .attr('x', bbox.x - 3).attr('y', bbox.y - 2)
          .attr('width', bbox.width + 6).attr('height', bbox.height + 4)
      } catch {}
    })

    node.attr('cx', d => d.x).attr('cy', d => d.y)
    nodeLabels.attr('x', d => d.x).attr('y', d => d.y)
  })

  svg.on('click', () => {
    selectedItem.value = null
    node.attr('stroke', '#fff').attr('stroke-width', 2.5)
    linkGroup.selectAll('line').attr('stroke', '#C0C0C0').attr('stroke-width', 1.5)
  })
}

watch(() => props.graphData, () => { nextTick(renderGraph) }, { deep: true })

watch(showEdgeLabels, val => {
  if (linkLabelsRef) linkLabelsRef.style('display', val ? 'block' : 'none')
  if (linkLabelBgRef) linkLabelBgRef.style('display', val ? 'block' : 'none')
})

const handleResize = () => { nextTick(renderGraph) }

onMounted(() => { window.addEventListener('resize', handleResize) })
onUnmounted(() => {
  window.removeEventListener('resize', handleResize)
  if (currentSimulation) currentSimulation.stop()
})
</script>

<style scoped>
.graph-panel {
  position: relative;
  width: 100%;
  height: 100%;
  background: #FAFAFA;
  background-image: radial-gradient(#D0D0D0 1.5px, transparent 1.5px);
  background-size: 24px 24px;
  overflow: hidden;
}

.panel-header {
  position: absolute;
  top: 0; left: 0; right: 0;
  padding: 14px 18px;
  z-index: 10;
  display: flex;
  justify-content: space-between;
  align-items: center;
  background: linear-gradient(to bottom, rgba(255,255,255,0.95), rgba(255,255,255,0));
  pointer-events: none;
}

.panel-title {
  font-size: 13px;
  font-weight: 700;
  color: #333;
  pointer-events: auto;
}

.header-tools {
  pointer-events: auto;
  display: flex;
  gap: 8px;
}

.tool-btn {
  height: 30px;
  padding: 0 12px;
  border: 1px solid #E0E0E0;
  background: #FFF;
  border-radius: 4px;
  display: flex;
  align-items: center;
  gap: 6px;
  cursor: pointer;
  color: #666;
  font-size: 12px;
  transition: all 0.2s;
}

.tool-btn:hover { background: #F5F5F5; }

.icon-refresh.spinning {
  animation: spin 1s linear infinite;
  display: inline-block;
}

@keyframes spin { to { transform: rotate(360deg); } }

.graph-container, .graph-view, .graph-svg {
  width: 100%;
  height: 100%;
  display: block;
}

.graph-state {
  position: absolute;
  top: 50%; left: 50%;
  transform: translate(-50%, -50%);
  text-align: center;
  color: #999;
}

.empty-icon { font-size: 40px; margin-bottom: 12px; opacity: 0.2; }
.empty-text { font-size: 13px; }

.loading-spinner {
  width: 36px; height: 36px;
  border: 3px solid #E0E0E0;
  border-top-color: var(--accent, #1a56db);
  border-radius: 50%;
  animation: spin 1s linear infinite;
  margin: 0 auto 12px;
}

.detail-panel {
  position: absolute;
  top: 60px; right: 20px;
  width: 280px;
  max-height: calc(100% - 80px);
  background: #FFF;
  border: 1px solid #EAEAEA;
  border-radius: 8px;
  box-shadow: 0 4px 20px rgba(0,0,0,0.1);
  overflow: hidden;
  z-index: 20;
  display: flex;
  flex-direction: column;
}

.detail-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 12px 14px;
  background: #FAFAFA;
  border-bottom: 1px solid #EEE;
}

.detail-title { font-size: 13px; font-weight: 700; color: #333; }

.detail-close {
  background: none; border: none; font-size: 18px; cursor: pointer; color: #999;
}

.detail-content { padding: 14px; overflow-y: auto; }

.detail-row { display: flex; flex-wrap: wrap; gap: 4px; margin-bottom: 10px; }
.detail-label { color: #888; font-size: 11px; font-weight: 600; min-width: 70px; }
.detail-value { color: #333; font-size: 12px; flex: 1; word-break: break-word; }
.mono-text { font-family: var(--font-mono, monospace); font-size: 10px; color: #555; }

.graph-legend {
  position: absolute; bottom: 20px; left: 20px;
  background: rgba(255,255,255,0.95);
  padding: 10px 14px;
  border-radius: 6px;
  border: 1px solid #EAEAEA;
  box-shadow: 0 2px 12px rgba(0,0,0,0.06);
  z-index: 10;
}

.legend-title {
  display: block; font-size: 10px; font-weight: 700;
  color: var(--accent, #1a56db); margin-bottom: 8px; text-transform: uppercase;
}

.legend-items { display: flex; flex-wrap: wrap; gap: 8px 14px; max-width: 300px; }

.legend-item { display: flex; align-items: center; gap: 5px; font-size: 11px; color: #555; }

.legend-dot {
  width: 9px; height: 9px; border-radius: 50%; flex-shrink: 0;
}

.edge-toggle {
  position: absolute; top: 56px; left: 20px;
  display: flex; align-items: center; gap: 8px;
  background: #FFF; padding: 6px 12px;
  border-radius: 16px; border: 1px solid #E0E0E0;
  box-shadow: 0 1px 6px rgba(0,0,0,0.04); z-index: 10;
}

.toggle-switch {
  position: relative; display: inline-block; width: 36px; height: 20px;
}

.toggle-switch input { opacity: 0; width: 0; height: 0; }

.slider {
  position: absolute; cursor: pointer; top: 0; left: 0; right: 0; bottom: 0;
  background: #E0E0E0; border-radius: 20px; transition: 0.3s;
}

.slider:before {
  position: absolute; content: ""; height: 14px; width: 14px;
  left: 3px; bottom: 3px; background: white; border-radius: 50%; transition: 0.3s;
}

input:checked + .slider { background: var(--accent, #1a56db); }
input:checked + .slider:before { transform: translateX(16px); }

.toggle-label { font-size: 11px; color: #666; }
</style>
