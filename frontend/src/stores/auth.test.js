import { describe, it, expect, vi, beforeEach } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { useAuthStore } from './auth'

// stores/auth 真实 store 测试：applyRefreshedTokens 是本项目新增核心逻辑，
// 含微妙业务规则——refresh 轮换后不得清 must_change_password（与 setSession 不同）。
// node 环境无浏览器全局，stub localStorage；store 的 authApi 依赖在 import 时无副作用。

const mem = new Map()
beforeEach(() => {
  mem.clear()
  vi.stubGlobal('localStorage', {
    getItem: (k) => (mem.has(k) ? mem.get(k) : null),
    setItem: (k, v) => mem.set(k, String(v)),
    clear: () => mem.clear(),
  })
  setActivePinia(createPinia())
})

describe('auth store applyRefreshedTokens', () => {
  // 模拟已登录：localStorage 预置完整会话，store state 从 localStorage 初始化
  const seedLogin = () => {
    mem.set('access_token', 'old')
    mem.set('refresh_token', 'r-old')
    mem.set('role', 'admin')
    mem.set('username', 'u')
    mem.set('must_change_password', 'true')
    return useAuthStore()
  }

  it('更新 token/role，保留 username 与 must_change_password（不清强制改密标记）', () => {
    const auth = seedLogin()
    auth.applyRefreshedTokens({ access_token: 'new', refresh_token: 'r-new', role: 'viewer' })
    expect(auth.access_token).toBe('new')
    expect(auth.refresh_token).toBe('r-new')
    expect(auth.role).toBe('viewer')
    expect(auth.username).toBe('u')              // 保留
    expect(auth.must_change_password).toBe(true) // 关键：强制改密中 refresh 后仍须改密
    expect(localStorage.getItem('access_token')).toBe('new')
    expect(localStorage.getItem('refresh_token')).toBe('r-new')
    expect(localStorage.getItem('role')).toBe('viewer')
    expect(localStorage.getItem('username')).toBe('u')
    expect(localStorage.getItem('must_change_password')).toBe('true')
  })

  it('role 缺省时不覆盖现有 role', () => {
    const auth = seedLogin()
    auth.applyRefreshedTokens({ access_token: 'new', refresh_token: 'r-new' })
    expect(auth.role).toBe('admin')
    expect(localStorage.getItem('role')).toBe('admin')
  })

  it('must_change_password 为 false 时刷新后保持 false', () => {
    const auth = seedLogin()
    auth.must_change_password = false
    auth.applyRefreshedTokens({ access_token: 'new', refresh_token: 'r-new' })
    expect(auth.must_change_password).toBe(false)
    expect(auth.username).toBe('u')
  })
})
