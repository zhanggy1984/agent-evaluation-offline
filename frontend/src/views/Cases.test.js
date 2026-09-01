// @vitest-environment jsdom
// P2-D16：标注待办 suite 定位回归——loadSuites 优先选中预置 curSuiteId（可非首个），
// 未预置回退首个 suite；annotateFromTodo 预置 curSuiteId 后 loadSuites 定位到 case 所在 suite。
import { describe, it, expect, beforeEach, vi } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import ElementPlus from 'element-plus'
import Cases from './Cases.vue'

const { isAdminMock } = vi.hoisted(() => ({ isAdminMock: { value: true } }))
vi.mock('pinia', () => ({ storeToRefs: () => ({ isAdmin: isAdminMock }) }))
vi.mock('../stores/auth', () => ({ useAuthStore: () => ({ role: 'admin' }) }))
vi.mock('../api/agents', () => ({
  listAgents: vi.fn(), listInterfaces: vi.fn(), listScenes: vi.fn(), addCaseScenes: vi.fn(),
}))
vi.mock('../api/suites', () => ({
  listSuites: vi.fn(), createSuite: vi.fn(), updateSuite: vi.fn(), deleteSuite: vi.fn(),
}))
vi.mock('../api/cases', () => ({
  listCases: vi.fn(), getCase: vi.fn(), createCase: vi.fn(), updateCase: vi.fn(), invalidateCase: vi.fn(),
}))
vi.mock('../api/uploads', () => ({ uploadFile: vi.fn() }))
vi.mock('../api/annotations', () => ({
  listTodo: vi.fn(), listCaseAnnotations: vi.fn(), addAnnotation: vi.fn(),
}))

import { listAgents, listInterfaces, listScenes } from '../api/agents'
import { listSuites } from '../api/suites'
import { listCases } from '../api/cases'
import { listTodo, listCaseAnnotations } from '../api/annotations'

const SUITES = [
  { id: 5, name: '首个 suite', case_count: 2 },
  { id: 9, name: '目标 suite', case_count: 1 },
]
const TODO = [{
  case_id: 31, case_name: '待办 case', suite_id: 9, agent_id: 1,
  annotation_status: 'none', dimension_codes: [],
}]
const CASE = { id: 31, name: '待办 case', input_type: 'text', input: {}, assertions: [], metrics: {} }

beforeEach(() => {
  vi.clearAllMocks()
  listAgents.mockResolvedValue([{ id: 1, name: 'agent-a' }])
  listSuites.mockResolvedValue(SUITES)
  listCases.mockResolvedValue([CASE])
  listTodo.mockResolvedValue(TODO)
  listCaseAnnotations.mockResolvedValue([])
  listInterfaces.mockResolvedValue([])
  listScenes.mockResolvedValue([])
})

describe('Cases 标注待办 suite 定位（P2-D16）', () => {
  it('mount 无预置 suite → 回退选中首个 suite', async () => {
    const wrapper = mount(Cases, { global: { plugins: [ElementPlus] } })
    await flushPromises()
    expect(listSuites).toHaveBeenCalledWith(1)
    expect(listCases).toHaveBeenCalledWith(5)
  })

  it('去标注：预置 curSuiteId → 选中 case 所在 suite（非首个）+ 打开标注', async () => {
    const wrapper = mount(Cases, { global: { plugins: [ElementPlus] } })
    await flushPromises()
    // 切到「标注待办」tab（el-tab-pane 懒渲染，点击后才挂载表格）
    await wrapper.findAll('.el-tabs__item')[1].trigger('click')
    await flushPromises()
    expect(wrapper.text()).toContain('待办 case')
    // 点「去标注」
    const btn = wrapper.findAll('button').find((b) => b.text().includes('去标注'))
    expect(btn, '去标注按钮应存在').toBeTruthy()
    await btn.trigger('click')
    await flushPromises()
    // 命中 suite 9（非首个）而非回退 5；标注 dialog 打开并回显 case
    expect(listCases).toHaveBeenCalledWith(9)
    expect(wrapper.text()).toContain('待办 case')
  })
})
