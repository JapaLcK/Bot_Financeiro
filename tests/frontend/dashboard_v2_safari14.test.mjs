// Protótipo dashboard-v2: o model tem de rodar sem Array.prototype.at (Safari 14 não
// tem; o alvo safari14 do build só transpila sintaxe, não faz polyfill de API).
// O NetWorth chama netWorth() no carregamento do módulo: se lançar, o painel não monta.
import { test } from "node:test";
import assert from "node:assert/strict";

delete Array.prototype.at; // antes do import: é o que o iOS 14 vê
const { HORIZONS, monthSummary, netWorth, trajectory } = await import("../../webapp/src/dashboard/lib/model.js");
const { MONTHS } = await import("../../webapp/src/dashboard/lib/data.js");

test("sem Array.prototype.at: resumo, trajetória e patrimônio calculam", () => {
  assert.equal(Array.prototype.at, undefined);
  for (const key of MONTHS) assert.equal(typeof monthSummary(key).opening, "number"); // saldo inicial do mês
  const past = trajectory(MONTHS[0], "mes"); // mês fechado
  assert.equal(past.end, past.points[past.points.length - 1]);
  for (const h of Object.keys(HORIZONS)) { // mês corrente, com previsão
    const t = trajectory(MONTHS[MONTHS.length - 1], h);
    assert.equal(t.end, t.points[t.points.length - 1]);
    assert.equal(t.end.real, false);
  }
  const rows = netWorth();
  assert.equal(rows[rows.length - 1].total, 19806.97);
});
