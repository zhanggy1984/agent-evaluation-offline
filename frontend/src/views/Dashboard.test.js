// @vitest-environment jsdom
// 看板 agent 下钻联动：点门禁墙/顶部下拉选 agent 后评测记录只显示该 agent 的 run（loadRuns 带 agent_id），
// 未选 agent 恢复全量；评测记录分页 PAGE_SIZE=5 生效（超过 5 条才显示分页器）。
import { describe, it, expect, beforeEach, vi } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import ElementPlus from 'element-plus'
import Dashboard from './Dashboard.vue'

const { isAdminMock } = vi.hoisted(() => ({ isAdminMock: { value: true } }))
vi.mock('pinia', () => ({ storeToRefs: () => ({ isAdmin: isAdminMock }) }))
vi.mock('../stores/auth', () => ({ useAuthStore: () => ({ role: 'admin' }) }))
vi.mock('../api/agents', () => ({ listAgents: vi.fn() }))
vi.mock('../api/suites', () => ({ listSuites: vi.fn() }))
vi.mock('../api/runs', () => ({
  listRuns: vi.fn(), createRun: vi.fn(), cancelRun: vi.fn(), rerunRun: vi.fn(),
  listRunResults: vi.fn(), listRunFailures: vi.fn(), getResultEvidence: vi.fn(),
}))
vi.mock('../api/dashboard', () => ({
  getGate: vi.fn(), getTrend: vi.fn(), getCompare: vi.fn(),
  getPerf: vi.fn(), getCost: vi.fn(), getCoverage: vi.fn(), getBaseline: vi.fn(),
}))
vi.mock('../api/config', () => ({ listModelPrices: vi.fn() }))
vi.mock('../api/exports', () => ({ createExport: vi.fn(), downloadExport: vi.fn() }))

import { listAgents } from '../api/agents'
import { listSuites } from '../api/suites'
import { listRuns } from '../api/runs'
import { getGate, getTrend, getCompare, getPerf, getCost, getCoverage, getBaseline } from '../api/dashboard'
import { listModelPrices } from '../api/config'

// 覆盖 tab 模板访问 coverage.interface_blank.length 等字段，mock 需为对象而非数组（[] 会导致渲染 undefined.length）
const COVERAGE = {
  interface_rate: 1, interface_covered: 10, interface_total: 10, interface_blank: [],
  scene_rate: 1, scene_covered: 5, scene_total: 5, scene_blank: [],
}

const AGENTS = [{ id: 1, name: 'agent-a' }, { id: 2, name: 'agent-b' }]
const GATE = [
  { agent_id: 1, agent_name: 'agent-a', version: '0.2.0', score: 90, pass_rate: 0.9, total_cases: 10, pass_cases: 9 },
  { agent_id: 2, agent_name: 'agent-b', version: '0.2.0', score: 80, pass_rate: 0.8, total_cases: 10, pass_cases: 8 },
]
const RUNS = [1, 2, 3, 4, 5, 6].map((id) => ({
  id, agent_id: id % 2 ? 1 : 2, version: '0.2.0', status: 'completed',
  agent_score: 90, pass_rate: 1, started_at: '2026-09-01T00:00:00Z',
}))

function mountDashboard() {
  return mount(Dashboard, {
    global: {
      plugins: [ElementPlus],
      stubs: { EChart: true, TermTip: true },
    },
  })
}

beforeEach(() => {
  vi.clearAllMocks()
  listAgents.mockResolvedValue(AGENTS)
  listSuites.mockResolvedValue([])
  getGate.mockResolvedValue(GATE)
  getTrend.mockResolvedValue([])
  getPerf.mockResolvedValue([])
  getCost.mockResolvedValue([])
  getCoverage.mockResolvedValue(COVERAGE)
  getBaseline.mockResolvedValue({})
  getCompare.mockResolvedValue(null)
  listModelPrices.mockResolvedValue([])
})

