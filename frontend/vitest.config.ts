import path from 'node:path';
import { defineConfig } from 'vitest/config';

export default defineConfig({
  // Next compiles JSX with the automatic runtime; component tests need the same.
  esbuild: { jsx: 'automatic' },
  test: {
    environment: 'jsdom',
    globals: true,
  },
  resolve: {
    alias: {
      '@': path.resolve(__dirname, './src'),
    },
  },
});
