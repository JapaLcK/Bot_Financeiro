import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const FRONTEND = join(dirname(fileURLToPath(import.meta.url)), "..", "..", "frontend");
const html = readFileSync(join(FRONTEND, "dashboard.html"), "utf8");
const js = readFileSync(join(FRONTEND, "dashboard.js"), "utf8");

test("Exportar abre o seletor de período com as duas datas obrigatórias", () => {
  assert.match(html, /onclick="openExportModal\(\)"/);
  assert.match(html, /id="export-start-date" type="date" required/);
  assert.match(html, /id="export-end-date" type="date" required/);
  assert.match(html, /id="export-overlay"/);
});

test("a exportação envia o intervalo inclusivo ao backend", () => {
  assert.match(js, /new URLSearchParams\(\{ start_date: start, end_date: end \}\)/);
  assert.match(js, /end < start/);
  assert.match(js, /Nenhum lançamento neste período para exportar/);
  assert.doesNotMatch(js, /\/export\/\$\{USER_ID\}\?year=\$\{viewYear\}&month=\$\{viewMonth\}/);
});
