/** A coleta de baseline tem contrato de CLI e não deve iniciar navegador no teste. */
import { test } from "node:test";
import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const RAIZ = join(dirname(fileURLToPath(import.meta.url)), "..", "..");

test("coletor de baseline explica as duas amostras e seus parâmetros", () => {
  const saida = execFileSync(process.execPath, ["scripts/medir_frontend_publico.mjs", "--help"], {
    cwd: RAIZ,
    encoding: "utf8",
  });
  assert.match(saida, /--runs N/);
  assert.match(saida, /N contextos frios e N medições quentes/);
  assert.match(saida, /JSON registra todas as amostras/);
});
