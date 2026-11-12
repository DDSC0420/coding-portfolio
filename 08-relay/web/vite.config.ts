import react from '@vitejs/plugin-react';
import { defineConfig } from 'vite';

// The dashboard only ever calls /api/...; in development Vite forwards that to the control API,
// in Docker nginx does the same (web/nginx.conf). So there is no CORS and no API address in the build.
const api = process.env.RELAY_API_URL ?? 'http://127.0.0.1:8000';
const proxy = { '/api': { target: api, changeOrigin: true, rewrite: (p: string) => p.replace(/^\/api/, '') } };

export default defineConfig({
  plugins: [react()],
  server: { port: 5173, proxy },
  preview: { port: 4173, strictPort: true, proxy },
});
