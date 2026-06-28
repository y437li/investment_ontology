/**
 * useGraphModel — library-agnostic filter + derivation composable.
 *
 * Responsibilities:
 *   - Owns all filter state (hiddenLevels, hiddenEdgeTypes, minDegree)
 *   - Derives presentLevels / presentEdgeTypes / maxDegree
 *   - Exposes computeModel() → { nodes, edges, adjacency }
 *
 * NO D3 or DOM dependency. Renderer-agnostic: the SVG renderer, a future
 * Canvas renderer, or a test suite can all call computeModel() identically.
 *
 * Canvas swap trigger: when the graph sustains >~500 nodes, SVG frame-rate
 * degrades. At that point, create renderers/canvasRenderer.js implementing
 * the same interface (draw / highlightHop / hoverNode / restore / destroy)
 * and swap the import in LayeredGraph.vue — this composable is untouched.
 */
import { ref, computed } from 'vue'

// ── Palette & layout constants ────────────────────────────────────────────────

export const LEVELS = ['macro', 'industry', 'company', 'idiosyncratic', 'contextual']

/**
 * Low-saturation research palette: calm, readable on white; 6 level bands.
 * (Purple-indigo → steel-blue → sea-green → sienna → cadet → blue-grey)
 */
export const LEVEL_COLORS = {
  macro:         '#5b5ea6',   // periwinkle-indigo
  industry:      '#3a7abf',   // steel blue
  company:       '#2e8b5a',   // sea green
  idiosyncratic: '#c0703a',   // sienna
  contextual:    '#7a8d9c',   // cadet grey
  evidence:      '#a0b0bc',   // blue-grey
}

/** Fractional Y position for each level band (0 = top, 1 = bottom). */
export const LEVEL_Y = {
  macro: 0.12, industry: 0.33, company: 0.55,
  idiosyncratic: 0.77, contextual: 0.92, evidence: 0.97,
}

/** Edge colour per relationship type. */
export const EDGE_COLORS = {
  benefits:     '#2e8b5a',   // sea green
  hurts:        '#c0392b',   // muted red
  causes:       '#c0703a',   // sienna
  exposed_to:   '#3a7abf',   // steel blue
  sensitive_to: '#5b5ea6',   // periwinkle
  located_in:   '#7a8d9c',   // cadet grey
}

// Entity-type → level fallback for raw graph.json nodes without explicit level
const TYPE_LEVEL = {
  Company:        'company',
  Sector:         'industry',
  Commodity:      'macro',
  MacroIndicator: 'macro',
  Event:          'idiosyncratic',
  EconomicConcept:'contextual',
  Geography:      'contextual',
  Document:       'evidence',
}

/** Resolve the effective level for a node (explicit > type-inferred > contextual). */
export function levelOf(n) {
  if (n && LEVEL_COLORS[n.level]) return n.level
  return (n && TYPE_LEVEL[n.entity_type]) || 'contextual'
}

/** Colour for an edge type, with fallback. */
export function edgeColor(t) {
  return EDGE_COLORS[t] || '#94a3b8'
}

// ── Composable ────────────────────────────────────────────────────────────────

/**
 * @param {import('vue').ComputedRef<object[]>} nodesRef  reactive nodes array
 * @param {import('vue').ComputedRef<object[]>} edgesRef  reactive edges array
 */
export function useGraphModel(nodesRef, edgesRef) {
  const hiddenLevels    = ref(new Set())
  const hiddenEdgeTypes = ref(new Set())
  const minDegree       = ref(0)
  const shownCount      = ref(0)

  const presentLevels = computed(() =>
    LEVELS.filter((lv) => nodesRef.value.some((n) => levelOf(n) === lv))
  )

  const presentEdgeTypes = computed(() =>
    Array.from(new Set(edgesRef.value.map((e) => e.edge_type))).sort()
  )

  const maxDegree = computed(() => {
    const deg = {}
    edgesRef.value.forEach((e) => {
      deg[e.source] = (deg[e.source] || 0) + 1
      deg[e.target] = (deg[e.target] || 0) + 1
    })
    return Math.max(1, ...Object.values(deg))
  })

  function toggleLevel(lv) {
    const s = new Set(hiddenLevels.value)
    s.has(lv) ? s.delete(lv) : s.add(lv)
    hiddenLevels.value = s
  }

  function toggleEdge(et) {
    const s = new Set(hiddenEdgeTypes.value)
    s.has(et) ? s.delete(et) : s.add(et)
    hiddenEdgeTypes.value = s
  }

  function resetFilters() {
    hiddenLevels.value    = new Set()
    hiddenEdgeTypes.value = new Set()
    minDegree.value       = 0
  }

  /**
   * Derive the filtered, degree-annotated model ready for any renderer.
   * Returns NEW arrays (safe to mutate for D3 simulation).
   *
   * @returns {{ nodes: object[], edges: object[], adjacency: Record<string, Set<string>> }}
   */
  function computeModel() {
    const visNodes = nodesRef.value.filter((n) => !hiddenLevels.value.has(levelOf(n)))
    const visIds   = new Set(visNodes.map((n) => n.id))

    const visEdges = edgesRef.value.filter(
      (e) =>
        !hiddenEdgeTypes.value.has(e.edge_type) &&
        visIds.has(e.source) &&
        visIds.has(e.target)
    )

    // Degree computation (only over visible edges)
    const degree = {}
    visEdges.forEach((e) => {
      degree[e.source] = (degree[e.source] || 0) + 1
      degree[e.target] = (degree[e.target] || 0) + 1
    })

    const nodesAll = visNodes.map((n) => ({ ...n, deg: degree[n.id] || 0 }))
    const nodes    = minDegree.value > 0
      ? nodesAll.filter((n) => n.deg >= minDegree.value)
      : nodesAll

    shownCount.value = nodes.length

    const idset = new Set(nodes.map((n) => n.id))
    const edges = visEdges
      .filter((e) => idset.has(e.source) && idset.has(e.target))
      .map((e) => ({ ...e }))

    // Pre-build adjacency for O(1) hover-highlight queries in the renderer
    const adjacency = {}
    edges.forEach((e) => {
      ;(adjacency[e.source] ||= new Set()).add(e.target)
      ;(adjacency[e.target] ||= new Set()).add(e.source)
    })

    return { nodes, edges, adjacency }
  }

  return {
    // Reactive filter state (read by template)
    hiddenLevels,
    hiddenEdgeTypes,
    minDegree,
    shownCount,
    // Derived (read-only outside)
    presentLevels,
    presentEdgeTypes,
    maxDegree,
    // Filter actions
    toggleLevel,
    toggleEdge,
    resetFilters,
    // Model derivation — call before every renderer.draw()
    computeModel,
  }
}
