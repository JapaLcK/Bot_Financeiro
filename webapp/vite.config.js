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
    // `safari14` nos DOIS, e não o default (`ios16.4`) em nenhum: este
    // repositório suporta iOS 14 explicitamente
    // (`tests/frontend/pb_nav_ios14.test.mjs` e o caso de iOS 14 do
    // `precos_pix_anual.test.mjs`), e ali cada um falha de um jeito:
    //
    //  · CSS — sem isto o minificador reescreve `@media (min-width: 508px)`
    //    como `@media (width >= 508px)`, sintaxe de range do Media Queries 4
    //    que só existe do Safari 16.4 pra frente, e o bloco inteiro é
    //    DESCARTADO: o pódio não aparece, sem erro nenhum;
    //  · JS — o `target` no default deixa o bundle emitir sintaxe de iOS 16.4.
    //    O artefato de hoje não usa nenhuma (`static{`, `#privado` e `.at(`
    //    saem em zero), mas isso é sorte da versão instalada de React/Vite: um
    //    bump que emita `static{}` mataria a ilha no iOS 14 com o CI verde.
    //    Assimetria no mesmo objeto de config é a forma que esse bug tem de
    //    entrar sem ninguém decidir por ele.
    target: "safari14",
    cssTarget: "safari14",
    outDir: resolve(import.meta.dirname, "..", "frontend"),
    emptyOutDir: false,
    cssCodeSplit: false,
    modulePreload: false,
    rollupOptions: {
      input: resolve(import.meta.dirname, "src", "precos", "main.jsx"),
      output: {
        format: "iife",
        entryFileNames: "precos-app.js",
        assetFileNames: "precos-app.[ext]",
      },
    },
  },
});
