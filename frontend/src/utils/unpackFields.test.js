import { describe, it, expect } from 'vitest'
import { unpackFields, collectInput } from './unpackFields'

describe('unpackFields', () => {
  it('扁平路径 → 嵌套空模板 + 路径元信息', () => {
    const { paths, template } = unpackFields(['content', 'a.b', 'file_path'])
    expect(template).toEqual({ content: '', a: { b: '' }, file_path: '' })
    expect(paths.map((p) => p.path)).toEqual(['a.b', 'content', 'file_path'])
    expect(paths.map((p) => p.leaf)).toEqual(['b', 'content', 'file_path'])
  })

  it('冲突路径（a 与 a.b 并存）中间层提升为 dict，不丢嵌套字段', () => {
    const { template } = unpackFields(['a', 'a.b'])
    expect(template).toEqual({ a: { b: '' } })
  })

  it('空数组 / 缺省安全', () => {
    expect(unpackFields().template).toEqual({})
    expect(unpackFields([]).paths).toEqual([])
  })
})

describe('collectInput', () => {
  it('扁平值表 → 嵌套对象', () => {
    expect(collectInput(['content', 'a.b'], { content: '你好', 'a.b': 'x' }))
      .toEqual({ content: '你好', a: { b: 'x' } })
  })

  it('空串/缺省跳过（骨架空串补全，不覆盖）', () => {
    expect(collectInput(['content', 'a.b'], { content: '', 'a.b': 'x' }))
      .toEqual({ a: { b: 'x' } })
    expect(collectInput(['content'], {})).toEqual({})
  })

  it('凭证点号键 → 嵌套对象（engine._get_path 按点号遍历 secrets）', () => {
    expect(collectInput(['credentials.token', 'username'], { 'credentials.token': 'abc', username: 'u' }))
      .toEqual({ credentials: { token: 'abc' }, username: 'u' })
  })
})
