import { resolve } from "node:path";
import { mergeConfig } from "vite";
import tailwindcss from "tailwindcss";
import autoprefixer from "autoprefixer";
import base from "./vite.config.js";

// Protótipo do novo dashboard. Reusa os targets Safari 14 e a entrega IIFE da
// ilha de preços, mas escreve em ../dashboard-v2 (fora de frontend/): não é
// servido pelo backend e não entra no gate de artefato do CI. O `css` é trocado
// depois do merge pelo mesmo motivo do vite.chat.config.ts.
const config = mergeConfig(base, {
  build: {
    outDir: resolve(import.meta.dirname, "..", "dashboard-v2"),
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
