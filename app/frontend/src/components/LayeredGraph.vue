<template>
  <div class="lg-wrap">
    <!-- Filter controls (levels + edge types + min-degree slider) -->
    <div class="lg-filters" v-if="presentLevels.length || presentEdgeTypes.length">
      <div class="lg-row" v-if="presentLevels.length">
        <span class="lg-cap">levels</span>
        <button
          v-for="lvl in presentLevels"
          :key="lvl"
          class="lg-chip"
          :class="{ off: hiddenLevels.has(lvl) }"
          @click="toggleLevel(lvl)"
          :title="hiddenLevels.has(lvl) ? 'show ' + lvl : 'hide ' + lvl"
        >
          <span class="lg-dot" :style="{ background: LEVEL_COLORS[lvl] }"></span>{{ lvl }}
        </button>
      </div>
      <div class="lg-row" v-if="presentEdgeTypes.length">
        <span class="lg-cap">edges</span>
        <button
          v-for="et in presentEdgeTypes"
          :key="et"
          class="lg-chip"
          :class="{ off: hiddenEdgeTypes.has(et) }"
          @click="toggleEdge(et)"
          :title="hiddenEdgeTypes.has(et) ? 'show ' + et : 'hide ' + et"
        >
          <span class="lg-dash" :style="{ background: edgeColor(et) }"></span>{{ et }}
        </button>
      </div>
      <div class="lg-row" v-if="maxDegree > 1">
        <span class="lg-cap">min links</span>
        <input type="range" class="lg-slider" min="0" :max="maxDegree" v-model.number="minDegree" />
        <span class="lg-val">{{ minDegree }}</span>
        <span class="lg-count">· {{ shownCount }}/{{ props.nodes.length }} nodes</span>
        <button
          class="lg-reset"
          v-if="hiddenLevels.size || hiddenEdgeTypes.size || minDegree"
          @click="resetFilters"
        >reset</button>
      </div>
    </div>

    <svg ref="svgEl" class="lg-svg"></svg>
  </div>
</template>

<script setup>
/**
 * LayeredGraph — force-directed graph with level bands and filter controls.
 *
 * Public contract (props / emits) is UNCHANGED from the pre-EG-F version:
 *   props : nodes  [{ id, label, entity_type, level }]
 *           edges  [{ source, target, edge_type }]
 *           activeHop  { source_id, target_id } | null
 *   emits : node-click(nodeData)
 *
 * Internal structure (EG-F decoupling):
 *   useGraphModel  — all filter logic + model derivation (no DOM/D3)
 *   createSvgRenderer — D3/SVG rendering behind a swappable interface
 *     draw / highlightHop / hoverNode / restore / destroy
 *
 * To swap in a Canvas renderer at ~500+ nodes: replace the import below with
 *   import { createSvgRenderer } from '../renderers/canvasRenderer.js'
 * and implement the same five methods. useGraphModel and consuming views
 * are completely untouched.
 */
import { ref, computed, watch, onMounted, onBeforeUnmount, nextTick, toRef } from 'vue'
import { useGraphModel, LEVEL_COLORS, edgeColor } from '../composables/useGraphModel.js'
import { createSvgRenderer } from '../renderers/svgRenderer.js'

// ── Props / emits (public contract — do not change) ───────────────────────────
const props = defineProps({
  nodes:     { type: Array,  default: () => [] },   // [{ id, label, entity_type, level }]
  edges:     { type: Array,  default: () => [] },   // [{ source, target, edge_type }]
  activeHop: { type: Object, default: null },       // { source_id, target_id } | null
})
const emit = defineEmits(['node-click'])

// ── DOM ref ───────────────────────────────────────────────────────────────────
const svgEl = ref(null)

/** The active renderer instance (replaced on every full repaint). */
let renderer = null

