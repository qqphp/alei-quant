import { defineConfig, loadEnv } from 'vite'
import vue from '@vitejs/plugin-vue'

export default defineConfig(({ mode }) => {
  const projectRoot = decodeURIComponent(new URL('..', import.meta.url).pathname).replace(/^\/([A-Za-z]:)/, '$1')
  const apiPort = loadEnv(mode, projectRoot, 'STOCK_').STOCK_API_PORT || '8000'
  if (!/^\d+$/.test(apiPort) || Number(apiPort) < 1 || Number(apiPort) > 65535) {
    throw new Error('STOCK_API_PORT 必须为 1–65535 的整数')
  }
  return {
    plugins: [vue()],
    server: { port: 5173, strictPort: true, proxy: { '/api': `http://127.0.0.1:${apiPort}` } },
  }
})
