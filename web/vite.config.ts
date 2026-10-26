import react from '@vitejs/plugin-react';
import { defineConfig } from 'vite';

// Built into ../website, which the API serves at "/" (app/website.py).
export default defineConfig({
  plugins: [react()],
  build: { outDir: '../website', emptyOutDir: true, assetsDir: 'assets', sourcemap: false },
  server: { proxy: { '/v1': 'http://localhost:8000' } },
  test: { environment: 'node' },
});
