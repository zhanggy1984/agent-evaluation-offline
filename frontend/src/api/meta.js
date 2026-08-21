import http from './http'

// 6.4 元评测与数据清理

// GET /api/meta/drift → 漂移历史序列（维度过滤可选）
export const getDrift = (dimensionCode) =>
  http.get('/api/meta/drift', { params: dimensionCode ? { dimension_code: dimensionCode } : {} })

// POST /api/meta/drift/check → 手动触发漂移检测（重判金标准，烧 judge token，Staff）
// 粒度可选：{ dimension_code?, agent_id? }，不传 → 全量重判
export const triggerDrift = (data) => http.post('/api/meta/drift/check', data || {})

// POST /api/meta/cleanup → 手动触发数据清理（保留 N 次分批删，Staff）
export const triggerCleanup = () => http.post('/api/meta/cleanup')
