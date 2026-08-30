// @vitest-environment jsdom
// Q8：wizard 组件测试——mock API/路由/权限，验证 6 步编排 + 权限门控 + 失败诊断。
// 覆盖 Q7 hook 审查修的真 bug 回归：discover 失败前移诊断、confirmAdapter 响应驱动
// 下游（凭证/输入域）、编辑 manifest 后接口/场景用 confirmedManifest 真相源。
import { describe, it, expect, beforeEach, vi } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import ElementPlus from 'element-plus'
import Onboarding from './Onboarding.vue'

const { isAdminMock } = vi.hoisted(() => ({ isAdminMock: { value: true } }))
vi.mock('vue-router', () => ({ useRouter: () => ({ push: vi.fn() }) }))
// storeToRefs 走 pinia；useAuthStore 走 stores/auth mock（避免加载真实 store 需 active pinia）
vi.mock('pinia', () => ({ storeToRefs: () => ({ isAdmin: isAdminMock }) }))
vi.mock('../stores/auth', () => ({ useAuthStore: () => ({ role: 'admin' }) }))
vi.mock('../api/agents', () => ({
  addScenes: vi.fn(),
  confirmAdapter: vi.fn(),
  createAgent: vi.fn(),
  discoverAgent: vi.fn(),
  generateSkeleton: vi.fn(),
  listInterfaces: vi.fn(),
  probeAgentInput: vi.fn(),
  setAgentAuth: vi.fn(),
  syncInterfaces: vi.fn(),
}))
vi.mock('../api/suites', () => ({ createSuite: vi.fn() }))
vi.mock('../api/cases', () => ({ createCase: vi.fn() }))

import {
  addScenes, confirmAdapter, createAgent, discoverAgent, generateSkeleton,
  listInterfaces, probeAgentInput, setAgentAuth, syncInterfaces,
} from '../api/agents'
import { createSuite } from '../api/suites'
import { createCase } from '../api/cases'

const MANIFEST = {
  agent: 'demo',
  contract_version: '2.0',
  interfaces: [{ name: 'chat', path: '/chat', method: 'POST', contract_type: 'sse', llm: true }],
  scenes: [{ tag: 'greeting', description: '问候' }],
  contract: {
    type: 'sse', timeout: 120,
    request: { path: '/chat', method: 'POST', body: { content: '{{input.content}}' } },
  },
}
const AUTH_MANIFEST = {
  ...MANIFEST,
  contract: {
    ...MANIFEST.contract,
    request: {
      ...MANIFEST.contract.request,
      headers: { Authorization: 'Bearer {{auth.credentials.token}}' },
    },
  },
}

const DISCOVER_OK = {
  ok: true, agent: 'demo', contract_version: '2.0',
  interfaces: MANIFEST.interfaces, scenes: MANIFEST.scenes,
  adapter: { valid: true, input_fields: ['content'], requires_auth: false, warnings: [], errors: [] },
  adapter_drift: [], manifest: MANIFEST,
}

beforeEach(() => {
  isAdminMock.value = true
  vi.clearAllMocks()
  createAgent.mockResolvedValue({ id: 1 })
  discoverAgent.mockResolvedValue(DISCOVER_OK)
  confirmAdapter.mockResolvedValue({ requires_auth: false, input_fields: ['content'], warnings: [] })
  syncInterfaces.mockResolvedValue({ created: [], skipped: [], renamed: [] })
  addScenes.mockResolvedValue({ created: [], skipped: [] })
  probeAgentInput.mockResolvedValue({
    ok: true,
    interfaces: [{ interface_id: 1, contract_type: 'sse', http_status: 200,
                   fields: { meta: 1, answer: 1, usage: 1, done: 1 }, errors: [] }],
  })
  generateSkeleton.mockResolvedValue({
    agent_name: 'demo',
    suite: { name: 'demo 接入示例', description: '骨架描述' },
    interfaces: [{ name: 'chat', path: '/chat', contract_type: 'sse' }],
    cases: [{
      name: 'greeting-chat', interface_name: 'chat', scene_tag: 'greeting',
      input_type: 'text', input: { content: '' },
      expected: {}, assertions: [], metrics: {},
    }],
  })
  listInterfaces.mockResolvedValue([{ id: 10, name: 'chat' }])
  createSuite.mockResolvedValue({ id: 100 })
  createCase.mockResolvedValue({ id: 1 })
})

function cardByTitle(wrapper, title) {
  return wrapper.findAll('.el-card').find((c) => c.text().includes(title))
}
function btn(wrapper, text) {
  const b = wrapper.findAll('button').find((x) => x.text().includes(text))
  expect(b, `按钮「${text}」应存在`).toBeTruthy()
  return b
}
async function goStep0(wrapper) {
  const card = cardByTitle(wrapper, '①')
  const inputs = card.findAll('input')
  await inputs[0].setValue('demo')
  await inputs[1].setValue('http://localhost:8000')
  await btn(wrapper, '创建并发现').trigger('click')
  await flushPromises()
}

