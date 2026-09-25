// Protótipo dashboard-v2: os cálculos dos três blocos de perfil (lib/model.js), sem
// navegador. Os números saem dos dados sintéticos de lib/data.js, então mudar um dado
// lá muda a conta aqui de propósito.
import { test } from "node:test";
import assert from "node:assert/strict";

const M = await import("../../webapp/src/dashboard/lib/model.js");
const D = await import("../../webapp/src/dashboard/lib/data.js");

test("renda: 6 meses até o atual; abr–jun do histórico, jul–set dos lançamentos", () => {
  const r = M.incomeHistory();
  assert.deepEqual(r.map((x) => x.key), ["2026-04", "2026-05", "2026-06", "2026-07", "2026-08", "2026-09"]);
  assert.deepEqual(r.map((x) => x.value), [3650, 4600, 3400, 3800, 4050, 4300]); // bolsa 3400 + freela do mês
  assert.equal(M.fixedMonthly(), 1310.1);
  assert.equal(M.reserveMonths(), 4380 / 1310.1);
});

test("parcelas: cada mês soma só as que ainda faltam, e a última acaba no mês certo", () => {
  const p = M.installmentsAhead(6);
  assert.deepEqual(p.months.map((x) => x.key), ["2026-10", "2026-11", "2026-12", "2027-01", "2027-02", "2027-03"]);
  assert.deepEqual(p.months.map((x) => x.value), [511.35, 511.35, 511.35, 269.85, 189.9, 189.9]);
  assert.equal(p.last.getFullYear() * 100 + p.last.getMonth() + 1, 202703); // celular: faltam 6 de 10
});

test("rendimento: ganho sobre o saldo de hoje, % do CDI composto em 12 meses", () => {
  const base = D.INVESTMENTS.reduce((s, x) => s + x.amount, 0);
  const y = M.yieldVsCdi();
  assert.equal(y.month.value, Math.round((base - base / 1.0102) * 100) / 100);
  assert.ok(Math.abs(y.month.ofCdi - 1.02 / 1.13) < 1e-9);
  const acc = (k) => D.YIELDS.reduce((p, x) => p * (1 + x[k]), 1) - 1;
  assert.ok(Math.abs(y.year.ofCdi - acc("carteira") / acc("cdi")) < 1e-9);
  assert.ok(y.year.value > y.month.value && y.year.value < base);
});
