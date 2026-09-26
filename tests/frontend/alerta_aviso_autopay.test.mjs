/**
 * Q42: o banner de alertas separa o aviso de vencimento do autopay (linha de
 * `recurring_charges` sem lançamento, `launched:false`) do "Piggy lançou" das
 * linhas antigas do cobrador (`launched:true`). O aviso não pode dizer que o
 * Piggy lançou — nada foi lançado.
 *
 * Controle NEGATIVO (medido): com o `renderAlerts` ignorando `a.launched`
 * (sempre o texto "Piggy lançou"), os dois casos de aviso ficam vermelhos; o
 * positivo (launched:true) segue verde.
 *
 * Rodar: node --test tests/frontend/alerta_aviso_autopay.test.mjs
 */
import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import { abrirBrowser, fecharBrowser, loadDashboardJs } from "./_dashboard_loader.mjs";

let page;
before(async () => { await abrirBrowser(); page = await loadDashboardJs(); });
after(fecharBrowser);

const banner = (alerta) => page.evaluate((a) => {
  renderAlerts([{ type: "recurring_charged", charge_id: 1, amount: 120, charged_at: new Date().toISOString(), ...a }]);
  return document.getElementById("alert-banner").textContent.replace(/\s+/g, " ").trim();
}, alerta);

test("aviso na conta: dia de débito no banco, sem 'Piggy lançou'", async () => {
  const t = await banner({ name: "Aluguel", payment_type: "account", launched: false });
  assert.match(t, /Aluguel R\$\s?120,00: dia de débito no banco hoje\./);
  assert.doesNotMatch(t, /Piggy lançou/);
});

test("aviso no cartão: dia de cobrança no cartão, sem 'Piggy lançou'", async () => {
  const t = await banner({ name: "Streaming", payment_type: "credit_card", launched: false });
  assert.match(t, /Streaming R\$\s?120,00: dia de cobrança no cartão hoje\./);
  assert.doesNotMatch(t, /Piggy lançou/);
});

test("positivo: linha antiga que lançou continua 'Piggy lançou'", async () => {
  const t = await banner({ name: "Academia", payment_type: "account", launched: true });
  assert.match(t, /Piggy lançou Academia R\$\s?120,00 da conta hoje\./);
});
