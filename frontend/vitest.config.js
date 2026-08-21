// 前端单测：纯函数 node 环境（不引入 jsdom/@vue/test-utils，只测 utils + 常量结构）
import { defineConfig } from 'vitest/config'

export default defineConfig({
  test: {
    environment: 'node',
    include: ['src/**/*.test.js'],
  },
})
