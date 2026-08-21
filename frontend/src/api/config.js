import http from './http'

// GET /config/global → [{key, value, scope, is_hot, updated_at}]
export const getGlobalConfig = () => http.get('/api/config/global')

// PUT /config/global {values: {key: value}} → 未知 key 400；is_hot=false 拒绝改
export const putGlobalConfig = (values) =>
  http.put('/api/config/global', { values })

// ---- 插件字典 ----
// GET/POST /config/model-prices → [{model, input_price, output_price, cache_hit_price, effective_from}]
export const listModelPrices = () => http.get('/api/config/model-prices')
export const createModelPrice = (data) => http.post('/api/config/model-prices', data)

// GET/POST /config/assertion-ops → [{op, class_path}]
export const listAssertionOps = () => http.get('/api/config/assertion-ops')
export const createAssertionOp = (data) => http.post('/api/config/assertion-ops', data)

// GET/POST/PUT/DELETE /config/rubrics → [{id, dimension_code, interface_id, version, template}]
export const listRubrics = (dimensionCode) =>
  http.get('/api/config/rubrics', { params: dimensionCode ? { dimension_code: dimensionCode } : {} })
export const createRubric = (data) => http.post('/api/config/rubrics', data)
export const updateRubric = (id, data) => http.put(`/api/config/rubrics/${id}`, data)
export const deleteRubric = (id) => http.delete(`/api/config/rubrics/${id}`)
