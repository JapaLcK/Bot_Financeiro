/**
 * "Sobrou este mês" com o mês ZERADO não pode sair verde.
 *
 * O detalhe do card (`openSobrouDetail`) trazia dois literais de cor:
 *
 *   1. `row("Receitas do mês", "+ " + fmt(s.inc), "sd-plus")` — a classe
 *      `.sd-plus` é `var(--green)` (dashboard.css), então um mês sem nenhuma
 *      receita escrevia "+ R$ 0,00" em VERDE, do mesmo jeito que um mês com
 *      R$ 3.000 de salário. Verde é a cor de valor positivo na identidade do
 *      produto; zero não é positivo.
 *   2. `<span class="ld-v ${deficit ? "neg" : "pos"}">` com `deficit = s.sav < 0`
 *      — o zero caía no ELSE e o total também saía verde.
 *
 * Os dois passaram a sair do `_toneClass`, que devolve classe NENHUMA no zero
 * (a mesma regra do `_toneMoney`, e a mesma já aplicada em `.stat-delta`).
 *
 * O que ESTE arquivo mede, e como: roda o `render()` real (é ele que preenche
 * `_sobrouDetail`, ~45 linhas depois do início — muito antes do tail que
 * estoura com o DOM reduzido do loader) e depois `openSobrouDetail()`, que
 * constrói o próprio modal via `_ensureSobrouDetailModal`. A asserção olha o
 * HTML de `#sd-rows`, que é o que o usuário lê.
 *
 * Controle positivo obrigatório: o caso 500/100 prova que o caminho legítimo
 * continua VERDE — sem ele a suíte passaria num código que tirou a cor de tudo.
 *
 * Rodar: node --test --test-concurrency=1 tests/frontend/sobrou_zero_neutro.test.mjs
 */
import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import { abrirBrowser, fecharBrowser, loadDashboardJs } from "./_dashboard_loader.mjs";

before(abrirBrowser);
after(fecharBrowser);

/** Renderiza um mês e abre o detalhe do Sobrou; devolve o que saiu na tela. */
const abrirDetalhe = (page, { inc, exp }) => page.evaluate((m) => {
  try {
    render({
      year: 2026, month: 8, balance: 0,
      monthly_income: m.inc, monthly_expense: m.exp,
      expense_categories: [], launches: [], pockets: [], investments: [],
      credit_cards: [], budgets: {},
    });
  } catch { /* tail do render, fora do que este teste mede */ }
  openSobrouDetail();
  const rows = document.getElementById("sd-rows");
  const total = rows.querySelector(".sd-total .ld-v");
  return {
    html: rows.innerHTML,
    receitas: rows.querySelector(".ld-row .ld-v").className,
    totalCls: total.className,
    totalTxt: total.textContent,
  };
}, { inc, exp });

test("mês zerado: receitas e total sem classe de cor", async () => {
  const page = await loadDashboardJs();
  const r = await abrirDetalhe(page, { inc: 0, exp: 0 });

  assert.ok(r.html.includes("R$ 0,00"), "o detalhe não renderizou: " + r.html);
  assert.ok(!r.html.includes("sd-plus"), "‘+ R$ 0,00’ saiu verde (.sd-plus)");
  assert.equal(r.receitas.trim(), "ld-v", "a linha de receitas ganhou classe de cor");
  assert.ok(!/\bpos\b/.test(r.totalCls), "o total zerado saiu verde (.pos): " + r.totalCls);
  assert.ok(!/\bneg\b/.test(r.totalCls), "o total zerado saiu vermelho (.neg): " + r.totalCls);
  await page.close();
});

test("controle positivo: mês com receita real continua verde", async () => {
  const page = await loadDashboardJs();
  const r = await abrirDetalhe(page, { inc: 500, exp: 100 });

  assert.ok(r.html.includes("sd-plus"), "as receitas perderam o verde: " + r.html);
  assert.match(r.totalCls, /\bpos\b/, "o total positivo perdeu o verde: " + r.totalCls);
  await page.close();
});

