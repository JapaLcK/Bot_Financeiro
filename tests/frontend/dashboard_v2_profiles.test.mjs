/**
 * Protótipo dashboard-v2, perfis do Resumo (webapp/src/dashboard/lib/profiles.js), sem
 * navegador: a leitura do layout salvo, o corte por plano e a paridade com o backend.
 *
 *   · o layout salvo é entrada que o usuário (ou uma versão velha do painel) deixou no
 *     navegador: JSON ruim, formato errado, id desconhecido ou repetido caem para o preset
 *     ou somem, sem quebrar; o id travado some da tela e continua salvo;
 *   · storage que lança não derruba nada: tudo segue em memória;
 *   · o plano mínimo de cada bloco pago é cópia de FEATURE_MIN_TIER_V2
 *     (core/services/plan_service.py) e a ordem dos planos, de TIER_ORDER
 *     (core/services/plan_limits.py): este teste compara as cópias (CLAUDE.md §0.7).
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

// localStorage de mentira, antes do import (o módulo não o lê no carregamento, mas o
// teste de "storage que lança" o troca depois).
const store = new Map();
const fake = {
  getItem: (k) => (store.has(k) ? store.get(k) : null),
  setItem: (k, v) => store.set(k, String(v)),
  removeItem: (k) => store.delete(k),
};
Object.defineProperty(globalThis, "localStorage", { configurable: true, get: () => fake });

const P = await import("../../webapp/src/dashboard/lib/profiles.js");
const { PROFILES, FEATURE_TIER, WIDGET_FEATURE, TIERS, locked, readLayout, saveLayout, readProfile, saveProfile } = P;

// Os blocos que o painel conhece hoje (DEFAULT + EXTRA de parts/Board.tsx).
const KNOWN = ["hero", "resumo", "categorias", "calendario", "simulador", "compromissos", "piggy", "metas", "patrimonio", "fatura", "wealth", "renda", "rendimento", "parcelas"];
const PRESET = ["resumo", "hero", "metas"];
// Cada caso usa um perfil próprio: o módulo lembra em memória o que ele mesmo escreveu.
const salvo = (perfil, raw) => store.set(`pigbank.dashboard.layout.v1.${perfil}`, raw);

test("layout salvo ilegível ou fora do formato: vale o preset", () => {
  const casos = { ruim1: "{nao é json", ruim2: '{"hero":1}', ruim3: '"hero"', ruim4: "null", ruim5: "42" };
  for (const [perfil, raw] of Object.entries(casos)) {
    salvo(perfil, raw);
    assert.deepEqual(readLayout(perfil, PRESET, KNOWN, "pro"), PRESET, perfil);
  }
  assert.deepEqual(readLayout("nunca-salvou", PRESET, KNOWN, "pro"), PRESET);
});

test("id desconhecido e repetido somem; o ausente do salvo não aparece", () => {
  salvo("sujo", JSON.stringify(["gastador", "metas", "hero", "metas", "xyz", 7, null, "renda", "hero"]));
  assert.deepEqual(readLayout("sujo", PRESET, KNOWN, "pro"), ["metas", "hero", "renda"]);
  const investir = PROFILES.find((p) => p.id === "investir");
  assert.deepEqual(readLayout("investir-preset", investir.preset, KNOWN, "pro"), ["patrimonio", "rendimento", "wealth", "simulador", "metas", "resumo", "piggy"]);
});

test("[] é um painel vazio válido, não o preset", () => {
  salvo("vazio", "[]");
  assert.deepEqual(readLayout("vazio", PRESET, KNOWN, "pro"), []);
});

test("bloco travado sai da tela sem reescrever o salvo; no plano certo ele volta", () => {
  const raw = JSON.stringify(["simulador", "resumo", "hero"]);
  salvo("travado", raw);
  assert.deepEqual(readLayout("travado", PRESET, KNOWN, "plus"), ["resumo", "hero"]);
  assert.deepEqual(readLayout("travado", PRESET, KNOWN, "essencial"), ["resumo"]);
  assert.equal(store.get("pigbank.dashboard.layout.v1.travado"), raw);
  assert.deepEqual(readLayout("travado", PRESET, KNOWN, "pro"), ["simulador", "resumo", "hero"]); // positivo
});

test("salvar, apagar e o perfil: ida e volta", () => {
  saveLayout("rt", ["metas", "resumo"]);
  assert.equal(store.get("pigbank.dashboard.layout.v1.rt"), '["metas","resumo"]');
  assert.deepEqual(readLayout("rt", PRESET, KNOWN, "pro"), ["metas", "resumo"]);
  saveLayout("rt", null);
  assert.equal(store.has("pigbank.dashboard.layout.v1.rt"), false);
  assert.deepEqual(readLayout("rt", PRESET, KNOWN, "pro"), PRESET);
  assert.equal(readProfile(), null);
  store.set("pigbank.dashboard.profile.v1", '"inexistente"');
  assert.equal(readProfile(), null);
  saveProfile("dividas");
  assert.equal(store.get("pigbank.dashboard.profile.v1"), '"dividas"');
  assert.equal(readProfile(), "dividas");
});

test("storage que lança: preset na leitura e tudo segue em memória", () => {
  Object.defineProperty(globalThis, "localStorage", { configurable: true, get: () => { throw new Error("SecurityError"); } });
  try {
    assert.deepEqual(readLayout("sem-storage", PRESET, KNOWN, "pro"), PRESET);
    saveLayout("sem-storage", ["hero"]);
    assert.deepEqual(readLayout("sem-storage", PRESET, KNOWN, "pro"), ["hero"]);
    saveProfile("investir");
    assert.equal(readProfile(), "investir");
  } finally {
    Object.defineProperty(globalThis, "localStorage", { configurable: true, get: () => fake });
  }
});

test("locked nos três planos", () => {
  const tabela = Object.fromEntries(TIERS.map((plan) => [plan, KNOWN.filter((id) => locked(id, plan))]));
  assert.deepEqual(tabela, { essencial: ["hero", "simulador", "piggy"], plus: ["simulador"], pro: [] });
  assert.equal(locked("renda", "essencial"), false); // bloco sem recurso pago é de todos
});

test("paridade: plano mínimo e ordem dos planos iguais aos do backend", () => {
  const svc = readFileSync(new URL("../../core/services/plan_service.py", import.meta.url), "utf8");
  const bloco = svc.match(/FEATURE_MIN_TIER_V2 = \{([\s\S]*?)\n\}/)[1];
  const back = Object.fromEntries([...bloco.matchAll(/"(\w+)":\s*"(\w+)"/g)].map((m) => [m[1], m[2]]));
  for (const feature of Object.values(WIDGET_FEATURE)) assert.equal(FEATURE_TIER[feature], back[feature], feature);
  assert.deepEqual(Object.keys(FEATURE_TIER).sort(), [...new Set(Object.values(WIDGET_FEATURE))].sort());

  const lim = readFileSync(new URL("../../core/services/plan_limits.py", import.meta.url), "utf8");
  const ordem = JSON.parse(lim.match(/TIER_ORDER: dict\[str, int\] = (\{[^}]*\})/)[1]);
  assert.deepEqual(TIERS, Object.keys(ordem).filter((t) => t !== "free").sort((a, b) => ordem[a] - ordem[b]));
});
