import { defineConfig } from 'vitest/config'
import vue from '@vitejs/plugin-vue'

const proxy = { target: 'http://127.0.0.1:8000', changeOrigin: false }
export default defineConfig({
  plugins: [vue()],
  server: {
    host: '127.0.0.1', port: 5173, strictPort: true,
    proxy: { '/api/v1': proxy, '/healthz': proxy, '/readyz': proxy },
  },
  test: { include: ['tests/**/*.test.ts'], environment: 'happy-dom', globals: false, clearMocks: true },
})
