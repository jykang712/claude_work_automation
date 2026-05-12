import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// Vite 개발 서버 설정
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    host: true, // localhost 외에도 접속 가능
  },
})