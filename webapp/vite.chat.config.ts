import { resolve } from "node:path";
import { mergeConfig } from "vite";
import tailwindcss from "tailwindcss";
import autoprefixer from "autoprefixer";
import base from "./vite.config.js";

// Reutiliza os targets Safari14 e a entrega IIFE; a ilha de preços conserva
// sua configuração e não recebe o processador de estilos do chat.
export default mergeConfig(base, {
  resolve: { alias: { "@": resolve(import.meta.dirname, "src") } },
  css: { postcss: { plugins: [tailwindcss(), autoprefixer()] } },
  build: {
    rollupOptions: {
      input: resolve(import.meta.dirname, "src/chat/main.tsx"),
      output: { entryFileNames: "chat-app.js", assetFileNames: "chat-app.[ext]" },
    },
  },
});
