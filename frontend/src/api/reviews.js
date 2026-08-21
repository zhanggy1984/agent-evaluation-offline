import http from './http'

// 7.8 前置③：pending_human 人工复核（低置信 judge 判定回写）

// GET /reviews/pending → 待复核队列（Staff；留出集 owner 对 held-out 隐藏）
export const listPendingReviews = () => http.get('/api/reviews/pending')

// POST /reviews/review {run_id, case_id, dimension_code, action, score?, reason?}（Staff）
// JudgeTask 无单列 id，复合键定位；approve 采纳 / reject 带分改判 / 不带分放弃维度
export const reviewTask = (data) => http.post('/api/reviews/review', data)
