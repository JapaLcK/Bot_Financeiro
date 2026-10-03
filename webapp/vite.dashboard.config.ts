import { resolve } from "node:path";
import { mergeConfig } from "vite";
import tailwindcss from "tailwindcss";
import autoprefixer from "autoprefixer";
import base from "./vite.config.js";

// Dashboard v2 (/painel). Herda da ilha de preços os targets Safari 14, a entrega
// IIFE e a saída em frontend/ sem limpar a pasta; o artefato é commitado e entra no
// gate do CI. O protótipo dashboard-v2/index.html carrega o mesmo artefato. O `css`
// é trocado depois do merge pelo mesmo motivo do vite.chat.config.ts.
const config = mergeConfig(base, {
  build: {
    rollupOptions: {
      input: resolve(import.meta.dirname, "src/dashboard/main.tsx"),
      output: { entryFileNames: "dashboard-app.js", assetFileNames: "dashboard-app.[ext]" },
    },
  },
});
config.css = {
  postcss: {
    plugins: [tailwindcss({ config: resolve(import.meta.dirname, "tailwind.dashboard.config.js") }), autoprefixer()],
  },
};

export default config;
