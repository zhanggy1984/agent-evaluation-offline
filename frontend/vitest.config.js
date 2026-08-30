// 前端单测：默认 node 环境测纯函数（utils/常量）；组件测试文件用
// `// @vitest-environment jsdom` 文件级切换（见 Onboarding.test.js），
// 不强制全量测试引入 jsdom/@vue/test-utils，保持纯函数测试轻量。
import { defineConfig } from 'vitest/config'
import vue from '@vitejs/plugin-vue'

export default defineConfig({
  plugins: [vue()],
  test: {
    environment: 'node',
    include: ['src/**/*.test.js'],
  },
})
