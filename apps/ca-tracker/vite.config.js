import { resolve } from 'node:path';
import { defineConfig } from 'vite';
import legacy from '@vitejs/plugin-legacy';

export default defineConfig({
  root: 'frontend',
  base: '/ca/static/dist/',
  plugins: [legacy()],
  build: {
    outDir: '../static/dist',
    emptyOutDir: true,
    manifest: false,
    rollupOptions: {
      input: {
        app: resolve(__dirname, 'frontend/src/main.js'),
      },
      output: {
        entryFileNames: 'assets/app.js',
        chunkFileNames: 'assets/[name].js',
        assetFileNames: (assetInfo) => {
          if (assetInfo.name && assetInfo.name.endsWith('.css')) {
            return 'assets/style.css';
          }
          return 'assets/[name][extname]';
        },
      },
    },
  },
});
