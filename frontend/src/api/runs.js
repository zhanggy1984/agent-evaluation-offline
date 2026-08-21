import http from './http'

// GET /runs?agent_id&limit&offset → [{id, agent_id, suite_id, version, status, ...}]
export const listRuns = (params = {}) => http.get('/api/runs', { params })

// GET /runs/{id} → run 详情
export const getRun = (id) => http.get(`/api/runs/${id}`)

// POST /runs {agent_id, suite_id, version}（admin 触发）
export const createRun = (data) => http.post('/api/runs', data)

// POST /runs/{id}/cancel（staff，非终态）
export const cancelRun = (id) => http.post(`/api/runs/${id}/cancel`)

// POST /runs/{id}/rerun（staff，复用冻结配置）
export const rerunRun = (id) => http.post(`/api/runs/${id}/rerun`)

// GET /runs/{id}/results → L3 用例明细
export const listRunResults = (runId) => http.get(`/api/runs/${runId}/results`)

// GET /runs/{id}/failures → L3.5 门禁失败摘要（viewer 可达）
export const listRunFailures = (runId) => http.get(`/api/runs/${runId}/failures`)

// GET /runs/{id}/results/{resultId}/evidence → L4 证据（viewer 403）
export const getResultEvidence = (runId, resultId) =>
  http.get(`/api/runs/${runId}/results/${resultId}/evidence`)
