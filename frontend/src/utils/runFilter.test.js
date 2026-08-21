import { describe, it, expect } from 'vitest'
import { filterRuns, paginate } from './runFilter'

const RUNS = [
  { id: 1, agent_id: 1, status: 'completed' },
  { id: 2, agent_id: 2, status: 'running' },
  { id: 22, agent_id: 1, status: 'failed' },
]
const agentName = (id) => ({ 1: '客服', 2: '合同' }[id])
const runStatus = (s) => ({
  completed: { label: '已完成' }, running: { label: '进行中' }, failed: { label: '失败' },
}[s])

describe('filterRuns', () => {
  it('空条件返回全量', () => {
    expect(filterRuns(RUNS, { agentName, runStatus })).toEqual(RUNS)
  })
  it('状态过滤', () => {
    expect(filterRuns(RUNS, { statusFilter: 'running', agentName, runStatus }).map((r) => r.id)).toEqual([2])
  })
  it('按 id 搜索', () => {
    expect(filterRuns(RUNS, { search: '22', agentName, runStatus }).map((r) => r.id)).toEqual([22])
  })
  it('按 agent 名搜索', () => {
    expect(filterRuns(RUNS, { search: '合同', agentName, runStatus }).map((r) => r.id)).toEqual([2])
  })
  it('按状态 label 搜索', () => {
    expect(filterRuns(RUNS, { search: '完成', agentName, runStatus }).map((r) => r.id)).toEqual([1])
  })
  it('搜索前后空白与大小写不敏感', () => {
    expect(filterRuns(RUNS, { search: '  完成 ', agentName, runStatus }).map((r) => r.id)).toEqual([1])
  })
  it('状态 + 关键词组合', () => {
    expect(filterRuns(RUNS, { statusFilter: 'completed', search: '完成', agentName, runStatus }).map((r) => r.id)).toEqual([1])
  })
  it('无命中返回空数组', () => {
    expect(filterRuns(RUNS, { search: '不存在', agentName, runStatus })).toEqual([])
  })
})

describe('paginate', () => {
  const L = Array.from({ length: 25 }, (_, i) => i + 1)
  it('首页', () => { expect(paginate(L, 1, 20)).toHaveLength(20) })
  it('末页', () => { expect(paginate(L, 2, 20)).toEqual([21, 22, 23, 24, 25]) })
  it('越界页返回空', () => { expect(paginate(L, 99, 20)).toEqual([]) })
})
