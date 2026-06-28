/**
 * svgRenderer.js — D3/SVG renderer implementing the graph renderer interface.
 *
 * Interface (all public exports of createSvgRenderer):
 *   draw(model)          — full repaint: { nodes, edges, adjacency }
 *   highlightHop(hop)    — dim everything except { source_id, target_id }; null = restore
 *   hoverNode(nodeData)  — dim everything except the hovered node + 1-hop neighbours
 *   restore()            — clear all highlight/dim state
 *   destroy()            — stop simulation, remove SVG content
 *
 * Canvas swap note:
 *   When sustained node count reaches ~500+ and SVG frame-rate degrades,
 *   create renderers/canvasRenderer.js implementing the same five methods,
 *   then change the import in LayeredGraph.vue from './renderers/svgRenderer.js'
 *   to './renderers/canvasRenderer.js'. useGraphModel and all consuming views
 *   are untouched.
 */
import * as d3 from 'd3'
import {
  LEVEL_COLORS,
  LEVEL_Y,
  LEVELS,
  levelOf,
  edgeColor,
} from '../composables/useGraphModel.js'

// ── Sizing helpers ────────────────────────────────────────────────────────────

/** Node radius: base 5px + up to 10px bonus for degree. */
const nodeR = (d) => 5 + Math.min(10, (d?.deg || 0) * 0.85)

// ── SVG defs helpers ──────────────────────────────────────────────────────────

/** Lighten a hex colour by blending toward white. */
function lighten(hex, amt) {
  const r  = parseInt(hex.slice(1, 3), 16)
  const gv = parseInt(hex.slice(3, 5), 16)
  const b  = parseInt(hex.slice(5, 7), 16)
  const li = (c) => Math.min(255, Math.round(c + (255 - c) * amt))
  return `rgb(${li(r)},${li(gv)},${li(b)})`
}

/** Build radial gradients (one per level) into a defs selection. */
function buildGradients(defs) {
  Object.entries(LEVEL_COLORS).forEach(([lv, base]) => {
    const id = `lgGrad-${lv}`
    if (!defs.select(`#${id}`).empty()) return
    const grad = defs.append('radialGradient')
      .attr('id', id)
      .attr('cx', '35%').attr('cy', '35%').attr('r', '65%')
    grad.append('stop').attr('offset', '0%').attr('stop-color', lighten(base, 0.4))
    grad.append('stop').attr('offset', '100%').attr('stop-color', base)
  })
}

/** Add a drop-shadow filter to defs (idempotent). */
function buildShadowFilter(defs) {
  if (!defs.select('#lgShadow').empty()) return
  const filt = defs.append('filter')
    .attr('id', 'lgShadow')
    .attr('x', '-60%').attr('y', '-60%')
    .attr('width', '220%').attr('height', '220%')
  filt.append('feDropShadow')
    .attr('dx', 0).attr('dy', 1.5).attr('stdDeviation', 2)
    .attr('flood-color', '#1a1a2e').attr('flood-opacity', 0.14)
}

/**
 * Add a tapered arrowhead marker for the given edge type (idempotent).
 * The marker tip sits at (9,0) in viewBox space; refX=9 → tip at path end.
 * Paths end at node boundary (not centre), so the tip touches the circle edge.
 */
function ensureMarker(defs, edgeType) {
  const id = `lgarr-${edgeType}`
  if (!defs.select(`#${id}`).empty()) return
  defs.append('marker')
    .attr('id', id)
    .attr('viewBox', '0 -5 10 10')
    .attr('refX', 9)          // tip at path endpoint (node boundary)
    .attr('refY', 0)
    .attr('markerWidth', 7)
    .attr('markerHeight', 7)
    .attr('orient', 'auto')
    .append('path')
    .attr('d', 'M0,-4L9,0L0,4L1.5,0Z')   // tapered: wide base → pointed tip
    .attr('fill', edgeColor(edgeType))
    .attr('opacity', 0.85)
}

// ── Bezier path helpers ───────────────────────────────────────────────────────

