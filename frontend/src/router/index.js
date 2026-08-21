import { createRouter, createWebHistory } from 'vue-router'
import { useAuthStore } from '../stores/auth'

const routes = [
  {
    path: '/login',
    name: 'login',
    component: () => import('../views/Login.vue'),
  },
  {
    // 改密页需要登录但不被 must_change_password 拦截（否则改密引导无入口）
    path: '/change-password',
    name: 'change-password',
    component: () => import('../views/ChangePassword.vue'),
  },
  {
    path: '/',
    component: () => import('../layout/MainLayout.vue'),
    children: [
      { path: '', redirect: '/dashboard' },
      { path: 'dashboard', name: 'dashboard', component: () => import('../views/Dashboard.vue') },
      { path: 'config', name: 'config', component: () => import('../views/Config.vue') },
      { path: 'agents', name: 'agents', component: () => import('../views/Agents.vue') },
      { path: 'cases', name: 'cases', component: () => import('../views/Cases.vue') },
      { path: 'performance', name: 'performance', component: () => import('../views/Performance.vue') },
      { path: 'cost', name: 'cost', component: () => import('../views/Cost.vue') },
      { path: 'coverage', name: 'coverage', component: () => import('../views/Coverage.vue') },
      { path: 'issues', name: 'issues', component: () => import('../views/Issues.vue') },
      { path: 'meta-eval', name: 'meta-eval', component: () => import('../views/MetaEval.vue') },
      { path: 'reviews', name: 'reviews', component: () => import('../views/Review.vue') },
      { path: 'plugins', name: 'plugins', component: () => import('../views/PluginDict.vue') },
      { path: 'users', name: 'users', component: () => import('../views/Users.vue') },
    ],
  },
]

const router = createRouter({
  history: createWebHistory(),
  routes,
})

router.beforeEach((to) => {
  const auth = useAuthStore()

  if (to.path === '/login') {
    // 已登录访问登录页 → 回首页
    if (auth.isLoggedIn) return '/'
    return true
  }

  // 其余页面均需登录
  if (!auth.isLoggedIn) {
    return { path: '/login', query: { redirect: to.fullPath } }
  }

  // 强制改密：除 /change-password 外全部重定向
  if (auth.must_change_password && to.path !== '/change-password') {
    return '/change-password'
  }

  return true
})

export default router
