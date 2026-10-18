// Bancos conectados: o que o /painel COPIA de outro lugar tem de bater com a fonte (CLAUDE.md §0.7). Os dois projetos npm não se
// importam, então lê-se o TEXTO dos arquivos (sem navegador), no molde de tests/test_phosphor_subset.py:
//   · CORES_BANCO (webapp/src/dashboard/lib/bancos.ts) == coresBanco (app/src/ui/tokens.ts);
//   · SUPERFICIE == --plane-1 e --tw-card (styles/base.css): a cor sobre a qual a regra de 3:1 mede o contraste;
//   · TOM_DO_ESTADO == OF_PILL_CLASS (frontend/settings.html): active→ok, pending→pendente, error→erro.
// Cada comparação falha ALTO se o formato mudar (mapa vazio), e o negativo prova que um hex ou um estado trocado é visto.
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { RAIZ } from "./_painel.mjs";

const ler = (...p) => readFileSync(join(RAIZ, ...p), "utf8");
const WEB = () => ler("webapp", "src", "dashboard", "lib", "bancos.ts");
const APP = () => ler("app", "src", "ui", "tokens.ts");
const BASE = () => ler("webapp", "src", "dashboard", "styles", "base.css");
const SETTINGS = () => ler("frontend", "settings.html");

// O texto entre as chaves do `const <nome>` (até a primeira `}`), ou erro alto.
const bloco = (texto, nome) => {
  const m = new RegExp(`${nome}\\b[^=]*=\\s*\\{([^}]*)\\}`).exec(texto);
  assert.ok(m, `bloco ${nome} não encontrado: o formato mudou`);
  return m[1];
};
const pares = (texto, re) => {
  const mapa = Object.fromEntries([...texto.matchAll(re)].map((m) => [m.groups.k ?? m.groups.b, m.groups.v]));
  assert.ok(Object.keys(mapa).length > 0, "nenhum par lido: o formato mudou");
  return mapa;
};
const hex = (re, texto, rotulo) => { const m = re.exec(texto); assert.ok(m, `${rotulo} não encontrado: o formato mudou`); return m[1].toLowerCase(); };
const diff = (a, b) => [...new Set([...Object.keys(a), ...Object.keys(b)])].filter((k) => a[k] !== b[k]).map((k) => `${k}: ${a[k]} × ${b[k]}`);

const COR = /(?:"(?<k>[^"]+)"|(?<b>\w+)):\s*"(?<v>#[0-9A-Fa-f]{6})"/g;
const coresDiff = (web, app) => diff(pares(bloco(web, "CORES_BANCO"), COR), pares(bloco(app, "coresBanco"), COR));
const rgbDe = (h) => [1, 3, 5].map((i) => parseInt(h.slice(i, i + 2), 16)).join(" ");
const superficieDiff = (web, base) => {
  const s = hex(/SUPERFICIE\s*=\s*"(#[0-9a-fA-F]{6})"/, web, "SUPERFICIE");
  const plane = hex(/--plane-1:\s*(#[0-9a-fA-F]{6})/, base, "--plane-1");
  const card = /--tw-card:\s*(\d+ \d+ \d+)/.exec(base)?.[1];
  assert.ok(card, "--tw-card não encontrado: o formato mudou");
  return [...(s === plane ? [] : [`SUPERFICIE ${s} × --plane-1 ${plane}`]), ...(rgbDe(s) === card ? [] : [`SUPERFICIE ${rgbDe(s)} × --tw-card ${card}`])];
};
const TOM = { active: "ok", pending: "pendente", error: "erro" };
const tomDiff = (web, settings) => {
  const doWeb = pares(bloco(web, "TOM_DO_ESTADO"), /(?<k>\w+):\s*"(?<v>ok|pendente|erro)"/g);
  const doSettings = pares(bloco(settings, "OF_PILL_CLASS"), /(?<k>\w+):\s*"(?<v>\w+)"/g);
  return diff(doWeb, Object.fromEntries(Object.entries(doSettings).map(([k, v]) => [k, TOM[v] ?? `?${v}`])));
};

test("cores dos bancos do /painel == coresBanco do app (mesmas chaves, mesmos hex)", () => {
  assert.deepEqual(coresDiff(WEB(), APP()), []);
});
test("negativo: um hex trocado só de um lado aparece como diferença", () => {
  const app = APP().replace("#820AD1", "#820AD2");
  assert.deepEqual(coresDiff(WEB(), app), ["nubank: #820AD1 × #820AD2"]);
  assert.ok(coresDiff(WEB().replace('itau: "#EC7000", ', ""), APP()).length > 0); // banco a menos
});

test("SUPERFICIE do /painel == --plane-1 e --tw-card do base.css", () => {
  assert.deepEqual(superficieDiff(WEB(), BASE()), []);
});
test("negativo: superfície diferente do base.css aparece como diferença", () => {
  assert.equal(superficieDiff(WEB().replace('"#101013"', '"#111113"'), BASE()).length, 2);
});

test("TOM_DO_ESTADO == OF_PILL_CLASS do settings.html (active→ok, pending→pendente, error→erro)", () => {
  assert.deepEqual(tomDiff(WEB(), SETTINGS()), []);
});
test("negativo: estado com outro tom (updated virando pendente) aparece como diferença", () => {
  assert.deepEqual(tomDiff(WEB().replace('updated: "ok"', 'updated: "pendente"'), SETTINGS()), ["updated: pendente × ok"]);
});
