import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

// https://vite.dev/config/
export default defineConfig({
  // Tailwind v4 is now a Vite plugin — no need for tailwind.config.js
  // or postcss.config.js.
  plugins: [react(), tailwindcss()],

  server: {
    // For external container access. --host is also set in the Dockerfile,
    // setting it here ensures consistent behavior locally.
    host: true,
    port: 5173,

    watch: {
      // File-change events are unreliable on Docker + Windows volume mounts.
      // Polling allows Vite to monitor changes, enabling hot reload.
      usePolling: true,
    },
  },
})
