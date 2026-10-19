/**
 * /painel#/milhao, a conta pura de webapp/src/dashboard/lib/milhao.ts (sem navegador): meta = o próximo
 * milhão ACIMA do patrimônio, juros compostos mensais com taxa fixa, teto de 600 meses, dinheiro negativo
 * que não rende. Os valores esperados são escritos à mão (conta de cabeça), não saem da própria lib.
 *
 * Rodar:  node --test tests/frontend/dashboard_v2_milhao_projecao.test.mjs
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { duracao, projetar } from "../../webapp/src/dashboard/lib/milhao.ts";

const P = (patrimonio, aporte, taxaAnual, inicio = "2026-10") => projetar({ patrimonio, aporte, taxaAnual, inicio });

test("sem rendimento, o aporte sozinho: R$ 2.000 por mês levam 500 meses", () => {
  assert.equal(P(0, 2000, 0).meses, 500);
});

test("o prazo é de 600 meses: chegar no 600 vale, no 601 é sem previsão", () => {
  assert.equal(P(400000, 1000, 0).meses, 600); // 400.000 + 600 × 1.000 = 1.000.000 exatos
  const fora = P(399000, 1000, 0);
  assert.equal(fora.meses, null);
  assert.equal(fora.chegada, null);
  assert.equal(fora.pontos.length, 601);
});

test("12% ao ano: R$ 100 viram R$ 112,00 em 12 meses (a taxa mensal é a equivalente, não 1%)", () => {
  assert.ok(Math.abs(P(100, 0, 12).pontos[12] - 112) < 1e-9);
});

test("patrimônio negativo não rende juro (nem juro negativo) e o aporte entra", () => {
  const r = P(-1000, 1000, 12);
  assert.equal(r.pontos[1], 0);
  assert.equal(r.meses, 250);
});

test("R$ 100 mil a 10%, sem aporte: chega em 290 meses, dezembro de 2050, 24 anos e 2 meses", () => {
  const r = P(100000, 0, 10);
  assert.deepEqual([r.meses, r.chegada, r.anos, r.mesesResto], [290, "2050-12", 24, 2]);
  assert.equal(duracao(r.anos, r.mesesResto), "24 anos e 2 meses");
});

test("exatamente R$ 1 milhão já passou: a meta vira R$ 2 milhões", () => {
  const r = P(1000000, 0, 10);
  assert.equal(r.meta, 2e6);
  assert.equal(r.faltaCentavos, 1e8);
});

test("R$ 1.250.000 a 10%: meta 2 milhões em 60 meses (5 anos)", () => {
  const r = P(1250000, 0, 10);
  assert.deepEqual([r.meta, r.meses, r.anos, r.mesesResto], [2e6, 60, 5, 0]);
});

test("R$ 500 a 10%, sem aporte: sem previsão", () => {
  assert.equal(P(500, 0, 10).meses, null);
});

test("falta em centavos e percentual (para baixo, nunca 100 abaixo da meta)", () => {
  const r = P(48250.37, 0, 10);
  assert.deepEqual([r.faltaCentavos, r.pct], [95174963, 4]);
  assert.equal(P(999999.99, 0, 0).pct, 99);
  assert.equal(P(-5, 0, 0).pct, 0);
});

test("o mês de chegada vira o ano: outubro + 3 meses = janeiro", () => {
  assert.equal(P(999000, 1000, 0, "2026-10").chegada, "2026-11"); // 1 mês
  assert.equal(P(997000, 1000, 0, "2026-10").chegada, "2027-01"); // 3 meses
});

test("duração: singular, plural e a parte zero some", () => {
  assert.equal(duracao(1, 0), "1 ano");
  assert.equal(duracao(0, 1), "1 mês");
  assert.equal(duracao(2, 1), "2 anos e 1 mês");
  assert.equal(duracao(0, 10), "10 meses");
});
