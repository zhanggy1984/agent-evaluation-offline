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
  target: { name: '达标分', desc: '该维度需达到的分数阈值（0-100 百分制）' },
}
