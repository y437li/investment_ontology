import service from './index.js'

/**
 * Fetch the main-theme hierarchy for a run.
 * Returns { run_id, sub_theme_count, main_themes: [{name, summary, sub_theme_ids, size}] }
 * Throws with status 404 if not yet built.
 */
export function getThemeHierarchy(runId) {
  return service.get(`/api/themes/${runId}/hierarchy`)
}

/**
 * Trigger an LLM build of the hierarchy (async, ~20s).
 * Returns the hierarchy on completion.
 */
export function buildThemeHierarchy(runId) {
  return service.post(`/api/themes/${runId}/hierarchy/build`)
}

/**
 * Fetch the narrative for a single community (sub-theme).
 * Returns { community_id, theme_name, narrative, reasoning_chain,
 *           relationships: [{source, edge_type, target, explanation, evidence:[...]}] }
 * First call may take ~20s (LLM); subsequent calls use cache.
 * Throws 503 if LLM is not configured.
 */
export function getCommunityNarrative(runId, communityId) {
  return service.get(`/api/themes/${runId}/communities/${communityId}/narrative`)
}
