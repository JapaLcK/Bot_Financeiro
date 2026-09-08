"use strict";

const DEFAULT_MAX_LINES = 350;

const BANNED_CONSOLE_METHODS = new Set([
  "log",
  "error",
  "warn",
  "info",
  "debug",
  "trace",
  "dir",
  "table",
  "time",
  "timeEnd",
  "timeLog",
  "group",
  "groupEnd",
  "groupCollapsed",
  "count",
  "countReset",
  "assert",
  "profile",
  "profileEnd",
]);

function normalizeFilePath(filename) {
  return filename.replace(/\\/g, "/");
}

// context.filename is the ESLint 9 property; getFilename() is kept as a
// fallback so these rules also load under a v8 host without editing.
function fileName(context) {
  return normalizeFilePath(context.filename ?? context.getFilename());
}

// ADAPTADO do vibe-coding-toolkit (que é TypeScript): lá havia mais duas
// isenções por nome — barrel/declaração (`index|types|interfaces|constants|
// dtos|enums|vo`) e um `.config`/`.stories` — e uma lista de diretórios
// (`generated/`, `locales/`, `dist/`, `build/`, `migrations/`, `fixtures/`,
// `mocks/`, `.next/`, `__tests__/`). Nenhum desses diretórios existe neste
// repositório (medido em 2026-09-04; para remedir:
//   git ls-files | awk -F/ '{for(i=1;i<NF;i++) print $i}' | sort -u
// ) e nenhum arquivo tem esses basenames. Elas só abriam buraco no gate:
// `frontend/index.js` é nome plausível aqui (já existe frontend/index.html) e
// passava com 404 linhas sem uma linha vermelha. Sobra a única que este repo
// tem de verdade: os testes em tests/frontend/*.test.mjs, cujo tamanho não
// diz nada sobre a fatoração do código de produção.
function isTestFile(filename) {
  return /\.(test|spec)\.[cm]?[jt]sx?$/.test(filename);
}

// Baseline entries are matched as a whole path or as a path suffix, so an
// entry written the way a lint report prints it ("src/legacy.ts") matches
// regardless of whether ESLint hands the rule an absolute path.
function isBaselineIgnored(filename, ignore) {
  return ignore.some(
    (entry) => filename === entry || filename.endsWith(`/${entry}`)
  );
}

module.exports = {
  BANNED_CONSOLE_METHODS,
  DEFAULT_MAX_LINES,
  fileName,
  isBaselineIgnored,
  isTestFile,
};
