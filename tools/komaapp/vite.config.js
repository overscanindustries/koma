import {defineConfig} from 'vite';
import {svelte} from '@sveltejs/vite-plugin-svelte';

// `npm run dev` proxies the bridge's endpoints to a running komabridge (default port 8765), so the app can be worked
// on with hot reload against `komabridge.py --mock --mock-tether --mock-hw`.
const BRIDGE = 'http://127.0.0.1:' + (process.env.KOMA_BRIDGE_PORT || '8765');
const API = ['/status', '/settings', '/settings-map.json', '/reboot', '/photos', '/tether', '/live', '/ram', '/keys', '/dial', '/api',
  '/install', '/komalog', '/analysis', '/webcam', '/phone'];

export default defineConfig({
  base: './',
  plugins: [svelte()],
  build: {outDir: 'dist', emptyOutDir: true, assetsDir: 'assets', target: 'es2020', sourcemap: false},
  server: {proxy: Object.fromEntries(API.map(p => [p, {target: BRIDGE, changeOrigin: false}]))},
});
