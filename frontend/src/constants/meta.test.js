// 静态维护表结构守卫：#230 批次 2/3 引入的术语表与配置说明表，每项字段必须完整
// （跨端 key 同步校验在 backend/tests/test_frontend_meta_sync.py，本文件只管前端侧结构）
import { describe, it, expect } from 'vitest'
import { TERMS } from './terms'
import { CONFIG_META } from './configMeta'

describe('TERMS 结构', () => {
  it('每项有非空 name 与 desc', () => {
    expect(Object.keys(TERMS).length).toBeGreaterThan(0)
    for (const [k, v] of Object.entries(TERMS)) {
      expect(v.name, `${k}.name 缺失`).toBeTruthy()
      expect(v.desc, `${k}.desc 缺失`).toBeTruthy()
    }
  })
})

describe('CONFIG_META 结构', () => {
  it('每项有非空 label/desc 且 precision 为数字', () => {
    expect(Object.keys(CONFIG_META).length).toBeGreaterThan(0)
    for (const [k, v] of Object.entries(CONFIG_META)) {
      expect(v.label, `${k}.label 缺失`).toBeTruthy()
      expect(v.desc, `${k}.desc 缺失`).toBeTruthy()
      expect(typeof v.precision, `${k}.precision 应为数字`).toBe('number')
    }
  })
})
