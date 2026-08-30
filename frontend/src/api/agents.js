import http from './http'

// ---- agent 基本信息 ----
// GET /agents → [{id, name, base_url, adapter_type, adapter_config, contract_version, owner_id, enabled, auth_configured}]
export const listAgents = () => http.get('/api/agents')

// POST /agents {name, base_url, adapter_type, adapter_config?, contract_version?, owner_id?}
export const createAgent = (data) => http.post('/api/agents', data)

// GET /agents/{id}
export const getAgent = (id) => http.get(`/api/agents/${id}`)

// PUT /agents/{id} {name?, base_url?, adapter_config?, contract_version?, enabled?, owner_id?}
// owner_id 显式传 null 可清除指派
export const updateAgent = (id, data) => http.put(`/api/agents/${id}`, data)

// DELETE /agents/{id} → 停用（不物理删）
export const disableAgent = (id) => http.delete(`/api/agents/${id}`)

// PUT /agents/{id}/auth {secrets: {username, password, ...}}
export const setAgentAuth = (id, secrets) => http.put(`/api/agents/${id}/auth`, { secrets })

// POST /agents/{id}/probe?suite_id&interface_id → {ok, interfaces: [ProbeResult]}
// 真实调 LLM，前端触发前二次确认
export const probeAgent = (id, params) => http.post(`/api/agents/${id}/probe`, null, { params })

// ---- 快捷接入（Q7 wizard） ----
// POST /agents/{id}/adapter {manifest} → 确认 v2 manifest，服务端权威生成 adapter 落库（Q2）
export const confirmAdapter = (id, manifest) => http.post(`/api/agents/${id}/adapter`, { manifest })

// POST /agents/{id}/skeleton {probe_input?} → 用例骨架（Q5，不落库；probe_input 冒烟成功值 merge）
export const generateSkeleton = (id, probeInput) =>
  http.post(`/api/agents/${id}/skeleton`, { probe_input: probeInput })

// POST /agents/{id}/probe {input} → 显式探测输入冒烟（Q4：文件型 file_path 也走这里）
export const probeAgentInput = (id, input) => http.post(`/api/agents/${id}/probe`, { input })

// ---- 接口（agent_interface） ----
export const listInterfaces = (id) => http.get(`/api/agents/${id}/interfaces`)

// POST /agents/{id}/interfaces {name, path, method, contract_type, contract_version, retryable}
export const createInterface = (id, data) => http.post(`/api/agents/${id}/interfaces`, data)

// PUT /agents/{id}/interfaces/{iid}
export const updateInterface = (id, iid, data) => http.put(`/api/agents/${id}/interfaces/${iid}`, data)

// DELETE /agents/{id}/interfaces/{iid} → 停用
export const deleteInterface = (id, iid) => http.delete(`/api/agents/${id}/interfaces/${iid}`)

// ---- 权重（agent 级默认 + interface 覆盖） ----
// GET/PUT /agents/{id}/weights {weights: {dim: number}}
export const getAgentWeights = (id) => http.get(`/api/agents/${id}/weights`)
export const setAgentWeights = (id, weights) => http.put(`/api/agents/${id}/weights`, { weights })

export const getInterfaceWeights = (id, iid) => http.get(`/api/agents/${id}/interfaces/${iid}/weights`)
export const setInterfaceWeights = (id, iid, weights) =>
  http.put(`/api/agents/${id}/interfaces/${iid}/weights`, { weights })

// ---- 阈值（双签） ----
// GET /agents/{id}/interfaces/{iid}/targets → [{dimension_code, target_score, approval_status, ...}]
export const getTargets = (id, iid) => http.get(`/api/agents/${id}/interfaces/${iid}/targets`)

// PUT /agents/{id}/interfaces/{iid}/targets {target_scores: {dim: number}}
export const setTargets = (id, iid, targetScores) =>
  http.put(`/api/agents/${id}/interfaces/${iid}/targets`, { target_scores: targetScores })

// ---- 契约发现 / 同步（scaffold） ----
// POST /agents/{id}/discover → {ok, agent, contract_version, scenes, interfaces, added/existing/missing/auxiliary}
export const discoverAgent = (id) => http.post(`/api/agents/${id}/discover`)

// POST /agents/{id}/interfaces/sync {interfaces: [{name, path, method, contract_type}]} → {created, skipped, renamed}
export const syncInterfaces = (id, interfaces) =>
  http.post(`/api/agents/${id}/interfaces/sync`, { interfaces })

// ---- 场景清单 ----
// GET /agents/{id}/scenes → [{id, scene_tag, description}]
export const listScenes = (id) => http.get(`/api/agents/${id}/scenes`)

// POST /agents/{id}/scenes {scenes: [{tag, description}]}
export const addScenes = (id, scenes) => http.post(`/api/agents/${id}/scenes`, { scenes })

// ---- 用例场景标签（5.2c 打标复用） ----
// GET/POST /cases/{id}/scenes
export const listCaseScenes = (caseId) => http.get(`/api/cases/${caseId}/scenes`)
export const addCaseScenes = (caseId, sceneTags) =>
  http.post(`/api/cases/${caseId}/scenes`, { scene_tags: sceneTags })