test("controle positivo: mês no vermelho continua vermelho", async () => {
  const page = await loadDashboardJs();
  const r = await abrirDetalhe(page, { inc: 100, exp: 500 });
  assert.match(r.totalCls, /\bneg\b/, "o déficit perdeu o vermelho: " + r.totalCls);
  await page.close();
});

/**
 * CARD e MODAL na MESMA medição. Medir só o modal foi o erro da rodada
 * anterior: o conserto entrou no `openSobrouDetail` e o card da Início, que é
 * quem ABRE esse modal e lê o mesmo `sav`, ficou com `${sav>=0?'pos':'neg'}` e
 * `sav < 0` crus. O resultado era pior que o bug original — os dois discordando
 * entre si, com o "R$ -0,00" vermelho sobrevivendo na tela mais visível.
 */
const cardEModal = (page, { inc, exp }) => page.evaluate((m) => {
  try {
    render({
      year: 2026, month: 8, balance: 0,
      monthly_income: m.inc, monthly_expense: m.exp,
      expense_categories: [], launches: [], pockets: [], investments: [],
      credit_cards: [], budgets: {},
    });
  } catch { /* tail do render, fora do que este teste mede */ }
  const stat = document.querySelector("#grid .ov-stat-clickable");
  const card = {
    rotulo: stat.querySelector(".ov-lbl").textContent.trim(),
    classe: stat.querySelector(".ov-val").className,
    valor: stat.querySelector(".ov-val").textContent.trim(),
    aria: stat.getAttribute("aria-label"),
  };
  openSobrouDetail();
  const total = document.querySelector("#sd-rows .sd-total");
  return {
    card,
    modal: {
      rotulo: total.querySelector(".ld-k").textContent.trim(),
      classe: total.querySelector(".ld-v").className,
      valor: total.querySelector(".ld-v").textContent.trim(),
      titulo: document.getElementById("sd-title").textContent.trim(),
    },
  };
}, { inc, exp });

const cor = (cls) => (/\bpos\b/.test(cls) ? "verde" : /\bneg\b/.test(cls) ? "vermelho" : "neutro");

// [nome, inc, exp, cor esperada, rótulo esperado]
const CASOS = [
  ["zero exato", 0, 0, "neutro", "Sobrou este mês"],
  // sav = -2.27e-13: o resíduo de reduce que o docstring do `_toneMoney` cita.
  // O `_fmtBRL` escreve "R$ -0,00" e o card dizia "Déficit do mês" em vermelho.
  ["resíduo de float", 1800.30, 1500.20 + 300.10 + 1e-13, "neutro", "Sobrou este mês"],
  ["sobra de verdade", 500, 100, "verde", "Sobrou este mês"],
  ["déficit de verdade", 100, 500, "vermelho", "Déficit do mês"],
];

for (const [nome, inc, exp, corEsperada, rotulo] of CASOS) {
  test(`card e modal concordam: ${nome}`, async () => {
    const page = await loadDashboardJs();
    const r = await cardEModal(page, { inc, exp });
    const onde = `card="${r.card.rotulo} ${r.card.valor} (${r.card.classe})" modal="${r.modal.rotulo} ${r.modal.valor} (${r.modal.classe})"`;

    assert.equal(cor(r.card.classe), corEsperada, `cor do CARD — ${onde}`);
    assert.equal(cor(r.modal.classe), corEsperada, `cor do MODAL — ${onde}`);
    assert.equal(cor(r.card.classe), cor(r.modal.classe), `card e modal com cores diferentes — ${onde}`);

    assert.ok(r.card.rotulo.startsWith(rotulo), `rótulo do CARD — ${onde}`);
    assert.equal(r.modal.rotulo, rotulo, `rótulo do MODAL — ${onde}`);
    assert.equal(r.modal.titulo, rotulo, `título do MODAL — ${onde}`);
    assert.ok(r.card.aria.startsWith(rotulo), `aria-label do CARD — "${r.card.aria}"`);
  });
}
