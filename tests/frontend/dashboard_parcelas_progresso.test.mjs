/**
 * Parcelamentos do /dashboard legado: as abas e o "pagas/N" seguem `progresso` (a regra do card da IA,
 * tests/fixtures/parcelas_progresso.json), não a contagem das parcelas VISTAS (n_pending / parcelas.length).
 *
 * Controle NEGATIVO: volte o filtro das abas para `(g.n_pending || 0) === 0` (ou o texto para
 * `${g.n_paid}/${parcelas.length}`) em `frontend/dashboard.js` — "só a 3/12 paga" e "5/12" ficam vermelhos.
 * Controle POSITIVO: o parcelamento manual 3x com a 1ª paga segue "1/3" na aba ativa.
 *
 * Rodar: node --test tests/frontend/dashboard_parcelas_progresso.test.mjs
 */
import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import { abrirBrowser, fecharBrowser, loadDashboardJs } from "./_dashboard_loader.mjs";

before(abrirBrowser);
after(fecharBrowser);

const pc = (no, pago, extra = {}) => ({ installment_no: no, is_paid: pago, valor: 100, due_date: "2026-11-10", is_next: false, ...extra });
const grupo = (name, installments_total, parcelas) => {
  const n_paid = parcelas.filter((p) => p.is_paid).length;
  const proxima = parcelas.find((p) => !p.is_paid);
  return {
    group_id: name, name, categoria: "casa", card_name: "Banco", installments_total, parcelas,
    total: 100 * parcelas.length, paid_amount: 100 * n_paid, remaining_amount: 100 * (parcelas.length - n_paid),
    n_paid, n_pending: parcelas.length - n_paid, next_due_date: proxima ? "2026-11-10" : null, valor_parcela: 100,
  };
};
const GRUPOS = [
  grupo("Sofa", 12, [pc(5, false, { is_next: true })]),                       // 5/12 em aberto: 4 de 12
  grupo("Notebook", 12, [pc(3, true)]),                                       // só a 3 vista, paga: ativa, 3 de 12 (antes ia ao histórico)
  grupo("Quitada", 12, [pc(12, true)]),                                       // 12 de 12: histórico
  grupo("Geladeira", 3, [pc(1, true), pc(2, false, { is_next: true }), pc(3, false)]), // manual: 1 de 3
  grupo("Fone", 12, [pc(12, false, { is_next: true })]),                      // 12/12 em aberto: esta SIM é a última
];

/** Renderiza as duas abas e devolve, por aba, o nome e o texto de progresso de cada parcelamento. */
const abas = (page) => page.evaluate((grupos) => {
  document.body.insertAdjacentHTML("beforeend", '<div id="installments-stats"></div><div id="installments-list"></div>');
  const ler = (tab) => {
    _instTab = tab;
    renderInstallmentsView(grupos);
    return [...document.querySelectorAll("#installments-list .inst-card")].map((c) => ({
      nome: c.querySelector(".inst-name").textContent,
      texto: c.textContent.match(/\d+\/\d+(?: · [^\n<]*)?/)?.[0].trim(),
    }));
  };
  return { active: ler("active"), history: ler("history") };
}, GRUPOS);

test("as abas e o pagas/N seguem `progresso`: a 3/12 paga é ativa e a 12/12 é histórico", async () => {
  const page = await loadDashboardJs();
  const r = await abas(page);
  const por = (aba) => Object.fromEntries(aba.map((g) => [g.nome, g.texto]));
  const ativos = por(r.active);
  assert.deepEqual(Object.keys(ativos).sort(), ["Fone", "Geladeira", "Notebook", "Sofa"]);
  assert.match(ativos.Fone, /^11\/12 · última /); // positivo: a 12 em aberto é a última
  assert.match(ativos.Sofa, /^4\/12 · próx\. /); // faltam 8: não é a última
  assert.equal(ativos.Notebook, "3/12", "sem parcela a vencer vista, só o progresso (nem 'quitado' nem data)");
  assert.match(ativos.Geladeira, /^1\/3 · próx\. /);
  assert.deepEqual(por(r.history), { Quitada: "12/12 · quitado" });
  await page.close();
});
