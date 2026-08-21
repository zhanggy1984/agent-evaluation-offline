import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

// dev 直连 backend（容器外开发用）；生产走 nginx 同源反代（baseURL ''），零改动切换
export default defineConfig({
  plugins: [vue()],
  server: {
    host: '0.0.0.0',
    port: 5173,
    proxy: {
      '/api': {
        target: 'http://backend:8000',
        changeOrigin: true,
      },
    },
  },
})
