// @vitest-environment jsdom
// P2-D16：ResizeObserver 自适应回归——隐藏 tab（display:none）中 init 的图容器宽 0，
// ECharts fallback 100px 图挤在角落；RO 监听容器尺寸变化（tab 切回可见 0→真实宽）自动 resize。
// 增强：resize 带「容器宽>0」守卫（对 0 宽容器无意义）+ onMounted 后 rAF 兜底（挂载后布局完成强制 resize）。
// mock echarts 避免引入完整库；RO 用假类可手动触发回调验证 resize 链路；rAF stub 同步执行便于断言。
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import { mount } from '@vue/test-utils'
import EChart from './EChart.vue'

const { chart } = vi.hoisted(() => ({
  chart: { setOption: vi.fn(), resize: vi.fn(), dispose: vi.fn() },
}))
vi.mock('echarts', () => ({ init: () => chart }))

class MockRO {
  static instances = []
  constructor(cb) {
    this.cb = cb
    MockRO.instances.push(this)
  }
  observe(el) { this.el = el }
  disconnect() { this.disconnected = true }
  trigger() { this.cb() }
}

beforeEach(() => {
  vi.clearAllMocks()
  MockRO.instances = []
  globalThis.ResizeObserver = MockRO
  vi.stubGlobal('requestAnimationFrame', (cb) => { cb(); return 1 }) // 同步执行，兜底 resize 立即可断言
})

afterEach(() => {
  delete globalThis.ResizeObserver
  vi.restoreAllMocks() // 恢复 clientWidth 原型 spy，防跨用例泄漏
  vi.unstubAllGlobals()
})

describe('EChart ResizeObserver 自适应（P2-D16）', () => {
  it('mount 时创建 RO 并 observe 容器', () => {
    const wrapper = mount(EChart, { props: { option: { series: [] } } })
    expect(MockRO.instances).toHaveLength(1)
    expect(MockRO.instances[0].el).toBe(wrapper.element)
    expect(MockRO.instances[0].disconnected).toBeFalsy()
    expect(chart.setOption).toHaveBeenCalledWith({ series: [] })
  })

  it('隐藏容器（宽 0）mount：rAF 兜底与 RO 回调均不 resize（守卫生效）', () => {
    const wrapper = mount(EChart, { props: { option: { series: [] } } }) // jsdom 默认宽 0
    MockRO.instances[0].trigger()
    expect(chart.resize).not.toHaveBeenCalled()
  })

  it('可见容器 mount：rAF 兜底在挂载后强制 resize 一次', () => {
    vi.spyOn(HTMLElement.prototype, 'clientWidth', 'get').mockReturnValue(800)
    mount(EChart, { props: { option: { series: [] } } })
    expect(chart.resize).toHaveBeenCalled()
  })

  it('RO 回调触发 chart.resize（tab 切回可见 0→真实宽 修正）', () => {
    vi.spyOn(HTMLElement.prototype, 'clientWidth', 'get').mockReturnValue(800)
    const wrapper = mount(EChart, { props: { option: { series: [] } } })
    MockRO.instances[0].trigger()
    expect(chart.resize).toHaveBeenCalled()
  })

  it('unmount 时 disconnect + dispose + 移除 window resize 监听', () => {
    const removeSpy = vi.spyOn(window, 'removeEventListener')
    const wrapper = mount(EChart, { props: { option: { series: [] } } })
    wrapper.unmount()
    expect(MockRO.instances[0].disconnected).toBe(true)
    expect(chart.dispose).toHaveBeenCalled()
    expect(removeSpy).toHaveBeenCalledWith('resize', expect.any(Function))
  })
})
