import { describe, it, expect } from 'vitest'
import { EXAMPLES, parseCaseField, buildCaseExample } from './caseParse'

const FIELDS = ['input', 'expected', 'assertions', 'metrics']
const TYPES = ['text', 'file', 'conversation']

describe('EXAMPLES 结构', () => {
  it('三种输入类型各含 4 个字段且值可 JSON 序列化', () => {
    for (const type of TYPES) {
      for (const f of FIELDS) {
        expect(EXAMPLES[type], `${type}.${f}`).toHaveProperty(f)
        expect(() => JSON.stringify(EXAMPLES[type][f]), `${type}.${f} 不可序列化`).not.toThrow()
      }
    }
  })
})

describe('parseCaseField', () => {
  it('合法 JSON 返回 { ok, value }', () => {
    expect(parseCaseField('input', '{"content":"hi"}')).toEqual({ ok: true, value: { content: 'hi' } })
  })
  it('非法 JSON 返回 { ok: false, error }', () => {
    const r = parseCaseField('input', '{bad json')
    expect(r.ok).toBe(false)
    expect(typeof r.error).toBe('string')
  })
  it('空 text 用类型默认（assertions→[]，其余→{}）', () => {
    expect(parseCaseField('assertions', '').value).toEqual([])
    expect(parseCaseField('input', '').value).toEqual({})
    expect(parseCaseField('metrics', undefined).value).toEqual({})
  })
})

describe('buildCaseExample', () => {
  it('返回 4 字段可 parse 的 JSON 字符串', () => {
    for (const type of TYPES) {
      const s = buildCaseExample(type)
      for (const f of FIELDS) {
        expect(() => JSON.parse(s[f]), `${type}.${f} 非法 JSON`).not.toThrow()
      }
    }
  })
  it('含【改】引导标记', () => {
    expect(buildCaseExample('text').input).toContain('【改】')
  })
  it('未知类型兜底 text', () => {
    expect(buildCaseExample('unknown').input).toContain('content')
  })
})
