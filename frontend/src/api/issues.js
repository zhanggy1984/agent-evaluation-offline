import http from './http'

// ---- 问题跟踪（5.4，viewer 只读 / staff 可写）----
// GET /issues?agent_id&status&severity → [{id, agent_id, agent_name, title, severity, status, created_at, updated_at, ...}]
export const listIssues = (params) => http.get('/api/issues', { params })

// POST /issues {agent_id, title, description?, severity?, related_case_id?, related_dimension?} → {id, status}
export const createIssue = (data) => http.post('/api/issues', data)

// PUT /issues/{id} {title?, description?, severity?, related_case_id?, related_dimension?}
export const updateIssue = (id, data) => http.put(`/api/issues/${id}`, data)

// POST /issues/{id}/transition {to} → {id, status}（非法流转后端 400 兜底）
export const transitionIssue = (id, to) => http.post(`/api/issues/${id}/transition`, { to })
