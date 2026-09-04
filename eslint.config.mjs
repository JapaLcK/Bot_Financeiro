// Tier único de lint. O projeto é JavaScript puro servido estático (sem build,
// sem TypeScript), então não existe aqui o tier type-aware do template
// (eslint.typed.config.mjs) nem o bloco de fronteiras do import-x: os scripts
// de frontend/ são <script> clássicos, sem imports entre si.
//
// Adaptado de templates/eslint/eslint.config.mjs.example (vibe-coding-toolkit).
// As severidades vêm da MEDIÇÃO de 2026-09-03 (`npm run lint` neste branch),
// não de preferência: regra com zero violação nasce "error"; regra com
// violação nasce "warn" com a contagem anotada como linha de base, e volta
// para "error" quando a contagem chegar a zero.
import js from "@eslint/js";
import { defineConfig, globalIgnores } from "eslint/config";
import globals from "globals";

import quality from "./eslint-rules/index.cjs";

export default defineConfig([
  js.configs.recommended,

  {
    // 1 violação: um `&nbsp;` deliberado dentro de um comentário em
    // tests/frontend/precos_sem_plano_gratis.test.mjs:209.
    rules: { "no-irregular-whitespace": "warn" }, // 1
  },

  {
    // frontend/**: scripts clássicos carregados por <script>, não módulos.
    files: ["frontend/**/*.js"],
    languageOptions: {
      ecmaVersion: "latest",
      sourceType: "script",
      globals: {
        ...globals.browser,
        ...globals.serviceworker,
        // Bibliotecas de CDN carregadas pelo HTML.
        Chart: "readonly",
        Sortable: "readonly",
        // Globais que o próprio projeto publica em um arquivo e consome em
        // outro (dashboard.js, pb-nav.js, modals.js são scripts clássicos).
        PBNav: "readonly",
        pigModalKeys: "readonly",
        closePiggy: "readonly",
        piggySend: "readonly",
        piggyAutoresize: "readonly",
        isProUser: "readonly",
        showUpgradeModal: "readonly",
        csrfHeaders: "readonly",
      },
    },
  },
  {
    // Harness de testes e scripts de smoke: Node com ESM.
    files: ["**/*.mjs"],
    languageOptions: {
      ecmaVersion: "latest",
      sourceType: "module",
      globals: { ...globals.node, ...globals.browser },
    },
  },

  {
    files: ["frontend/**/*.js", "scripts/**/*.mjs"],
    plugins: { quality },
    rules: {
      "no-empty": ["error", { allowEmptyCatch: true }],
      // Linhas de base medidas em 2026-09-03 (contagem entre parênteses).
      "no-var": ["warn"], // 201
      "prefer-const": ["warn"], // 6
      "no-unused-vars": [
        "warn", // 101
        {
          argsIgnorePattern: "^_",
          varsIgnorePattern: "^_",
          // `catch (_)` é o idioma do projeto, e argsIgnorePattern não vale
          // para parâmetro de catch.
          caughtErrorsIgnorePattern: "^_",
        },
      ],
      // builtinGlobals: false porque as funções globais do próprio projeto
      // estão declaradas em `globals` acima — sem isso, o arquivo que DEFINE
      // cada uma é reportado como redeclaração dela.
      "no-redeclare": ["warn", { builtinGlobals: false }], // 3
      // 1 violação, e é bug de verdade: `errEl` em frontend/dashboard.js:9774
      // está fora do escopo do `const errEl` da linha 9747.
      "no-undef": "warn", // 1
      // Orçamento de tamanho e complexidade: "warn" de propósito. São números
      // para conversa sobre fatoração, não portão.
      complexity: ["warn", 12], // 86
      "max-depth": ["warn", 4], // 4
      "max-statements": ["warn", 20], // 77
      "max-params": ["warn", 4], // 8
      "max-lines-per-function": [
        "warn", // 3
        { max: 150, skipBlankLines: true, skipComments: true },
      ],
      "max-nested-callbacks": ["warn", 3], // 1
      // Fica em "error" mesmo com 6 infratores: eles entram como lista
      // explícita em `ignore` (linha de base de 2026-09-03), então qualquer
      // arquivo NOVO acima de 350 linhas quebra o lint hoje. Cada arquivo
      // desta lista sai daqui quando for quebrado — não suba o teto.
      "quality/max-lines": [
        "error",
        {
          max: 350,
          // Sem o tamanho de cada um anotado aqui: número que um comando
          // responde envelhece em silêncio (CLAUDE.md §2 — e este já
          // envelheceu uma vez, no burndown que tirou 25 linhas do
          // dashboard.js). Para remedir:
          //   wc -l frontend/dashboard.js frontend/app-mode.js frontend/comecar.js \
          //         frontend/open-finance-connect.js frontend/pb-nav.js \
          //         frontend/static/auth-refresh.js
          ignore: [
            "frontend/dashboard.js",
            "frontend/app-mode.js",
            "frontend/comecar.js",
            "frontend/open-finance-connect.js",
            "frontend/pb-nav.js",
            "frontend/static/auth-refresh.js",
          ],
        },
      ],
      // 21 violações na linha de base. Não há adaptador de log em JS neste
      // projeto — quando existir, aponte o nome dele aqui e desligue a regra
      // no arquivo do adaptador com um bloco DEPOIS deste.
      "quality/no-direct-console": [
        "warn", // 21
        { logger: "um adaptador de log do frontend" },
      ],
      // quality/no-direct-data-access foi removida: não existe módulo de dados
      // em JS aqui — o banco é acessado só pelo backend Python (db/).
    },
  },

  {
    // Mesmo orçamento de tamanho para os testes, em "warn" (8 acima de 350).
    // Vem DEPOIS do bloco que liga a regra em "error": para um arquivo casado
    // pelos dois, o flat config aplica o bloco posterior por último.
    files: ["tests/frontend/**/*.mjs"],
    plugins: { quality },
    rules: {
      "quality/max-lines": ["warn", { includeTests: true, max: 350 }],
    },
  },
  {
    // O corpo de page.evaluate() roda no navegador e chama funções globais que
    // o dashboard.js define — o ESLint não tem como enxergar isso daqui.
    files: ["tests/frontend/**/*.mjs", "scripts/smoke_prod_ui.mjs"],
    rules: { "no-undef": "off" },
  },
  {
    files: ["eslint-rules/**/*.cjs"],
    languageOptions: { sourceType: "commonjs", globals: { ...globals.node } },
  },

  globalIgnores([
    "node_modules/**",
    "mobile/ios/**",
    "mobile/node_modules/**",
    "package-lock.json",
  ]),
]);
