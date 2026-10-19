/**
 * O veredito do POST do "Atualizar agora" do /painel (porto `webapp/src/dashboard/lib/atualizarBanco.ts`, #1076 PR 2) contra o `refreshVerdict` do `/settings` (frontend/settings.html), a fonte da regra.
 * Sem navegador: a função é extraída do HTML (`new Function`) e o porto é o `.ts` importado direto (Node 22.18+ tira os tipos; o porto não tem import nem sintaxe que precise de compilador).
 * A tabela cruza estado × motivo × `still_updating` × detalhe, sozinhos e em PAR (o erro de um banco nunca é escondido pelo outro), e compara só o TOM: erro, info (coletando) ou ok.
 * Fica vermelho se o porto mudar o julgamento (a guarda de motivo do `ruim`, o D1 do `coletando`, o D2 de `items:[]`): é o que prende as duas cópias da regra (§0.7).
 *
 * FORA da tabela, de propósito (as duas diferenças que existem e o backend não alcança):
 *  - `reason` que não é string (número, booleano): o servidor manda string ou null, e a borda do porto (`refreshDe`, conta/api.ts) transforma o resto em null; o web leria `0` como "0" (erro).
 *  - `items: []` com `ok: false` E `sync.still_updating > 0`: o web dá info (a contagem vem antes), o porto dá falha; mas `still_updating` é a SOMA dos itens (pluggy_sync.py), então com `items` vazio ela é 0.
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import { fileURLToPath } from "node:url";
import * as porto from "../../webapp/src/dashboard/lib/atualizarBanco.ts";

const html = fs.readFileSync(fileURLToPath(new URL("../../frontend/settings.html", import.meta.url)), "utf8");
const recorte = (de, ate) => { const i = html.indexOf(de), j = html.indexOf(ate, i); assert.ok(i >= 0 && j > i, `não achei o trecho ${de} … ${ate} do settings.html`); return html.slice(i, j); };
const refreshVerdict = new Function(`${recorte("const OF_VERDICT_OK", "/* ── Disconnect")}\nreturn refreshVerdict;`)();

/** O tom que o porto dá ao POST: roda `atualizar` com o mundo falso (relógio e rede injetados) e lê o desfecho. */
async function tomDoPorto(sync, connections) {
  const fases = []; let ok = false; let t = 0;
  const d = {
    agora: () => t, esperar: async (ms) => { t += ms; }, atual: () => true,
    enviar: async () => ({ connections, sync }),
    // a coleta termina na 1ª leitura (sync novo, em dia): o que interessa é se o porto ACOMPANHOU, não como a coleta acaba
    ler: async () => connections.map((c) => ({ ...c, last_sync_at: "NOVO", ui: { ...c.ui, state: "updated" } })),
    aoMudar: (e) => fases.push(e.fase + (e.falha ? "!" : "")), recarregar() {}, falhou() {}, transitoria: () => true, silencioso: () => false,
    sucesso: () => { ok = true; }, aviso() {}, anunciar() {},
  };
  await porto.atualizar(d);
  if (fases[1] === "acompanhando") return "info";
  if (ok) return "ok";
  if (fases.includes("resultado") || fases.includes("ocioso!")) return "error";
  return `?${fases.join()}`;
}

const ESTADOS = ["updated", "rate_limited", "updating", "partial", "error_recoverable", "no_accounts", "needs_user_action", "item_missing", "paused", "removed", "inventado", "", "UPDATED", "Updating"];
const MOTIVOS = [null, "", "ok", "OK", " ok", "no_accounts", "NO_ACCOUNTS", "read_failed", "sync_failed", "rate_limited"];
const DETALHES = [null, "Detalhe do servidor"];
const todos = [];
for (const state of ESTADOS) for (const reason of MOTIVOS) for (const still_updating of [false, true]) for (const detail of DETALHES) {
  todos.push({ item_id: "i1", institution: "Nubank", state, label: `L-${state}`, detail, reason, still_updating });
}

const conexoesDe = (itens) => itens.map((i) => ({ institution_name: i.institution, last_sync_at: "VELHO", provider_item_id: i.item_id, ui: { state: i.state, label: i.label, detail: i.detail } }));
/** `sync.still_updating` é a soma dos itens (pluggy_sync.py), como o servidor manda. */
async function compara(itens, ok = true) {
  const web = refreshVerdict({ ok, items: itens, still_updating: itens.filter((i) => i.still_updating).length }).tone;
  const tom = await tomDoPorto({ ok, items: itens }, conexoesDe(itens));
  return { web: web === "error" || web === "info" ? web : "ok", porto: tom, itens };
}

test("o tom do porto é o do refreshVerdict do settings.html: itens sozinhos e em par, estado × motivo × still_updating × detalhe", async () => {
  const diferentes = []; const tons = new Set(); let n = 0;
  const confere = async (itens, ok) => { const r = await compara(itens, ok); n++; tons.add(r.web); if (r.web !== r.porto) diferentes.push(`web=${r.web} porto=${r.porto} ${JSON.stringify(itens.map((i) => [i.state, i.reason, i.still_updating, i.detail]))}`); };
  for (const a of todos) await confere([a]);
  const amostra = todos.filter((_, k) => k % 7 === 0);
  for (const a of amostra) for (const b of amostra) await confere([a, { ...b, item_id: "i2", institution: "Inter" }]);
  for (const ok of [true, false, undefined]) await confere([], ok); // D2: items:[] com ok:false é falha; com ok:true ou ausente, em dia
  assert.deepEqual(diferentes.slice(0, 5), [], `${diferentes.length} de ${n} casos divergem do settings.html`);
  assert.ok(n > 6000, `a tabela encolheu: ${n} casos`);
  assert.deepEqual([...tons].sort(), ["error", "info", "ok"], "a tabela tem de exercer os três tons, senão não mede nada");
});
