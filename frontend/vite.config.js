import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

// https://vite.dev/config/
export default defineConfig({
  // Tailwind v4 ab Vite plugin ki tarah aata hai — koi tailwind.config.js
  // ya postcss.config.js banane ki zaroorat nahi.
  plugins: [react(), tailwindcss()],

  server: {
    // Container ke bahar se access ke liye. Dockerfile me bhi --host diya hai,
    // yahan likhne se local pe bhi same behaviour milta hai.
    host: true,
    port: 5173,

    watch: {
      // Docker + Windows volume mount pe file-change events reliably nahi aate.
      // Polling se Vite khud check karta rehta hai — isi se hot reload chalta hai.
      usePolling: true,
    },
  },
})
