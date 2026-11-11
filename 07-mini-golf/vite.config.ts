import { defineConfig } from 'vitest/config';

export default defineConfig({
  base: './', // the built game works from any folder, or straight off a static host
  build: { target: 'es2022', sourcemap: true },
  server: { port: 5173, strictPort: false },
  preview: { port: 4173, strictPort: true },
  test: {
    include: ['test/unit/**/*.test.ts'],
    environment: 'node',
  },
});