/**
 * Quadratic bezier path from source to target node boundary.
 * The control point is slightly off the straight midpoint (perpendicular),
 * giving a gentle arc. Path ends at the node circle boundary so the
 * tapered arrowhead tip (refX=9) sits exactly on the edge of the circle.
 *
 * @param {object} d      D3 link datum (d.source / d.target are node objects)
 * @param {number} tgtR   target node radius (to shorten path to boundary)
 */
function curvePath(d, tgtR) {
  const sx = d.source?.x ?? 0, sy = d.source?.y ?? 0
  const tx = d.target?.x ?? 0, ty = d.target?.y ?? 0

  const dx = tx - sx, dy = ty - sy
  const len = Math.sqrt(dx * dx + dy * dy) || 1

  // Gentle perpendicular offset (15% of edge length, capped at 28px)
  const offset = Math.min(28, len * 0.15)
  const mx = (sx + tx) / 2
  const my = (sy + ty) / 2
  const cx = mx - (dy / len) * offset
  const cy = my + (dx / len) * offset

  // Tangent direction at t=1 of Q bezier: from ctrl-point to endpoint
  const tdx = tx - cx, tdy = ty - cy
  const tlen = Math.sqrt(tdx * tdx + tdy * tdy) || 1

  // Shorten path to stop at the node circle boundary (+ 1px buffer)
  const r = (tgtR || 6) + 1
  const ex = tx - (tdx / tlen) * r
  const ey = ty - (tdy / tlen) * r

  return `M${sx},${sy} Q${cx},${cy} ${ex},${ey}`
}

// ── Factory ───────────────────────────────────────────────────────────────────

/**
 * Create an SVG renderer attached to `svgEl`.
 *
 * @param {SVGElement}   svgEl  raw SVG DOM element (from Vue ref)
 * @param {{ W, H }}     dims   pixel dimensions at mount time
 * @param {object}       cbs    { onNodeClick(d), onHover(d), onHoverEnd() }
 * @returns {{ draw, highlightHop, hoverNode, restore, destroy }}
 */
