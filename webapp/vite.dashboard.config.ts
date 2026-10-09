import { resolve } from "node:path";
import { mergeConfig } from "vite";
import tailwindcss from "tailwindcss";
import autoprefixer from "autoprefixer";
import base from "./vite.config.js";

// Dashboard v2 (/painel). Herda da ilha de preços os targets Safari 14, a entrega
// IIFE e a saída em frontend/ sem limpar a pasta; o artefato é commitado e entra no
// gate do CI. O protótipo dashboard-v2/index.html carrega o mesmo artefato. O `css`
// é trocado depois do merge pelo mesmo motivo do vite.chat.config.ts.

// O rolldown 1.2.8 rebaixa o `this.#scopes.get(scope)?.find(...)` do
// `MutationCache.runNext` (@tanstack/query-core 5.104.0) para
// `D(I,this).bind(this).get(...)`: TypeError em runtime, engolido num `finally`, e a 2ª
// mutation de um mesmo `scope` fica `isPaused` para sempre. Tirar o `?.` da frente do
// campo privado contorna o bug. Se o texto mudar (bump do query-core), o build FALHA em
// vez de voltar ao emitido quebrado: remova o plugin se o rolldown já tiver conserto.
// tests/frontend/query_core_scope.test.mjs mede o efeito.
const ESCOPO_ORIGINAL = "(this.#scopes.get(scope)?.find((m) => m !== mutation && m.state.isPaused))?.continue()";
const ESCOPO_SEM_OPTIONAL =
  "((s) => s && s.find((m) => m !== mutation && m.state.isPaused))(this.#scopes.get(scope))?.continue()";
const queryCoreScope = {
  name: "query-core-scope-runnext",
  transform(code: string, id: string) {
    if (!/query-core[\\/].*mutationCache\.js$/.test(id)) return null;
    if (!code.includes(ESCOPO_ORIGINAL)) {
      throw new Error("query-core: o runNext mudou; revise o plugin query-core-scope-runnext em vite.dashboard.config.ts");
    }
    return code.replace(ESCOPO_ORIGINAL, ESCOPO_SEM_OPTIONAL);
  },
};

const config = mergeConfig(base, {
  plugins: [queryCoreScope],
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
