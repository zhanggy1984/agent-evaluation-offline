// @vitest-environment jsdom
// P2-D16：ResizeObserver 自适应回归——隐藏 tab（display:none）中 init 的图容器宽 0，
// ECharts fallback 100px 图挤在角落；RO 监听容器尺寸变化（tab 切回可见 0→真实宽）自动 resize。
// mock echarts 避免引入完整库；RO 用假类可手动触发回调验证 resize 链路。
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
})

afterEach(() => {
  delete globalThis.ResizeObserver
})

describe('EChart ResizeObserver 自适应（P2-D16）', () => {
  it('mount 时创建 RO 并 observe 容器', () => {
    const wrapper = mount(EChart, { props: { option: { series: [] } } })
    expect(MockRO.instances).toHaveLength(1)
    expect(MockRO.instances[0].el).toBe(wrapper.element)
    expect(MockRO.instances[0].disconnected).toBeFalsy()
    expect(chart.setOption).toHaveBeenCalledWith({ series: [] })
  })

  it('RO 回调触发 chart.resize（tab 切回可见 0→真实宽 修正）', () => {
    mount(EChart, { props: { option: { series: [] } } })
    MockRO.instances[0].trigger()
    expect(chart.resize).toHaveBeenCalledTimes(1)
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
