import http from './http'

// GET /suites/{id}/cases → [{id, name, interface_id, interface_name, input_type, status, is_gold, is_held_out, annotation_status, scene_tags, updated_at}]
export const listCases = (suiteId) => http.get(`/api/suites/${suiteId}/cases`)

// POST /suites/{id}/cases
export const createCase = (suiteId, data) => http.post(`/api/suites/${suiteId}/cases`, data)

// GET /cases/{id}（含 input/expected/assertions/metrics 全量）
export const getCase = (id) => http.get(`/api/cases/${id}`)

// PUT /cases/{id}（expected/assertions 字段级权限：owner 改自己 agent 403）
export const updateCase = (id, data) => http.put(`/api/cases/${id}`, data)

// DELETE /cases/{id} → 作废（status=invalidated 软删）
export const invalidateCase = (id) => http.delete(`/api/cases/${id}`)
