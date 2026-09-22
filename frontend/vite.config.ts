import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// 前后端联调（vite-react-backend-integration §2）：删 mock 插件、加 proxy，前端代码零改动。
// 后端：backend/src/api/routes.py  →  uvicorn api.routes:app --port 8010
export default defineConfig({
  plugins: [react()],
  server: {
    host: '127.0.0.1',
    port: 5173,
    strictPort: true,
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8010',
        changeOrigin: true,
      },
    },
  },
  build: {
    rollupOptions: {
      output: {
        // 框架依赖单独成块：版本不常变，浏览器可长期缓存。页面与图表 chunk 由动态
        // import 自动分包（见 App.tsx 的路由 lazy 与 LearningHomePage 的图表 lazy）。
        manualChunks: {
          'vendor-react': ['react', 'react-dom', 'react-router-dom'],
        },
      },
    },
  },
})