// ── Model layer (filter state + derivation — no D3) ──────────────────────────
const {
  presentLevels, presentEdgeTypes,
  maxDegree, shownCount,
  hiddenLevels, hiddenEdgeTypes, minDegree,
  toggleLevel, toggleEdge, resetFilters,
  computeModel,
} = useGraphModel(
  computed(() => props.nodes),
  computed(() => props.edges),
)

// ── Render ────────────────────────────────────────────────────────────────────
function render() {
  const el = svgEl.value
  if (!el) return

  const W = el.clientWidth  || 700
  const H = el.clientHeight || 520

  // Destroy previous renderer (stops simulation + clears SVG)
  if (renderer) renderer.destroy()

  // Create new renderer, wiring interaction callbacks
  renderer = createSvgRenderer(el, { W, H }, {
    onNodeClick: (d)  => emit('node-click', d),
    onHover:     (d)  => { if (!props.activeHop) renderer.hoverNode(d) },
    onHoverEnd:  ()   => { if (!props.activeHop) renderer.restore() },
  })

  // Derive filtered model and hand it to the renderer
  renderer.draw(computeModel())

  // Re-apply hop highlight if one is active (e.g. filter changed while hop is set)
  if (props.activeHop) renderer.highlightHop(props.activeHop)
}

// ── Watchers ──────────────────────────────────────────────────────────────────

// Full repaint when data or filter state changes
watch(
  () => [
    props.nodes,
    props.edges,
    hiddenLevels.value,
    hiddenEdgeTypes.value,
    minDegree.value,
  ],
  () => nextTick(render),
  { deep: false }
)

// Lightweight hop highlight — no repaint, just style update on the existing SVG
watch(
  () => props.activeHop,
  (hop) => {
    if (!renderer) return
    if (hop) renderer.highlightHop(hop)
    else     renderer.restore()
  }
)

onMounted(() => nextTick(render))
onBeforeUnmount(() => { if (renderer) renderer.destroy() })
</script>

<style scoped>
.lg-wrap { position: relative; width: 100%; height: 100%; }
.lg-svg  { width: 100%; height: 100%; display: block; min-height: 360px; }

/* Filter overlay */
.lg-filters {
  position: absolute;
  top: 8px; left: 10px;
  display: flex; flex-direction: column; gap: 4px;
  background: rgba(255, 255, 255, 0.93);
  padding: 6px 9px;
  border: 1px solid #e8e8e8;
  border-radius: 6px;
  font-size: 0.66rem;
  font-family: monospace;
  z-index: 2;
  max-width: calc(100% - 24px);
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.06);
}
.lg-row    { display: flex; align-items: center; gap: 5px; flex-wrap: wrap; }
.lg-cap    { color: #aaa; text-transform: uppercase; letter-spacing: 0.04em; margin-right: 2px; }
.lg-chip   {
  display: inline-flex; align-items: center; gap: 4px;
  border: 1px solid #e2e2e2; background: #fff;
  border-radius: 10px; padding: 1px 7px;
  font-size: 0.66rem; font-family: monospace; color: #555;
  cursor: pointer; transition: opacity .12s, border-color .12s;
}
.lg-chip:hover { border-color: #bbb; }
.lg-chip.off   { opacity: 0.34; text-decoration: line-through; }
.lg-dot  { width: 8px; height: 8px; border-radius: 50%; display: inline-block; }
.lg-dash { width: 11px; height: 3px; border-radius: 2px; display: inline-block; }
.lg-slider { width: 90px; accent-color: #3a7abf; cursor: pointer; }
.lg-val    { color: #333; font-weight: 700; min-width: 10px; }
.lg-count  { color: #aaa; }
.lg-reset  {
  margin-left: 4px;
  border: 1px solid #e2e2e2; background: #fff;
  border-radius: 10px; padding: 1px 8px;
  font-size: 0.66rem; font-family: monospace; color: #3a7abf;
  cursor: pointer; transition: border-color .12s;
}
.lg-reset:hover { border-color: #3a7abf; }
</style>
