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
      // Zeradas no burndown de 2026-09-03 (`npx eslint . --fix`, 199 sítios;
      // os 2 que o fixer recusou foram conferidos e convertidos à mão).
      "no-var": "error",
      "prefer-const": "error",
      "no-unused-vars": [
        "error",
        {
          argsIgnorePattern: "^_",
          varsIgnorePattern: "^_",
          // `catch (_)` é o idioma do projeto, e argsIgnorePattern não vale
          // para parâmetro de catch.
          caughtErrorsIgnorePattern: "^_",
          // MUDANÇA DE CONFIG, não limpeza de código: `local` deixa de
          // reportar declaração do escopo GLOBAL. Os arquivos de frontend/ são
          // scripts clássicos, e o que consome os nomes do topo são os 107
          // `onclick=` do dashboard.html (mais os gerados dentro de template
          // string) — HTML que o ESLint não lê. Para remedir:
          //   grep -oiE "onclick\s*=" frontend/dashboard.html | wc -l
          // Com o default `all` isso dava 73 avisos: 70 falso positivo e 3
          // código morto de VERDADE.
          //
          // NADA cobre esta classe. tests/frontend/handlers_inline.test.mjs roda
          // só na direção HTML→JS (pergunta se todo `onclick` do HTML acha um
          // nome no JS); a direção inversa — global declarada e SEM consumidor —
          // não é verificada por teste nenhum, nem por lint. O preço desta
          // config é exatamente essa: as 3 abaixo só existem registradas aqui, e
          // este comentário é o único registro. Linhas MEDIDAS EM 2026-09-03 —
          // remeça antes de reusar (CLAUDE.md §2), elas andam a cada edição:
          //   grep -nE "^(async )?function (openPocketModal|confirmDeletePocket|computeDailyFromLaunches)\b" frontend/dashboard.js
          //   openPocketModal          frontend/dashboard.js:8849
          //   confirmDeletePocket      frontend/dashboard.js:9140
          //   computeDailyFromLaunches frontend/dashboard.js:9973
          // Remover função é decisão do dono, fora deste ciclo — as 3 seguem no
          // arquivo de propósito.
          vars: "local",
        },
      ],
      // builtinGlobals: false porque as funções globais do próprio projeto
      // estão declaradas em `globals` acima — sem isso, o arquivo que DEFINE
      // cada uma é reportado como redeclaração dela. Zerada no burndown de
      // 2026-09-03: as 3 eram `_fmtBRL`/`_fmtDateBR` declaradas 2× e 3× no
      // dashboard.js, com a última vencendo em todo o arquivo.
      "no-redeclare": ["error", { builtinGlobals: false }],
      // "error" torna verdade uma coisa nova: global de CDN nova usada sem
      // entrar na lista `globals` deste arquivo REPROVA O CI, não avisa mais.
      "no-undef": "error",
      // Orçamento de tamanho e complexidade: "warn" de propósito. São números
      // para conversa sobre fatoração, não portão. As contagens abaixo são a
      // LINHA DE BASE do burndown de 2026-09-03 (portão de decisão: corrigir o
      // mecânico, rastrear esta cauda). 148 dos 179 estão em dashboard.js e só
      // saem quando o arquivo for quebrado — reestruturar função e mover função
      // de arquivo no mesmo commit é como se perde o rastro do que quebrou.
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
      // "Qualquer arquivo NOVO" é literal desde 2026-09-04: as isenções por
      // nome e por diretório que vinham do toolkit TypeScript foram
      // removidas (eslint-rules/utils.cjs) porque isentavam nomes plausíveis
      // aqui — `frontend/index.js` com 404 linhas passava sem uma linha
      // vermelha. Sonda de 403 linhas em 10 nomes: os 10 reprovam.
      "quality/max-lines": [
        "error",
        {
          // A contagem da regra bate com o `wc -l` (a linha vazia depois do
          // \n final é descontada em eslint-rules/core-rules.cjs): 350 passa,
          // 351 reprova, e a mensagem informa o mesmo número do comando.
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
        { logger: "um adaptador de log do projeto" },
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
