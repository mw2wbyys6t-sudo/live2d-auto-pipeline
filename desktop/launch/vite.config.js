import { defineConfig } from 'vite';
import vue from '@vitejs/plugin-vue';
import { viteSingleFile } from 'vite-plugin-singlefile';

// Tauri 的 frontendDist 指向 ../dist，启动页构建产物直接覆盖它。
// base './' 让资源用相对路径，避免 tauri:// 协议下出现绝对路径 404；
// singlefile 把 JS/CSS 内联进 index.html，让 dist 只保留一个可提交的文件，
// 避免 hashed 资源被 .gitignore 忽略后，干净检出/打包时启动页 404。
export default defineConfig({
  base: './',
  plugins: [vue(), viteSingleFile()],
  build: {
    outDir: '../dist',
    emptyOutDir: true,
    target: 'chrome110',
    cssCodeSplit: false,
    assetsInlineLimit: 100000000,
  },
});
