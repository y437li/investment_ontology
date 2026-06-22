import service from './index.js'

/**
 * Fetch an allowlisted artifact for a given run.
 * Artifact names: graph.json, communities.json, theme_snapshots.json,
 * theme_lineage.json, report.md, theme_metrics.parquet,
 * company_theme_exposure.parquet, validation/validation.csv
 *
 * Parquet files are returned as JSON records by the backend.
 */
export function getArtifact(runId, artifactName) {
  return service.get(`/api/artifacts/${runId}/${artifactName}`)
}

export function getGraphJson(runId) {
  return getArtifact(runId, 'graph.json')
}

export function getCommunitiesJson(runId) {
  return getArtifact(runId, 'communities.json')
}

export function getThemeSnapshots(runId) {
  return getArtifact(runId, 'theme_snapshots.json')
}

export function getThemeLineage(runId) {
  return getArtifact(runId, 'theme_lineage.json')
}

export function getThemeMetrics(runId) {
  return getArtifact(runId, 'theme_metrics.parquet')
}

export function getCompanyThemeExposure(runId) {
  return getArtifact(runId, 'company_theme_exposure.parquet')
}

export function getValidationCsv(runId) {
  return getArtifact(runId, 'validation/validation.csv')
}

export function getReport(runId) {
  return getArtifact(runId, 'report.md')
}
