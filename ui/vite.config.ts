import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// The API runs on 8000. Proxying /api keeps the browser on a single origin in
// development, which also means the SSE stream is not a cross-origin request.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      '/api': {
        target: process.env.DFSI_API_URL ?? 'http://127.0.0.1:8000',
        changeOrigin: true,
      },
    },
  },
})
