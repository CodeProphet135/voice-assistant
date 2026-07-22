import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5174,
    strictPort: true,
    proxy: {
      '/ws': {
        target: 'ws://localhost:8010',
        ws: true,
      },
      '/api': {
        target: 'http://localhost:8010',
      },
    },
  },
})
