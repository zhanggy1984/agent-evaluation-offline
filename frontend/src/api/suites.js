import http from './http'

// GET /suites?agent_id → [{id, agent_id, agent_name, name, description, case_count, created_at}]
export const listSuites = (agentId) => http.get('/api/suites', { params: agentId ? { agent_id: agentId } : {} })

// POST /suites {agent_id, name, description}
export const createSuite = (data) => http.post('/api/suites', data)

// PUT /suites/{id} {name?, description?}
export const updateSuite = (id, data) => http.put(`/api/suites/${id}`, data)

// DELETE /suites/{id}（有 case 409）
export const deleteSuite = (id) => http.delete(`/api/suites/${id}`)
