import http from './http'

// 6.5 用例骨架 LLM 增强生成

// POST /api/agents/{id}/skeleton/generate → 按场景×接口生成用例骨架（draft，黄金答案/断言留空，Staff）
// 可选过滤：{ interface_ids?, scene_tags? }，不传 = 全量
export const generateSkeleton = (agentId, data) =>
  http.post(`/api/agents/${agentId}/skeleton/generate`, data || {})
