import http from './http'

// GET /dashboard/gate → L0 门禁墙：[{agent_id, agent_name, version, agent_score, pass_rate, ...}]
export const getGate = () => http.get('/api/dashboard/gate')

// GET /dashboard/trend?agent_id → L1 趋势：[{run_id, version, status, agent_score, ...}]
export const getTrend = (agentId) => http.get('/api/dashboard/trend', { params: { agent_id: agentId } })

// GET /dashboard/compare?a={runId}&b={runId} → L2 版本对比
export const getCompare = (a, b) => http.get('/api/dashboard/compare', { params: { a, b } })

// ---- 5.4 辅助面板（viewer 可读）----
// GET /dashboard/perf?agent_id → 性能面板：[{run_id, version, status, ttft_p50, ttft_p95, e2e_p50, e2e_p95, started_at}]
export const getPerf = (agentId) => http.get('/api/dashboard/perf', { params: { agent_id: agentId } })

// GET /dashboard/cost?agent_id → 成本面板：[{run_id, version, status, model, total_tokens, total_cost, started_at}]
export const getCost = (agentId) => http.get('/api/dashboard/cost', { params: { agent_id: agentId } })

// GET /dashboard/coverage?agent_id → 覆盖率：{interface_total, interface_covered, interface_rate, interface_blank[], scene_total, scene_covered, scene_rate, scene_blank[]}
export const getCoverage = (agentId) => http.get('/api/dashboard/coverage', { params: { agent_id: agentId } })

// ---- 6.3 基线对比（viewer 可读）----
// GET /dashboard/baseline?agent_id → {run:{run_id,version,status,agent_score,...}, is_gold_count, interfaces:[{interface_id,name,case_count,dims:[{code,score,target,gap,met}]}]}
export const getBaseline = (agentId) => http.get('/api/dashboard/baseline', { params: { agent_id: agentId } })
