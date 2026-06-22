import service from './index.js'

/** Create a new pipeline run. */
export function createRun(asOfDate) {
  return service.post('/api/runs/create', { as_of_date: asOfDate })
}

/** Get the status of a run including which artifacts are present. */
export function getRunStatus(runId) {
  return service.get(`/api/runs/${runId}/status`)
}
