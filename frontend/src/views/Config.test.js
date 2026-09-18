// @vitest-environment jsdom
// 配置中心「值」列渲染回归，守两个已发生过的缺陷：
//  1. 无 CONFIG_META 契约的 number 项以 0 位小数渲染 —— 库里的 0.7 / 0.5 / 0.8 显示成 1；
//  2. 单位后缀 <span> 用 v-if 另起一条链（span 自己起头、el-input 接 v-else-if、textarea 收 v-else），
//     致链尾 v-else 对**所有非 text 型**项为真 ⇒ bool 行与无 unit 的 number 行各多渲染一个空 JSON 文本域。
//     —— 第 2 条只有挂载测试能覆盖（是模板链问题，不是纯函数问题），故本文件按仓内 views/*.test.js
//     惯例挂载组件，而非抽 utils 单测。
import { describe, it, expect, beforeEach, vi } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import ElementPlus from 'element-plus'
import Config from './Config.vue'

const { isAdminMock } = vi.hoisted(() => ({ isAdminMock: { value: true } }))
vi.mock('pinia', () => ({ storeToRefs: () => ({ isAdmin: isAdminMock }) }))
vi.mock('../stores/auth', () => ({ useAuthStore: () => ({ role: 'admin' }) }))
vi.mock('../api/config', () => ({ getGlobalConfig: vi.fn(), putGlobalConfig: vi.fn() }))

import { getGlobalConfig } from '../api/config'

// 最小行集，scope 全给 run 便于单 tab 内断言。key 的真实取值与库内一致（2026-09-17 现场）。
const ROWS = [
  // 无契约 + 小数：缺陷 1 的现场
  { key: 'judge_review_confidence', value: 0.7, scope: 'run', is_hot: true, updated_at: 't' },
  { key: 'alarm.error_ratio', value: 0.5, scope: 'run', is_hot: false, updated_at: 't' },
  // 有契约 + precision: 2：对照组，证明改动没波及契约项
  { key: 'judge_na_threshold', value: 0.3, scope: 'run', is_hot: true, updated_at: 't' },
  // 无契约 + 整数：推断结果必须仍是整数
  { key: 'human_review_timeout', value: 86400, scope: 'run', is_hot: true, updated_at: 't' },
  // 有契约 + 有 unit：单位后缀要正常显示
  { key: 'case_timeout', value: 120, scope: 'run', is_hot: true, updated_at: 't' },
  // bool：缺陷 2 的现场
  { key: 'judge_cache_enabled', value: false, scope: 'run', is_hot: true, updated_at: 't' },
  // null → JSON 分支：本该有 textarea，用于防「修过头」
  { key: 'run_timeout', value: null, scope: 'run', is_hot: true, updated_at: 't' },
  // 字符串：走 el-input，不该有 textarea
  { key: 'judge_llm.base_url', value: 'https://api.deepseek.com', scope: 'run', is_hot: false, updated_at: 't' },
]

const mountConfig = async () => {
  getGlobalConfig.mockResolvedValue(ROWS.map((r) => ({ ...r })))
  const wrapper = mount(Config, { global: { plugins: [ElementPlus] } })
  await flushPromises()
  return wrapper
}

// 列序：0 配置项 / 1 说明 / 2 值 / 3 热生效 / 4 更新时间（与页面现场一致）
const valueCell = (wrapper, key) => {
  const tr = wrapper.findAll('.el-table__body tr').find((r) => r.findAll('td')[0].text().trim() === key)
  if (!tr) throw new Error(`行未渲染：${key}`)
  return tr.findAll('td')[2]
}

describe('Config 值列渲染', () => {
  let wrapper
  beforeEach(async () => {
    vi.clearAllMocks()
    wrapper = await mountConfig()
  })

  it('无契约 number 项按值推断小数位，不再四舍五入成整数', () => {
    expect(valueCell(wrapper, 'judge_review_confidence').find('input').element.value).toBe('0.7')
    expect(valueCell(wrapper, 'alarm.error_ratio').find('input').element.value).toBe('0.5')
  })

  it('有契约项仍用契约 precision（对照组，precision: 2 → 补末尾零）', () => {
    expect(valueCell(wrapper, 'judge_na_threshold').find('input').element.value).toBe('0.30')
  })

  it('无契约整数项推断为 0 位，不引入小数', () => {
    expect(valueCell(wrapper, 'human_review_timeout').find('input').element.value).toBe('86400')
  })

  it('非 text 型项不再多渲染空 JSON 文本域', () => {
    // bool：开关在，文本域不在
    expect(valueCell(wrapper, 'judge_cache_enabled').findAll('.el-switch')).toHaveLength(1)
    expect(valueCell(wrapper, 'judge_cache_enabled').findAll('textarea')).toHaveLength(0)
    // 无 unit 的 number：输入框 1 个，文本域 0 个
    expect(valueCell(wrapper, 'human_review_timeout').findAll('input')).toHaveLength(1)
    expect(valueCell(wrapper, 'human_review_timeout').findAll('textarea')).toHaveLength(0)
    // text 项走 el-input，同样没有文本域
    expect(valueCell(wrapper, 'judge_llm.base_url').findAll('textarea')).toHaveLength(0)
  })

  it('有 unit 的 number 项仍显示单位后缀（后缀移位后未丢）', () => {
    // 注：本条在修前也绿 —— 修前 <span> 在链中间同样渲染，只是顺带把链尾 v-else 打开了。
    // 它是「移位没把后缀弄丢」的守卫，不是缺陷的判别条。
    expect(valueCell(wrapper, 'case_timeout').find('.unit-suffix').text()).toBe('秒')
  })

  it('null 值仍走 JSON 文本域（防修过头）', () => {
    expect(valueCell(wrapper, 'run_timeout').findAll('textarea')).toHaveLength(1)
    expect(valueCell(wrapper, 'run_timeout').findAll('.el-switch')).toHaveLength(0)
  })
})
