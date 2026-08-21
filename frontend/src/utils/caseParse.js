// #230 批次 3a：新建用例 JSON 引导与字段级校验（纯函数，供 Cases.vue 调用 + vitest 单测）
// 值带【改】标记处为需改写字段，结构可照抄
export const EXAMPLES = {
  text: {
    input: {
      content: '【改】填写发送给 agent 的文本问题，例如：请查询订单 20260101 的物流状态，并说明预计送达时间。',
      seed: 1,
    },
    expected: {
      golden_answer: '【改】填写该用例期望的标准答案（judge 据此比对判定），例如：订单 20260101 已发货，预计 2 天内送达。',
      reference_docs: '【改】可选：提供事实依据原文（markdown 文本），供 factuality 判定比对。',
    },
    assertions: [
      { op: 'field_nonempty', args: { path: 'answer' }, dimension: 'completeness' },
    ],
    metrics: { factuality: { enabled: true } },
  },
  file: {
    input: {
      file_path: '/app/uploads/【改】上传附件后自动填入 file_path，无需手填',
      seed: 1,
    },
    expected: {
      golden_answer: '【改】填写基于附件内容的标准答案，例如：乙方为 XX 科技有限公司，合同金额为 100 万元。',
    },
    assertions: [
      { op: 'field_nonempty', args: { path: 'answer' }, dimension: 'completeness' },
    ],
    metrics: { factuality: { enabled: true } },
  },
  conversation: {
    input: {
      content: [
        { role: 'user', content: '【改】第一轮：你好，我想退订订阅服务。' },
        { role: 'assistant', content: '【改】可选的中间轮助手回复。' },
        { role: 'user', content: '【改】最后一轮：我的账号是 10086。' },
      ],
      seed: 1,
    },
    expected: {
      golden_answer: '【改】填写整个多轮对话期望的最终答案。',
    },
    assertions: [
      { op: 'field_nonempty', args: { path: 'answer' }, dimension: 'completeness' },
    ],
    metrics: { factuality: { enabled: true } },
  },
}

const DEFAULT_TEXT = { assertions: '[]', input: '{}', expected: '{}', metrics: '{}' }

// 字段级 JSON 校验：返回 { ok: true, value } 或 { ok: false, error }；空 text 用类型默认（不写组件副作用）
export function parseCaseField(key, text) {
  try {
    return { ok: true, value: JSON.parse(text || (DEFAULT_TEXT[key] ?? '{}')) }
  } catch (e) {
    return { ok: false, error: 'JSON 格式非法，请检查后重试' }
  }
}

// 按输入类型生成 4 个字段的示例 JSON 字符串（未知类型兜底 text）
export function buildCaseExample(inputType) {
  const ex = EXAMPLES[inputType] || EXAMPLES.text
  return {
    input: JSON.stringify(ex.input, null, 2),
    expected: JSON.stringify(ex.expected, null, 2),
    assertions: JSON.stringify(ex.assertions, null, 2),
    metrics: JSON.stringify(ex.metrics, null, 2),
  }
}