export function createSvgRenderer(svgEl, { W, H }, cbs = {}) {
  let sim         = null
  let linkSel     = null
  let nodeSel     = null    // the inner circle selection (for opacity / stroke)
  let nodeGrpSel  = null    // the <g> wrapper selection (for transform)
  let labelSel    = null
  let _adjacency  = {}

  // ── Static SVG scaffold ───────────────────────────────────────────────────
  const svg = d3.select(svgEl)
  svg.selectAll('*').remove()
  svg
    .attr('viewBox', `0 0 ${W} ${H}`)
    .style('background', '#f8f9fc')

  // Pan & zoom
  const g = svg.append('g').attr('class', 'lg-root')
  svg.call(
    d3.zoom()
      .scaleExtent([0.15, 5])
      .on('zoom', (ev) => g.attr('transform', ev.transform))
  )

  // ── Defs ──────────────────────────────────────────────────────────────────
  const defs = svg.append('defs')
  buildShadowFilter(defs)
  buildGradients(defs)

  // ── Layer order (bottom to top) ───────────────────────────────────────────
  const bandLayer = g.append('g').attr('class', 'lg-bands')
  const linkLayer = g.append('g').attr('class', 'lg-links')
  const nodeLayer = g.append('g').attr('class', 'lg-nodes')

  // ── draw(model) ───────────────────────────────────────────────────────────
  function draw(model) {
    const { nodes, edges, adjacency } = model
    _adjacency = adjacency

    // Ensure arrowhead markers exist for every edge type in this dataset
    const edgeTypes = Array.from(new Set(edges.map((e) => e.edge_type)))
    edgeTypes.forEach((t) => ensureMarker(defs, t))

    // ── Level bands ──────────────────────────────────────────────────────────
    bandLayer.selectAll('*').remove()
    LEVELS
      .filter((lv) => nodes.some((n) => levelOf(n) === lv))
      .forEach((lv) => {
        const y = (LEVEL_Y[lv] ?? 0.9) * H
        bandLayer.append('rect')
          .attr('x', 0).attr('y', y - 42).attr('width', W).attr('height', 84)
          .attr('fill', LEVEL_COLORS[lv]).attr('opacity', 0.045).attr('rx', 0)
        bandLayer.append('text')
          .attr('x', 14).attr('y', y - 26)
          .text(lv.toUpperCase())
          .attr('font-size', 10.5)
          .attr('font-family', 'monospace')
          .attr('font-weight', 700)
          .attr('letter-spacing', '0.08em')
          .attr('fill', LEVEL_COLORS[lv])
          .attr('opacity', 0.48)
          .style('pointer-events', 'none')
      })

    // ── Links (curved bezier paths) ───────────────────────────────────────────
    linkLayer.selectAll('*').remove()
    linkSel = linkLayer.selectAll('path')
      .data(edges)
      .enter()
      .append('path')
      .attr('fill', 'none')
      .attr('stroke', (d) => edgeColor(d.edge_type))
      .attr('stroke-width', 1.6)
      .attr('stroke-opacity', 0.40)
      .attr('marker-end', (d) => `url(#lgarr-${d.edge_type})`)
      .style('pointer-events', 'none')

    // ── Nodes ─────────────────────────────────────────────────────────────────
    nodeLayer.selectAll('*').remove()

    nodeGrpSel = nodeLayer.selectAll('g')
      .data(nodes, (d) => d.id)
      .enter()
      .append('g')
      .attr('class', 'lg-node')
      .style('cursor', 'pointer')
      .on('click',     (ev, d) => { ev.stopPropagation(); cbs.onNodeClick?.(d) })
      .on('mouseover', (ev, d) => cbs.onHover?.(d))
      .on('mouseout',  ()      => cbs.onHoverEnd?.())
      .call(
        d3.drag()
          .on('start', (ev, d) => { if (!ev.active) sim?.alphaTarget(0.2).restart(); d.fx = d.x; d.fy = d.y })
          .on('drag',  (ev, d) => { d.fx = ev.x; d.fy = ev.y })
          .on('end',   (ev, d) => { if (!ev.active) sim?.alphaTarget(0); d.fx = null; d.fy = null })
      )

    // Outer halo ring (subtle ambient glow per level colour)
    nodeGrpSel.append('circle')
      .attr('class', 'lg-halo')
      .attr('r', (d) => nodeR(d) + 4)
      .attr('fill', (d) => LEVEL_COLORS[levelOf(d)])
      .attr('opacity', 0.11)
      .style('pointer-events', 'none')

    // Main node circle: radial gradient fill + drop shadow
    nodeSel = nodeGrpSel.append('circle')
      .attr('class', 'lg-circle')
      .attr('r', (d) => nodeR(d))
      .attr('fill', (d) => `url(#lgGrad-${levelOf(d)})`)
      .attr('stroke', '#ffffff')
      .attr('stroke-width', 1.8)
      .attr('filter', 'url(#lgShadow)')

    // Labels: white backing-stroke (de-collision via truncation + collision force)
    labelSel = nodeGrpSel.append('text')
      .attr('class', 'lg-label')
      .text((d) => {
        const lbl = d.label || d.id || ''
        return lbl.length > 18 ? lbl.slice(0, 17) + '…' : lbl
      })
      .attr('x', (d) => nodeR(d) + 5)
      .attr('y', 4)
      .attr('font-size', 10.5)
      .attr('font-family', 'system-ui, -apple-system, sans-serif')
      .attr('fill', '#2c3040')
      .attr('stroke', '#f8f9fc')
      .attr('stroke-width', 3.5)
      .attr('paint-order', 'stroke')
      .attr('opacity', (d) => (d.deg >= 1 ? 0.90 : 0.44))
      .style('pointer-events', 'none')

    // ── Force simulation ──────────────────────────────────────────────────────
    if (sim) sim.stop()

    sim = d3.forceSimulation(nodes)
      // Tuned link: longer rest distance, weaker pull → less tangled
      .force('link',
        d3.forceLink(edges)
          .id((d) => d.id)
          .distance(75)
          .strength(0.28)
      )
      // Stronger repulsion with distance cut-off
      .force('charge',
        d3.forceManyBody()
          .strength(-240)
          .distanceMin(8)
          .distanceMax(480)
      )
      // Gentle horizontal centering
      .force('x', d3.forceX(W / 2).strength(0.04))
      // Stronger vertical gravity per level band
      .force('y',
        d3.forceY((d) => (LEVEL_Y[levelOf(d)] ?? 0.9) * H).strength(0.80)
      )
      // Label-friendly collision radius (node radius + padding)
      .force('collide',
        d3.forceCollide()
          .radius((d) => nodeR(d) + 20)
          .strength(0.72)
      )
      .alphaDecay(0.024)      // slightly slower cool-down → smoother settling
      .on('tick', _tick)
  }

  function _tick() {
    if (linkSel) {
      linkSel.attr('d', (d) => curvePath(d, nodeR(d.target || {})))
    }
    if (nodeGrpSel) {
      nodeGrpSel.attr('transform', (d) => `translate(${d.x ?? 0},${d.y ?? 0})`)
    }
  }

  // ── highlightHop(hop) ─────────────────────────────────────────────────────
  function highlightHop(hop) {
    if (!nodeSel) return
    if (!hop?.source_id) { restore(); return }

    const hot = new Set([hop.source_id, hop.target_id])

    nodeSel
      .attr('opacity', (d) => (hot.has(d.id) ? 1 : 0.10))
      .attr('stroke', (d) =>
        d.id === hop.source_id ? '#f59e0b'      // amber: source
        : d.id === hop.target_id ? '#2e8b5a'    // sea-green: target
        : '#ffffff'
      )
      .attr('stroke-width', (d) => (hot.has(d.id) ? 3.5 : 1.8))

    nodeGrpSel?.selectAll('.lg-halo')
      .attr('opacity', (d) => (hot.has(d.id) ? 0.32 : 0.04))

    labelSel?.attr('opacity', (d) => (hot.has(d.id) ? 1 : 0.08))

    linkSel?.attr('stroke-opacity', (l) => {
      const s = l.source?.id ?? l.source
      const t = l.target?.id ?? l.target
      return (s === hop.source_id && t === hop.target_id) ||
             (s === hop.target_id && t === hop.source_id)
        ? 0.95 : 0.05
    }).attr('stroke-width', (l) => {
      const s = l.source?.id ?? l.source
      const t = l.target?.id ?? l.target
      return (s === hop.source_id && t === hop.target_id) ||
             (s === hop.target_id && t === hop.source_id)
        ? 3.0 : 1.6
    })
  }

  // ── hoverNode(nodeData) ───────────────────────────────────────────────────
  function hoverNode(d) {
    if (!nodeSel) return
    const nbrs = _adjacency[d.id]
    const hot  = nbrs ? new Set([...nbrs, d.id]) : new Set([d.id])

    nodeSel.attr('opacity', (n) => (hot.has(n.id) ? 1 : 0.10))

    nodeGrpSel?.selectAll('.lg-halo')
      .attr('opacity', (n) => (hot.has(n.id) ? 0.26 : 0.04))

    labelSel?.attr('opacity', (n) => (hot.has(n.id) ? 0.95 : 0.06))

    linkSel
      ?.attr('stroke-opacity', (l) => {
        const s = l.source?.id ?? l.source
        const t = l.target?.id ?? l.target
        return s === d.id || t === d.id ? 0.85 : 0.04
      })
      .attr('stroke-width', (l) => {
        const s = l.source?.id ?? l.source
        const t = l.target?.id ?? l.target
        return s === d.id || t === d.id ? 2.5 : 1.6
      })
  }

  // ── restore() ─────────────────────────────────────────────────────────────
  function restore() {
    if (!nodeSel) return
    nodeSel
      .attr('opacity', 1)
      .attr('stroke', '#ffffff')
      .attr('stroke-width', 1.8)
    nodeGrpSel?.selectAll('.lg-halo').attr('opacity', 0.11)
    labelSel?.attr('opacity', (d) => (d.deg >= 1 ? 0.90 : 0.44))
    linkSel?.attr('stroke-opacity', 0.40).attr('stroke-width', 1.6)
  }

  // ── destroy() ─────────────────────────────────────────────────────────────
  function destroy() {
    if (sim) { sim.stop(); sim = null }
    svg.selectAll('*').remove()
  }

  return { draw, highlightHop, hoverNode, restore, destroy }
}
