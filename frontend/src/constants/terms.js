// 术语表（#230 批次2）：各页面 hover tooltip 引用。
// 新增术语在此补充，勿散落到各页面写死文案。
export const TERMS = {
  L0: { name: 'L0', desc: '第一层看板——各 agent 的评分卡门禁墙：百分制评分与达标状态一览' },
  L1: { name: 'L1', desc: '单 agent 历次 run（评测）的评分趋势' },
  L2: { name: 'L2', desc: '同一 agent 不同版本的评分对比（Run A vs Run B）' },
  L3: { name: 'L3', desc: '单次 run 的逐用例评分明细，可导出 PDF' },
  'L3.5': { name: 'L3.5', desc: '门禁未达标用例的失败摘要：维度分低于达标分（target），或断言 / judge 判定不达标' },
  L4: { name: 'L4', desc: '单用例的判分依据：agent 最终回答、思考链全文与 judge 判定证据' },
  gate: { name: '门禁墙', desc: '对 agent 评分的准入达标检查：任一维度低于达标分即视为门禁失败，汇总在失败摘要' },
  gold: { name: '金标准', desc: '人工标定、作为判分事实依据的用例（golden），judge 以此比对判定' },
  baseline: { name: '基线', desc: '最近一次 run 的评分基准，用于对比当前分数（含各接口维度达标情况）' },
  target: { name: '达标分', desc: '该维度需达到的分数阈值（0-100 百分制）。judge 维度（事实性/思考链）精度为 20 分档，仅 0/20/40/60/80/100 有效——85/90/95 门禁等价 80，75/70 等价 60' },
  knowledge: { name: '知识版本', desc: '评测时 gq 应用侧知识库版本（库级文档时间戳锚，SSE meta 回填 env_snapshot）。与线上当前知识版本对比，判断「评测态/线上态」是否一致' },
  app_cache: { name: '应用缓存命中', desc: 'gq 应用层问答缓存命中 case 数（usage.cached=True）。区别于 DeepSeek 上下文缓存 prompt_cache_hit_tokens——应用缓存是语义级问答去重，命中代表相同问题复用历史答案' },
}
