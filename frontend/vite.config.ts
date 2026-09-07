import { defineConfig } from 'vitest/config';
import react from '@vitejs/plugin-react-swc';
import path from 'node:path';

export default defineConfig(({ mode }) => ({
  plugins: [react()],
  resolve: {
    alias: {
      '@': path.resolve(__dirname, 'src'),
    },
  },
  test: {
    environment: 'node',
    include: ['src/**/*.test.{ts,tsx}'],
    setupFiles: ['./src/test/setup.ts'],
    // (MUDD-90) Five self-hosted runners share one Mac mini, so a test that
    // easily beats vitest's 5000ms default in isolation can still miss it
    // under real CPU contention. Measured, not theorised: the same suite
    // failed on different, unrelated files each time under concurrent CI load
    // (2026-08-27 08:29 and 14:06) and passed clean standalone — a contention
    // budget problem, not a broken test. Local runs keep the tighter default
    // so a genuinely hung test still fails fast on a laptop.
    testTimeout: process.env.CI ? 15000 : 5000,
  },
  server: {
    port: 5173,
    host: true,
    proxy: {
      '/api': {
        target: process.env.VITE_API_PROXY ?? 'http://localhost:8000',
        changeOrigin: true,
      },
    },
  },
  build: {
    outDir: 'dist',
    sourcemap: mode !== 'production',
    target: 'es2022',
    rollupOptions: {
      output: {
        // (#789) vite 8 / rollup 4 typings accept only the function form here.
        // Same vendor-splitting intent as the prior object form: isolate the
        // heavy third-party libs into stable, separately-cacheable chunks.
        manualChunks: (id: string) => {
          if (!id.includes('node_modules')) return;
          if (/[\\/]node_modules[\\/](react|react-dom|react-router|react-router-dom|scheduler)[\\/]/.test(id)) return 'react';
          if (/[\\/]node_modules[\\/](@tanstack[\\/]react-query|axios)[\\/]/.test(id)) return 'query';
          if (/[\\/]node_modules[\\/]recharts[\\/]/.test(id)) return 'charts';
          if (/[\\/]node_modules[\\/](i18next|react-i18next|i18next-browser-languagedetector)[\\/]/.test(id)) return 'i18n';
        },
      },
    },
  },
}));