describe('看板 agent 下钻联动评测记录', () => {
  it('mount 未选 agent → loadRuns 全量（不带 agent_id）', async () => {
    listRuns.mockResolvedValue(RUNS)
    const wrapper = mountDashboard()
    await flushPromises()
    expect(listRuns).toHaveBeenCalledWith({ limit: 200 })
  })

  it('点门禁墙 gate-card → 评测记录联动 loadRuns 带 agent_id', async () => {
    listRuns.mockResolvedValue(RUNS)
    const wrapper = mountDashboard()
    await flushPromises()
    const cards = wrapper.findAll('.gate-card')
    expect(cards.length).toBe(2)
    await cards[0].trigger('click') // agent-a (agent_id=1)
    await flushPromises()
    // loadRuns 主列表调用（另有截断探测 {limit:1,offset:200,...}，故用 toHaveBeenCalledWith 而非 LastCalled）
    expect(listRuns).toHaveBeenCalledWith({ limit: 200, agent_id: 1 })
  })

  it('顶部下拉切换 agent → onAgentChange 联动 loadRuns 带 agent_id', async () => {
    listRuns.mockResolvedValue(RUNS)
    const wrapper = mountDashboard()
    await flushPromises()
    // 第一个 ElSelect 即顶部 agent 下拉（compare/trigger 的 select 未挂载/在其后）
    const agentSelect = wrapper.findAllComponents({ name: 'ElSelect' })[0]
    // 直接向组件实例 emit，模拟用户选中 agent-b（agent_id=2）：update:modelValue 触发 v-model 赋值，
    // change 触发 @change="onAgentChange"（内部读 activeAgentId=2 → loadRuns(2)）
    await agentSelect.vm.$emit('update:modelValue', 2)
    await agentSelect.vm.$emit('change', 2)
    await flushPromises()
    expect(listRuns).toHaveBeenCalledWith({ limit: 200, agent_id: 2 })
  })

  it('PAGE_SIZE=5：6 条记录显示分页器（若 20 则不显示）', async () => {
    listRuns.mockResolvedValue(RUNS) // 6 条
    const wrapper = mountDashboard()
    await flushPromises()
    expect(wrapper.find('.el-pagination').exists()).toBe(true)
  })
})

describe('版本对比默认预选（A=历史、B=最新）', () => {
  // 语义约定：后端 Δ = B−A（dashboard.py:101）。trendData 按 started_at 升序，
  // 默认 A=次新(历史基准)、B=最新(被测)，保证 Δ = 最新−历史 = 新版本较历史的变化。
  const TREND = [
    { run_id: 100, version: '0.1.0', status: 'completed', agent_score: 90, started_at: '2026-09-01T00:00:00Z' },
    { run_id: 101, version: '0.2.0', status: 'completed', agent_score: 95, started_at: '2026-09-01T01:00:00Z' },
    { run_id: 102, version: '0.3.0', status: 'completed', agent_score: 99, started_at: '2026-09-01T02:00:00Z' },
  ]

  it('下钻后自动预选最近两次：A=次新(历史)、B=最新，且 doCompare 按 (A, B) 调用', async () => {
    getTrend.mockResolvedValue(TREND)
    getCompare.mockResolvedValue({ a: { run_id: 101 }, b: { run_id: 102 }, dims: [] })
    const wrapper = mountDashboard()
    await flushPromises()
    await wrapper.findAll('.gate-card')[0].trigger('click') // 下钻 → loadTrend
    await flushPromises()
    // 下拉框顺序：第一个=A(历史/次新 101)，第二个=B(最新 102)
    const selects = wrapper.findAll('.compare-bar .el-select')
    expect(selects.length).toBeGreaterThanOrEqual(2)
    expect(selects[0].text()).toContain('#101')
    expect(selects[1].text()).toContain('#102')
    // doCompare 按 (compareA=历史, compareB=最新) 调后端；Δ=B−A=最新−历史（后端 dashboard.py:101）
    expect(getCompare).toHaveBeenCalledWith(101, 102)
  })
})
