// Protótipo dashboard-v2: formato compacto a partir de R$ 1 milhão e o CDB do banco
// contado só pelo total, como caixinhas (nunca como investimento).
import { test } from "node:test";
import assert from "node:assert/strict";
import { axisMoney, money, money0, moneyBig, signed, signed0, signedBig, tone } from "../../webapp/src/dashboard/lib/format.js";
import { BANK_CDB, BANK_CDB_TOTAL, INVESTMENTS } from "../../webapp/src/dashboard/lib/data.js";
import { caixinhasTotal, goalsTotal, netWorth } from "../../webapp/src/dashboard/lib/model.js";

test("abaixo de 1 milhão o texto é idêntico ao de hoje", () => {
  for (const n of [0, 120, -1440, 511.4, 999999.4, -999999]) {
    assert.equal(moneyBig(n), money0(n));
    assert.equal(moneyBig(n, money), money(n));
    assert.equal(signedBig(n), signed0(n));
  }
});

test("a partir de 1 milhão vira compacto, com o sinal do signed0", () => {
  assert.equal(moneyBig(1_200_000), "R$ 1,2 mi");
  assert.equal(moneyBig(-1_440_000, money), "−R$ 1,4 mi");
  assert.equal(signedBig(-1_200_000), "−R$ 1,2 mi");
  assert.equal(signedBig(3_000_000_000), "+R$ 3 bi");
});

test("eixo: negativo com o menos antes do R$, positivo como antes", () => {
  const axis = (n) => axisMoney(n).replace(/\u00a0/g, " "); // o compacto separa "mil" com espaço fixo
  assert.equal(axis(-20000), "−R$ 20 mil");
  assert.equal(axis(-500), "−R$ 500");
  assert.equal(axis(4000), "R$ 4 mil");
  assert.equal(axis(0), "R$ 0");
});

test("o que arredonda a zero sai sem sinal; negativo de verdade mantém o −", () => {
  assert.deepEqual([money0(-0), money0(-0.4), money(-0.004), signed0(-0.3), signed(0.001), signedBig(-0.2)], ["R$ 0", "R$ 0", "R$ 0,00", "R$ 0", "R$ 0,00", "R$ 0"]);
  assert.deepEqual([money0(-84), signed0(-84), signedBig(-84), money(-0.01), signed0(84)], ["−R$ 84", "−R$ 84", "−R$ 84", "−R$ 0,01", "+R$ 84"]);
});

test("negativo sem sinal próprio usa o − tipográfico, como o signed0", () => {
  assert.deepEqual([money0(-84), money(-45), moneyBig(-2_000_000)], ["−R$ 84", "−R$ 45,00", "−R$ 2 mi"]);
});

test("tom da simulação: o que aparece como R$ 0 é neutro", () => {
  assert.deepEqual([12, 0.6, 0.02, 0, -0.4, -0.6, -12].map(tone), ["gain", "gain", "", "", "", "warn", "warn"]);
});

test("CDB do banco: só o total, contado em caixinhas e fora dos investimentos", () => {
  assert.equal(BANK_CDB_TOTAL, 1512.8); // soma exata das posições
  assert.deepEqual(BANK_CDB.positions, [612.4, 388.15, 201.73, 150, 96.52, 64]);
  assert.equal(INVESTMENTS.filter((x) => /nubank|cdb/i.test(x.label)).length, 0);
  assert.equal(goalsTotal(), 12730);
  assert.equal(caixinhasTotal(), 12730 + 1512.8);
  const today = netWorth().at(-1);
  assert.equal(today.caixinhas, caixinhasTotal());
  assert.equal(today.investimentos, 2728.75);
  // Só a divisão entre caixinhas e investimentos muda: o patrimônio segue o de antes (R$ 19.807).
  assert.equal(today.total, 19806.97);
});
