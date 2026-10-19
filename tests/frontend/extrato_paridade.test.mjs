// Extrato: os atalhos (tipo, origem, rótulo) do /painel são COPIA dos do app (CLAUDE.md §0.7). Os dois projetos npm não se importam,
// então lê-se o TEXTO dos dois arquivos, no molde de bancos_paridade.test.mjs; falha ALTO se o formato mudar.
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { RAIZ } from "./_painel.mjs";

const ler = (...p) => readFileSync(join(RAIZ, ...p), "utf8");
const WEB = () => ler("webapp", "src", "dashboard", "lib", "extrato-dias.ts");
const APP = () => ler("app", "src", "features", "painel", "extratoFiltros.tsx");

const atalhos = (texto) => {
  const m = /export const ATALHOS\b[^=]*=\s*\[(.*?)\]\s*as const;/s.exec(texto);
  assert.ok(m, "ATALHOS não encontrado: o formato mudou");
  const linhas = [...m[1].matchAll(/\[\s*"([^"]*)",\s*"([^"]*)",\s*"([^"]*)"\s*\]/g)].map((x) => x.slice(1, 4).join("|"));
  assert.ok(linhas.length > 0, "nenhum atalho lido: o formato mudou");
  return linhas;
};

test("atalhos do /painel == ATALHOS do app (tipo, origem, rótulo, na mesma ordem)", () => {
  assert.deepEqual(atalhos(WEB()), atalhos(APP()));
});
test("negativo: um rótulo trocado só de um lado aparece como diferença", () => {
  assert.notDeepEqual(atalhos(WEB().replace('"Saídas"', '"Saida"')), atalhos(APP()));
  assert.notDeepEqual(atalhos(WEB().replace('"cartao"', '"banco"')), atalhos(APP()));
});
