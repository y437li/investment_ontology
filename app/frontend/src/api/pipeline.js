import service from './index.js'

/** POST /api/data/import */
export function importData(runId, documentsDir, sourceManifestPath) {
  return service.post('/api/data/import', {
    run_id: runId,
    documents_dir: documentsDir,
    source_manifest_path: sourceManifestPath
  })
}

/** POST /api/data/clean */
export function cleanData(runId) {
  return service.post('/api/data/clean', { run_id: runId })
}

/** POST /api/data/chunk */
export function chunkData(runId) {
  return service.post('/api/data/chunk', { run_id: runId })
}

/** POST /api/extraction/run */
export function runExtraction(runId) {
  return service.post('/api/extraction/run', { run_id: runId })
}

/** POST /api/extraction/resolve */
export function resolveEntities(runId) {
  return service.post('/api/extraction/resolve', { run_id: runId })
}

/** POST /api/graph/build */
export function buildGraph(runId) {
  return service.post('/api/graph/build', { run_id: runId })
}

/** POST /api/themes/discover */
export function discoverThemes(runId) {
  return service.post('/api/themes/discover', { run_id: runId })
}

/** POST /api/exposure/compute */
export function computeExposure(runId, includeWeakSignals = false) {
  return service.post('/api/exposure/compute', {
    run_id: runId,
    include_weak_signals: includeWeakSignals
  })
}

/** POST /api/discovery/freeze */
export function freezeDiscovery(runId) {
  return service.post('/api/discovery/freeze', { run_id: runId })
}

/** POST /api/validation/run */
export function runValidation(runId) {
  return service.post('/api/validation/run', { run_id: runId })
}

/** POST /api/report/generate */
export function generateReport(runId) {
  return service.post('/api/report/generate', { run_id: runId })
}
