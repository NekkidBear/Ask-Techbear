import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

export default defineConfig({
  plugins: [
    react(),
    tailwindcss(),
  ],
  server: {
    port: 3000,
    // Add this line right here 👇
    allowedHosts: ['ask-techbear.gymnarctosstudiosllc.com'],
    proxy: {
      '/api': {
        target: 'http://localhost:8000',import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

export default defineConfig({
  plugins: [
    react(),
    tailwindcss(),
  ],
  server: {
    port: 3000,
    // Add this line right here 👇
    allowedHosts: ['ask-techbear.gymnarctosstudiosllc.com'],
    proxy: {
      '/api': {
        target: 'http://localhost:8000',
        changeOrigin: true,
      }
    }
  }
})
        changeOrigin: true,
      }
    }
  }
})