describe('完整接入流程（admin）', () => {
  it('Step0 填 URL → 创建 + 发现 → 前进到自动发现卡片', async () => {
    const wrapper = mount(Onboarding, { global: { plugins: [ElementPlus] } })
    await goStep0(wrapper)
    expect(createAgent).toHaveBeenCalledWith(expect.objectContaining({
      name: 'demo', base_url: 'http://localhost:8000',
    }))
    expect(discoverAgent).toHaveBeenCalledWith(1)
    expect(cardByTitle(wrapper, '② 自动发现').isVisible()).toBe(true)
  })

  it('冒烟成功后生成骨架带探测输入，确认落库批量 createSuite/createCase', async () => {
    const wrapper = mount(Onboarding, { global: { plugins: [ElementPlus] } })
    await goStep0(wrapper)
    await btn(wrapper, '下一步').trigger('click')
    await flushPromises()
    await btn(wrapper, '确认 adapter').trigger('click')
    await flushPromises()
    expect(confirmAdapter).toHaveBeenCalledWith(1, MANIFEST)

    await btn(wrapper, '配置并同步').trigger('click')
    await flushPromises()
    expect(syncInterfaces).toHaveBeenCalledWith(1, [{
      name: 'chat', path: '/chat', method: 'POST', contract_type: 'sse',
    }])
    expect(addScenes).toHaveBeenCalledWith(1, [{ tag: 'greeting', description: '问候' }])

    // Step4 冒烟：content 输入 → 探测 → 前进 Step5
    const probeCard = cardByTitle(wrapper, '⑤ 冒烟探测')
    const contentInput = probeCard.findAll('input').find((i) => i.element.placeholder === '输入值')
    await contentInput.setValue('你好')
    await btn(wrapper, '开始冒烟').trigger('click')
    await flushPromises()
    expect(probeAgentInput).toHaveBeenCalledWith(1, { content: '你好' })

    // Step5 生成骨架（带冒烟输入）+ 落库
    await btn(wrapper, '生成骨架').trigger('click')
    await flushPromises()
    expect(generateSkeleton).toHaveBeenCalledWith(1, { content: '你好' })

    await btn(wrapper, '确认落库').trigger('click')
    await flushPromises()
    expect(createSuite).toHaveBeenCalledWith({ agent_id: 1, name: 'demo 接入示例', description: '骨架描述' })
    expect(createCase).toHaveBeenCalledWith(100, {
      interface_id: 10, name: 'greeting-chat', input_type: 'text',
      input: { content: '' }, expected: {}, assertions: [], metrics: {}, scenes: ['greeting'],
    })
    expect(wrapper.text()).toContain('接入完成')
  })
})

describe('发现失败诊断（Q7 hook 修复回归）', () => {
  it('discover ok=false → 前移到自动发现卡片展示错误 + 修改 URL 重试', async () => {
    discoverAgent.mockResolvedValueOnce({
      ok: false, agent_id: 1, base_url: 'http://localhost:8000',
      errors: ['连接失败: ConnectError'], raw_sample: null,
    })
    const wrapper = mount(Onboarding, { global: { plugins: [ElementPlus] } })
    await goStep0(wrapper)
    expect(cardByTitle(wrapper, '② 自动发现').isVisible()).toBe(true)
    expect(wrapper.text()).toContain('发现失败')
    expect(wrapper.text()).toContain('连接失败: ConnectError')
    expect(btn(wrapper, '修改 URL 重试').exists()).toBe(true)
  })
})

describe('权限门控', () => {
  it('非 Admin：Step2 确认 adapter 按钮禁用（isAdmin=false）', async () => {
    isAdminMock.value = false
    const wrapper = mount(Onboarding, { global: { plugins: [ElementPlus] } })
    await goStep0(wrapper)
    await btn(wrapper, '下一步').trigger('click')
    await flushPromises()
    const confirmBtn = btn(wrapper, '确认 adapter')
    expect(confirmBtn.attributes('disabled')).toBeDefined()
  })
})

describe('编辑 manifest 增删 auth（Q7 hook 修复回归）', () => {
  it('confirmAdapter 响应驱动下游：凭证键重扫 + 点号键嵌套落 setAgentAuth', async () => {
    confirmAdapter.mockResolvedValue({ requires_auth: true, input_fields: ['content'], warnings: [] })
    const wrapper = mount(Onboarding, { global: { plugins: [ElementPlus] } })
    await goStep0(wrapper)
    await btn(wrapper, '下一步').trigger('click')
    await flushPromises()

    // 编辑 manifest 加 {{auth.credentials.token}}
    await cardByTitle(wrapper, '③').find('textarea').setValue(JSON.stringify(AUTH_MANIFEST))
    await btn(wrapper, '确认 adapter').trigger('click')
    await flushPromises()
    expect(confirmAdapter).toHaveBeenCalledWith(1, AUTH_MANIFEST)

    // Step3 凭证表单出现完整键路径 credentials.token
    const credCard = cardByTitle(wrapper, '④ 凭证')
    expect(credCard.text()).toContain('credentials.token')
    await credCard.find('input[type="password"]').setValue('abc')
    await btn(wrapper, '配置并同步').trigger('click')
    await flushPromises()
    expect(setAgentAuth).toHaveBeenCalledWith(1, { credentials: { token: 'abc' } })
  })
})
