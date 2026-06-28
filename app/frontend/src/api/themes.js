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

/**
 * Fetch factor-level breakdown for all themes in a run.
 * Returns {
 *   factor_levels: ["macro","industry","company","idiosyncratic"],
 *   themes: [{community_id, level_counts:{macro:n,...}, dominant_level, size, strength, substantive}],
 *   main_themes: [{name, level_counts, dominant_level, substantive_sub_count}]
 * }
 * Gracefully fails: callers should catch and treat as unavailable.
 */
export function getThemeLevels(runId) {
  return service.get(`/api/themes/${runId}/levels`)
}

/**
 * Union structural subgraph for a set of communities (a whole main theme).
 * Returns { nodes:[{id,label,entity_type,level}], edges:[{source,target,edge_type}], node_count, edge_count }
 */
export function getSubgraph(runId, communityIds) {
  return service.get(`/api/themes/${runId}/subgraph?communities=${communityIds.join(',')}`)
}

/**
 * ONE story for a whole main theme: connect-the-dots narrative + ordered 推演.
 * Returns { community_ids, narrative, reasoning_steps:[{order,claim,source,source_id,target,target_id,edge_type,provenance}], reasoning_chain, relationships }
 * First call ~20s (LLM) then cached; throws 503 if LLM unconfigured.
 */
export function getMainNarrative(runId, communityIds) {
  return service.get(`/api/themes/${runId}/main-narrative?communities=${communityIds.join(',')}`)
}

/**
 * Monthly walk-forward trajectories for a run.
 * Returns { months:[ISO...], themes:[{community_id, theme_name, size, emerged_month, momentum, trajectory:[{month,size,overlap}]}] }
 * Gracefully fails: callers should catch and treat as unavailable.
 */
export function getThemeTrajectories(runId) {
  return service.get(`/api/themes/${runId}/trajectories`)
}

/**
 * Full-text source behind an evidence chunk.
 * Returns { chunk_id, chunk_text, document_id, available_at, section_title,
 *           document:{title,source,source_url,published_at,document_type}, document_text }
 */
export function getChunkSource(runId, chunkId) {
  return service.get(`/api/themes/${runId}/chunks/${chunkId}`)
}

/**
 * EG-C: Per-company detail page data.
 * Returns {
 *   company_id, name, ticker, entity_type, as_of_date,
 *   themes: [{community_id, theme_name, theme_snapshot_id, exposure_score}],
 *   fundamentals: {available: bool, as_of_date, rows: [...], message?},
 *   financial_facts: [{metric_name, value, unit, period, direction, is_guidance,
 *                       confidence, evidence_chunk_id, source}]
 * }
 */
export function getCompanyDetail(runId, companyId) {
  return service.get(`/api/themes/${runId}/companies/${companyId}`)
}

/**
 * EG-C/D: Company-level evidence grouped by theme.
 * Returns [{
 *   community_id, theme_name, theme_snapshot_id, chunk_count,
 *   chunks: [{chunk_id, text, document_id, available_at, section_title, block_type,
 *              financial_fact: {metric_name,value,unit,period,direction,is_guidance} | null,
 *              fact_label: string | null,
 *              document: {title, source, document_type, published_at}}]
 * }]
 * Requires /api/provenance/materialize to have been run.
 */
export function getCompanyEvidence(runId, companyId) {
  return service.get(`/api/themes/${runId}/companies/${companyId}/evidence`)
}

/**
 * FI-F: List data-driven Event triggers present in projected_impacts.parquet.
 * Returns {
 *   as_of_date: str, trigger_count: int,
 *   triggers: [{ trigger_id, trigger_kind, label, company_count }]
 * }
 * Raises 404 if projected_impacts.parquet not yet built for this run.
 * PIT-clean; projections are HYPOTHETICAL.
 */
export function getProjectionTriggers(runId) {
  return service.get(`/api/themes/${runId}/projections/triggers`)
}

/**
 * FI-F: Ranked projected company impacts for a given Event trigger.
 * Returns {
 *   trigger_id, trigger_kind, trigger_label, as_of_date,
 *   impact_count: int, empty_reason: str|null,
 *   impacts: [{
 *     company_id, company_name,
 *     direction: +1|-1, strength: float, confidence: float,
 *     sign_blind: bool,   // true when direction is provisional (#110)
 *     path: [edge_id],
 *     path_graph: { nodes:[{id,label,entity_type,level}], edges:[{source,target,edge_type}] },
 *     evidence_chunk_ids: [chunk_id]
 *   }]
 * }
 * empty_reason is set (never null) when impact_count == 0.
 * PIT-clean; projections are HYPOTHETICAL.
 */
export function getProjections(runId, triggerId) {
  return service.get(`/api/themes/${runId}/projections?trigger=${encodeURIComponent(triggerId)}`)
}

/**
 * SENT-D: Management-sentiment panel data for a company.
 * Returns {
 *   company_id, as_of_date, available: bool, message: str|null,
 *   fused_tone_summary: {
 *     dominant_tone, dominant_tone_label, dominant_tone_severity,
 *     tone_counts: {positive,negative,neutral,hedged},
 *     has_conflict: bool, has_hedged: bool, reading_count: int
 *   },
 *   readings: [{
 *     fusion_id, fused_tone, fused_tone_label, fused_tone_severity,
 *     agreement, agreement_label, agreement_severity,
 *     fused_confidence, direction, confidence_tone, hedging, forward_stance,
 *     lm_direction, tone_positive, tone_negative, tone_uncertainty,
 *     lexicon_hits: {category: [matched_words]},
 *     available_at, evidence_chunk_id,
 *     chunk_text: str|null, section_title: str|null,
 *     document: {title, source, document_type, published_at}
 *   }]
 * }
 * Returns available=false (not 404) when artifact absent or no readings.
 */
export function getCompanySentiment(runId, companyId) {
  return service.get(`/api/themes/${runId}/companies/${companyId}/sentiment`)
}
