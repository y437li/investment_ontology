import service from './index.js'

/**
 * OI-6 R3b panel API — read-only multi-period panel artifacts.
 * All endpoints 404 when the run has no panel/ artifacts (single-point/legacy).
 */

/** Run-level panel summary (cached or recomputed live). */
export function getPanelSummary(runId) {
  return service.get(`/api/runs/${runId}/panel/summary`)
}

/** Cross-point theme lineage (panel/theme_lineage.json, schema 2.0). */
export function getPanelLineage(runId) {
  return service.get(`/api/runs/${runId}/panel/lineage`)
}

/** Per-company cross-point exposure trajectories (parquet -> JSON records). */
export function getPanelTrajectories(runId) {
  return service.get(`/api/runs/${runId}/panel/trajectories`)
}

/** Per-point out-of-sample validation panel (panel/validation_panel.json). */
export function getPanelValidation(runId) {
  return service.get(`/api/runs/${runId}/panel/validation`)
}
