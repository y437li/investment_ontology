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
 *           relationships: [{source, source_id, edge_type, target, target_id, explanation, evidence:[...]}] }
 * First call may take ~20s (LLM); subsequent calls use cache.
 * Throws 503 if LLM is not configured.
 */
export function getCommunityNarrative(runId, communityId) {
  return service.get(`/api/themes/${runId}/communities/${communityId}/narrative`)
}

/**
 * Fetch temporal relevance scores for all themes in a run.
 * Returns { as_of_date, themes: [{community_id, relevance_score, state, recent_share, last_evidence_at, evidence_count}],
 *           main_themes: [{name, relevance_score, state, last_evidence_at, sub_theme_count}] }
 * Gracefully fails: callers should catch and treat as unavailable.
 */
export function getThemeRelevance(runId) {
  return service.get(`/api/themes/${runId}/relevance`)
}

/**
 * Fetch the profile for a single entity/node.
 * Returns { entity_id, name, entity_type, level, definition, first_seen_at, evidence_count, degree,
 *           why_present: [{direction, edge_type, other, explanation}],
 *           related_entities: [str] }
 */
export function getNodeProfile(runId, entityId) {
  return service.get(`/api/themes/${runId}/nodes/${entityId}/profile`)
}
