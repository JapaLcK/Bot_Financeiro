import { resolve } from "node:path";

import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

/**
 * Saída de NOME FIXO, sem hash, dentro de `frontend/` — o artefato é commitado.
 *
 * Nome fixo porque não há `StaticFiles` mount neste projeto: cada asset de
 * `frontend/` tem uma rota `@router.get` escrita à mão em
 * `frontend/routes/static_pages.py`, e nome com hash exigiria uma rota nova a
 * cada build. A invalidação de cache já é resolvida pelo `stamp_asset_versions`
 * (§5), que reescreve o `?v=` do HTML com um hash do CONTEÚDO do arquivo.
 *
 * IIFE e não ESM: `<script type="module">` é deferido, e o defer reintroduz a
 * corrida com o `pix-checkout.js` que a ordem dos `<script>` da precos.html
 * existe para evitar.
 *
 * `emptyOutDir: false` porque `outDir` é o site inteiro — limpar apagaria tudo.
 */
export default defineConfig({
  plugins: [react()],
  build: {
    outDir: resolve(import.meta.dirname, "..", "frontend"),
    emptyOutDir: false,
    cssCodeSplit: false,
    modulePreload: false,
    rollupOptions: {
      input: resolve(import.meta.dirname, "src", "precos", "main.jsx"),
      output: {
        format: "iife",
        inlineDynamicImports: true,
        entryFileNames: "precos-app.js",
        assetFileNames: "precos-app.[ext]",
      },
    },
  },
});
