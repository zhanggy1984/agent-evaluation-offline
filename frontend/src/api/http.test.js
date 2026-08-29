import { describe, it, expect, vi, beforeEach } from 'vitest'

// P2-B1 响应拦截器集成测试：401 → refresh 单飞换 token → 重放原请求。
// 全 mock（axios 实例/localStorage/pinia），手动触发拦截器 rejected 回调断言行为。

// ---- 模块级 mock 引用（vi.hoisted：mock 工厂与断言共享同一引用） ----
const h = vi.hoisted(() => ({
  post: vi.fn(),
  create: vi.fn(),
  reqCbs: [],
  resCbs: [],
  inst: { request: vi.fn() },
}))

const authMock = vi.hoisted(() => ({
  access_token: '',
  applyRefreshedTokens: vi.fn(),
  clear: vi.fn(),
}))

vi.mock('axios', () => {
  // axios 实例本质是 callable（http(config) 重放请求），mock 需为函数而非普通对象
  const instance = (...args) => h.inst.request(...args)
  instance.interceptors = {
    request: { use: (cb) => h.reqCbs.push(cb) },
    response: { use: (ok, err) => h.resCbs.push({ ok, err }) },
  }
  instance.request = h.inst.request
  h.create.mockReturnValue(instance)
  return { default: { create: h.create, post: h.post } }
})

vi.mock('element-plus', () => ({ ElMessage: { error: vi.fn() } }))
vi.mock('../stores/auth', () => ({ useAuthStore: () => authMock }))

import http from './http'

const rejected = h.resCbs[0].err

// ---- localStorage / window 环境 stub（vitest node 无浏览器全局） ----
const mem = new Map()
beforeEach(() => {
  mem.clear()
  h.post.mockReset()
  h.inst.request.mockReset()
  authMock.access_token = ''
  authMock.applyRefreshedTokens.mockReset()
  authMock.applyRefreshedTokens.mockImplementation((d) => { authMock.access_token = d.access_token })
  authMock.clear.mockReset()
  vi.stubGlobal('localStorage', {
    getItem: (k) => (mem.has(k) ? mem.get(k) : null),
    setItem: (k, v) => mem.set(k, String(v)),
    clear: () => mem.clear(),
  })
  vi.stubGlobal('window', { location: { pathname: '/dashboard', href: '' } })
})

const err401 = (url, headers = {}) => ({
  response: { status: 401, data: { message: 'unauthorized' } },
  config: { url, headers },
})

describe('P2-B1 401 → refresh 重放', () => {
  it('无 refresh_token：直接清态跳登录', async () => {
    await rejected(err401('/api/agents')).catch(() => {})
    expect(authMock.clear).toHaveBeenCalled()
    expect(window.location.href).toBe('/login')
    expect(h.post).not.toHaveBeenCalled()
  })

  it('refresh 成功：换新 token 重放原请求，不跳转', async () => {
    mem.set('refresh_token', 'r1')
    h.post.mockResolvedValue({ data: { data: { access_token: 'new', refresh_token: 'r2', role: 'admin' } } })
    await rejected(err401('/api/agents'))
    expect(h.post).toHaveBeenCalledTimes(1)
    expect(h.post).toHaveBeenCalledWith('/api/auth/refresh', { refresh_token: 'r1' })
    expect(authMock.applyRefreshedTokens).toHaveBeenCalledWith({ access_token: 'new', refresh_token: 'r2', role: 'admin' })
    expect(h.inst.request).toHaveBeenCalledTimes(1) // 重放原请求
    expect(window.location.href).toBe('') // 未跳登录
  })

  it('refresh 失败：清态跳登录', async () => {
    mem.set('refresh_token', 'r1')
    h.post.mockRejectedValue(new Error('refresh fail'))
    await rejected(err401('/api/agents')).catch(() => {})
    expect(authMock.clear).toHaveBeenCalled()
    expect(window.location.href).toBe('/login')
  })

  it('并发 401：refresh 单飞只发一次，各自重放', async () => {
    mem.set('refresh_token', 'r1')
    let resolveRefresh
    h.post.mockImplementation(() => new Promise((res) => { resolveRefresh = res }))
    const p1 = rejected(err401('/api/agents'))
    const p2 = rejected(err401('/api/cases'))
    expect(h.post).toHaveBeenCalledTimes(1) // 单飞：第二个 401 复用同一刷新
    resolveRefresh({ data: { data: { access_token: 'new', refresh_token: 'r2' } } })
    await Promise.all([p1, p2])
    expect(h.inst.request).toHaveBeenCalledTimes(2) // 两个请求都重放
    expect(window.location.href).toBe('')
  })

  it('重试后仍 401：不再二次 refresh，直接清态', async () => {
    mem.set('refresh_token', 'r1')
    h.post.mockResolvedValue({ data: { data: { access_token: 'new', refresh_token: 'r2' } } })
    const cfg = { url: '/api/agents', headers: {} }
    await rejected({ response: { status: 401, data: {} }, config: cfg }) // 第一次 401 → refresh → 重放
    expect(h.post).toHaveBeenCalledTimes(1)
    authMock.clear.mockClear()
    await rejected({ response: { status: 401, data: {} }, config: cfg }).catch(() => {}) // 重放后仍 401 → _retried → 清态
    expect(authMock.clear).toHaveBeenCalled()
    expect(h.post).toHaveBeenCalledTimes(1) // 未再次 refresh
  })

  it('login 401：不 refresh 不清态不跳转（走业务提示）', async () => {
    await rejected(err401('/api/auth/login')).catch(() => {})
    expect(h.post).not.toHaveBeenCalled()
    expect(authMock.clear).not.toHaveBeenCalled()
    expect(window.location.href).toBe('')
  })
})
