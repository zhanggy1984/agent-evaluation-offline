import http from './http'

// GET /annotations/todo?agent_id → [{case_id, case_name, suite_id, agent_id, annotation_status, dimension_codes}]
// 待办：annotation_status in (draft, single)；owner 仅自己 agent
export const listTodo = (agentId) => http.get('/api/annotations/todo', { params: agentId ? { agent_id: agentId } : {} })

// GET /annotations/cases/{id} → [{annotator_id, annotator_name, dimension_code, level, note, created_at}]
export const listCaseAnnotations = (caseId) => http.get(`/api/annotations/cases/${caseId}`)

// POST /annotations/cases/{id} {dimension_code, level?, note?} → {case_id, annotation_status}
// 同人同维度覆盖更新；写后重算整体状态机
export const addAnnotation = (caseId, data) => http.post(`/api/annotations/cases/${caseId}`, data)
