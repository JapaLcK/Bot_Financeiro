/**
 * A área do afiliado no /painel (webapp/src/dashboard/afiliado/): quem vê o item, o que a tela mostra e o que ela nunca mostra.
 * Em cada teste, o comentário de cima diz qual mutação o deixa vermelho. O saque está em dashboard_v2_afiliado_saque.test.mjs.
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { navegador } from "./_guia.mjs";
import { PROTOTIPO, RAIZ, servir } from "./_painel.mjs";
import { ABAIXO, ATIVA, COM_SAQUE, DESATIVADA, EM_REVISAO, REJEITADO, ZERO, comAfiliado, quadros, servidor } from "./_afiliado.mjs";

const browser = navegador();
const itemLateral = (page) => page.locator(".rail-list a", { hasText: "Afiliado" });

async function noCmdK(page) {
  await page.locator(".cmd-trigger").click();
  await page.locator(".cmdk input").fill("Afiliado");
  return page.locator(".cmdk").getByText("Afiliado", { exact: true });
}

// mutação: `noMenu` devolvendo sempre true (router.ts) ou `useAfiliado` sem o `=== true`: o item aparece para quem não é afiliado
for (const [caso, afiliado] of [["false", false], ["ausente (servidor antigo)", "ausente"]]) {
  test(`não afiliado (/me ${caso}): sem item na lateral, no ⌘K e no Menu do celular`, async () => {
    await comAfiliado(browser(), { afiliado, rota: "/", espera: "#board-profile" }, async (t) => {
      assert.equal(await itemLateral(t.page).count(), 0);
      assert.equal(await (await noCmdK(t.page)).count(), 0);
    });
    await comAfiliado(browser(), { afiliado, rota: "/", width: 390, height: 844, espera: "#board-profile" }, async (t) => {
      await t.page.locator(".tabbar").getByRole("button", { name: "Menu" }).click();
      await t.page.locator(".menu-telas").getByRole("link", { name: "Casa" }).waitFor();
      assert.equal(await t.page.locator(".menu-telas").getByRole("link", { name: "Afiliado" }).count(), 0);
    });
  });
}

// mutação: trocar `useAfiliado() ?` por `true ?` em PaginaAfiliado: a tela chama a API para quem não é afiliado
test("não afiliado em #/afiliado: o estado 'só para afiliados' com volta ao Resumo, e nenhum GET sai", async () => {
  await comAfiliado(browser(), { afiliado: false, corpo: null, espera: ".afil-fora" }, async (t) => {
    assert.match(await t.page.locator(".afil-fora").innerText(), /Esta área é só para afiliados\./);
    assert.equal(await t.page.locator(".afil-fora a").getAttribute("href"), "#/");
    await t.page.waitForTimeout(150);
    assert.equal(t.s.gets, 0, "GET /api/affiliate/me");
  });
});

// mutação: o `404 → null` do afiliadoQuery: o menu desatualizado cai na tela de erro em vez do estado certo
test("o /me diz afiliado e a API diz 404 (cadastro removido): o mesmo estado, sem erro", async () => {
  await comAfiliado(browser(), { corpo: null, espera: ".afil-fora" }, async (t) => {
    assert.match(await t.page.locator(".afil-fora").innerText(), /só para afiliados/);
  });
});

// mutação: tirar a rota `/afiliado` de ROUTES: sem item; tirar de NO_MONTH: o seletor de mês aparece; `noMenu` fora do App.tsx: a lateral não lista
test("afiliado: o item entre Casa e Ferramentas, aria-current, sem seletor de mês, e os 8 quadros com os números da fixture", async () => {
  await comAfiliado(browser(), {}, async (t) => {
    await t.page.locator(".afil-quadros").waitFor();
    const itens = await t.page.locator(".rail-list a").evaluateAll((a) => a.map((e) => e.textContent.trim()));
    const i = itens.indexOf("Afiliado");
    assert.deepEqual(itens.slice(i - 1, i + 2), ["Casa", "Afiliado", "Ferramentas"]);
    assert.equal(await t.page.locator('.rail-list a[aria-current="page"]').innerText(), "Afiliado");
    assert.equal(await t.page.locator(".month-switch").count(), 0, "NO_MONTH");
    assert.deepEqual(await quadros(t.page), {
      "Cadastros indicados": "12", "Clientes que pagaram": "6", "Comissões geradas": "R$ 350,00", "Em carência": "R$ 30,00",
      "Em revisão": "R$ 0,00", "Disponível": "R$ 120,00", "Em saque": "R$ 0,00", "Já pago": "R$ 200,00",
    });
    assert.match(await t.page.locator(".afil-quadros").innerText(), /1 reembolsado/);
    assert.match(await t.page.locator(".afil-quadros").innerText(), /libera 30 dias/);
    assert.equal(await t.page.locator(".afil-reembolsos").innerText(), "Reembolsos: 2 totais · 1 parcial");
    assert.equal(await t.page.locator("#afil-divulgar").count(), 1);
    assert.equal(await t.page.title(), "PigBank · Afiliado");
  });
});

// mutação: `noMenu` fora do MenuTelas.tsx ou do Command.tsx: um dos dois não lista
test("afiliado: o Menu do celular e o ⌘K levam à tela", async () => {
  await comAfiliado(browser(), { width: 390, height: 844, rota: "/", espera: "#board-profile" }, async (t) => {
    await t.page.locator(".tabbar").getByRole("button", { name: "Menu" }).click();
    await t.page.locator(".menu-telas").getByRole("link", { name: "Afiliado" }).click();
    await t.page.locator(".afil-quadros").waitFor();
    assert.equal(await t.page.evaluate(() => location.hash), "#/afiliado");
  });
  await comAfiliado(browser(), { rota: "/", espera: "#board-profile" }, async (t) => {
    await (await noCmdK(t.page)).first().click();
    await t.page.locator(".afil-quadros").waitFor();
    assert.equal(await t.page.evaluate(() => location.hash), "#/afiliado");
  });
});

// mutação: tirar `|| r.path === "/afiliado"` de ATIVAS: o hash abre a tela no protótipo e o menu a lista
test("protótipo (DEMO): sem a rota (#/afiliado vira o Resumo), sem item", async () => {
  const ctx = await browser().newContext({ viewport: { width: 1280, height: 800 }, reducedMotion: "reduce" });
  try {
    await servir(ctx, RAIZ);
    await ctx.addInitScript(() => localStorage.setItem("pigbank.dashboard.profile.v1", '"padrao"'));
    const page = await ctx.newPage();
    await page.goto(`${PROTOTIPO}#/afiliado`);
    await page.locator("#page-title").waitFor();
    assert.equal(await page.evaluate(() => location.hash), "#/");
    assert.equal(await itemLateral(page).count(), 0);
  } finally { await ctx.close(); }
});

const CLIP_OK = () => { window.__copiado = []; Object.defineProperty(navigator, "clipboard", { configurable: true, value: { writeText: async (t) => { window.__copiado.push(t); } } }); };
const CLIP_RECUSA = () => Object.defineProperty(navigator, "clipboard", { configurable: true, value: { writeText: () => Promise.reject(new Error("negado")) } });
const CLIP_AUSENTE = () => Object.defineProperty(navigator, "clipboard", { configurable: true, value: undefined });

// mutação: trocar `d.code` por `d.link` no botão de copiar código: o código copiado fica errado
test("copiar: código e link vão à área de transferência, cada um com o próprio aviso", async () => {
  await comAfiliado(browser(), { clipboard: CLIP_OK }, async (t) => {
    await t.page.getByRole("button", { name: "Copiar código" }).click();
    await t.page.getByText("Código copiado").waitFor();
    await t.page.getByRole("button", { name: "Copiar link" }).click();
    await t.page.getByText("Link copiado").waitFor();
    assert.deepEqual(await t.page.evaluate(() => window.__copiado), ["ANA2026", "https://pigbankai.com/r/ANA2026"]);
  });
});

// mutação: tirar o `Promise.resolve()` de lib/copiar.ts: clipboard ausente lança síncrono e sai como pageerror, sem aviso
for (const [caso, clipboard] of [["recusado", CLIP_RECUSA], ["ausente", CLIP_AUSENTE]]) {
  test(`copiar com o clipboard ${caso}: diz que falhou, sem exceção`, async () => {
    await comAfiliado(browser(), { clipboard }, async (t) => {
      await t.page.getByRole("button", { name: "Copiar link" }).click();
      await t.page.getByText("Não foi possível copiar").waitFor();
    });
  });
}

// mutação: tirar o `d.status === "disabled"` do aviso, ou esconder <Saque> para desativado
test("desativado: aviso claro, saldos, histórico e saque disponível", async () => {
  await comAfiliado(browser(), { corpo: DESATIVADA }, async (t) => {
    assert.match(await t.page.locator(".afil-aviso").innerText(), /desativado.*não gera novas comissões/s);
    assert.equal((await quadros(t.page))["Disponível"], "R$ 120,00");
    assert.equal(await t.page.locator("#afil-comissoes ~ .afil-lista li").count(), 6);
    assert.equal(await t.page.getByRole("button", { name: "Sacar R$ 120,00" }).isEnabled(), true);
  });
});

// mutação: `commission_percent || 10` ou tirar o `semComissao`: 0% vira promessa de 10%; tirar a condição do <Saque>: o bloco aparece sem saldo
test("afiliado 0%: 'sem comissão neste programa', nenhuma promessa de percentual, e sem bloco de saque se não há saldo", async () => {
  await comAfiliado(browser(), { corpo: ZERO }, async (t) => {
    await t.page.locator(".afil-quadros").waitFor();
    const tela = await t.page.locator("#main").innerText();
    assert.match(tela, /sem comissão neste programa/);
    assert.doesNotMatch(tela, /\d+\s*% da primeira/);
    assert.doesNotMatch(tela, /Divulgue seu link/, "0% não ganha: o vazio não sugere ganho");
    assert.equal(await t.page.locator("#afil-saque").count(), 0);
  });
  await comAfiliado(browser(), { corpo: { ...ZERO, stats: { ...ZERO.stats, available: 80 } } }, async (t) => {
    await t.page.locator("#afil-saque").waitFor(); // com saldo antigo, o saque aparece
  });
});

// mutação: tirar a frase do Pix ou prometer "por cobrança": o texto volta a prometer o que o backend não paga
test("a regra da comissão: só a primeira cobrança no cartão, e Pix não gera", async () => {
  await comAfiliado(browser(), {}, async (t) => {
    const texto = await t.page.locator("#afil-divulgar + p").innerText();
    assert.match(texto, /10% da primeira cobrança do plano no cartão/);
    assert.match(texto, /Pix não gera comissão/);
  });
});

// mutação: `ROTULO_REEMBOLSO` sem um dos dois, ou o `reembolso &&` fora da linha estornada; tirar `.afil-selos .selo.gain` do CSS: o `.selo` engole o verde e a linha "paga" fica cinza
test("comissões: cada status com o rótulo do pagamento; estornada e reembolsada mostra os dois", async () => {
  await comAfiliado(browser(), {}, async (t) => {
    await t.page.locator(".afil-lista").first().waitFor();
    const linhas = await t.page.locator("#afil-comissoes ~ .afil-lista li").evaluateAll((l) => l.map((e) => [...e.querySelectorAll(".selo")].map((s) => s.textContent)));
    assert.deepEqual(linhas, [["libera 20/10"], ["disponível"], ["paga"], ["estornada", "pagamento reembolsado"], ["disponível", "reembolso parcial"], ["disponível", "pagamento reembolsado"]]);
    const cor = (texto) => t.page.locator(".afil-selos .selo", { hasText: texto }).first().evaluate((e) => getComputedStyle(e).color);
    assert.equal(await cor("paga"), "rgb(61, 220, 151)", "verde só no valor positivo: --gain");
    assert.equal(await cor("estornada"), "rgb(245, 181, 68)", "--warn");
    assert.notEqual(await cor("libera"), "rgb(61, 220, 151)");
  });
});

// mutação: passar `refunded_at`, a forma de pagamento ou o valor devolvido para a tela: o texto aparece. A fixture os traz de propósito para ver a borda cortar.
test("reembolso: nenhuma data, valor devolvido, forma de pagamento nem pessoa na tela, mesmo se a API mandar a mais", async () => {
  const vazado = { ...ATIVA, commissions: ATIVA.commissions.map((c) => ({ ...c, refunded_at: "2026-09-29T12:00:00+00:00", refund_amount: 77.77, payment_method: "pix", referred_email: "indicado@x.com" })) };
  await comAfiliado(browser(), { corpo: vazado }, async (t) => {
    await t.page.locator(".afil-lista").first().waitFor();
    const tela = await t.page.locator("#main").innerText();
    for (const proibido of ["29/09", "77,77", "indicado@x.com", "refunded_at"]) assert.ok(!tela.includes(proibido), proibido);
    assert.doesNotMatch(await t.page.locator("#afil-comissoes ~ .afil-lista").innerText(), /pix|cartão/i);
  });
});

// mutação: `c.payout_id !== null` fora de `situacao`: a comissão em saque volta a parecer disponível
test("comissão em saque mostra 'em saque'; o quadro Em saque traz o valor", async () => {
  await comAfiliado(browser(), { corpo: COM_SAQUE }, async (t) => {
    await t.page.locator(".afil-lista").first().waitFor();
    assert.equal(await t.page.locator("#afil-comissoes ~ .afil-lista .selo").first().innerText(), "em saque");
    assert.equal((await quadros(t.page))["Em saque"], "R$ 120,00");
    assert.equal(await t.page.locator("#afil-saques ~ .afil-lista .selo").first().innerText(), "em análise");
  });
});

// mutação: tirar o quadro de `Area`, o `c.in_review` ou o `c.checking` de `situacao`, ou o `original_amount` de Historico.tsx
for (const [nome, width, height] of [["desktop", 1280, 800], ["celular", 390, 844]]) {
  test(`reembolso em análise (${nome}): quadro, selo, 'ajustada de' e o selo do pagamento reembolsado`, async () => {
    await comAfiliado(browser(), { corpo: EM_REVISAO, width, height }, async (t) => {
      await t.page.locator("#afil-comissoes ~ .afil-lista").waitFor();
      const q = await quadros(t.page);
      assert.equal(q["Em revisão"], "R$ 100,00");
      assert.equal(q["Disponível"], "R$ 70,00");
      assert.match(await t.page.locator(".afil-quadros").innerText(), /reembolso de cliente em análise/);
      const itens = t.page.locator("#afil-comissoes ~ .afil-lista li");
      assert.match(await itens.nth(0).innerText(), /em revisão/);
      assert.doesNotMatch(await itens.nth(0).innerText(), /ajustada de/);
      assert.match(await itens.nth(1).innerText(), /ajustada de R\$ 100,00/);
      assert.match(await itens.nth(1).innerText(), /R\$ 60,00/);
      assert.match(await itens.nth(2).innerText(), /paga[\s\S]*pagamento reembolsado/);
      assert.match(await itens.nth(3).innerText(), /em conferência/);  // vencida, mas a Stripe ainda não confirmou
      assert.doesNotMatch(await itens.nth(3).innerText(), /disponível/);
      assert.equal(await t.page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth), true);
    });
  });
}

// mutação: tirar o `p.note &&` de Historico.tsx: a rejeição fica sem o motivo
test("saque rejeitado mostra o motivo", async () => {
  await comAfiliado(browser(), { corpo: REJEITADO }, async (t) => {
    await t.page.locator("#afil-saques ~ .afil-lista").waitFor();
    assert.match(await t.page.locator("#afil-saques ~ .afil-lista").innerText(), /Motivo da rejeição: Chave Pix não encontrada/);
    assert.equal(await t.page.locator("#afil-saques ~ .afil-lista .selo").innerText(), "rejeitado");
  });
});

// mutação: `Vazio` removido: as listas vazias somem sem dizer nada
test("sem comissão nem saque: estados vazios que dizem o que fazer", async () => {
  await comAfiliado(browser(), { corpo: { ...ABAIXO, commissions: [], payouts: [] } }, async (t) => {
    await t.page.getByText("Nenhuma comissão ainda. Divulgue seu link.").waitFor();
    await t.page.getByText("Nenhum saque solicitado.").waitFor();
  });
});

// mutação: voltar o texto fixo em `falhaLeitura`: a conta agendada para exclusão não sabe por que a tela não abre
test("conta agendada para exclusão: a tela mostra o motivo do 403, não o erro genérico", async () => {
  const DETALHE = "Esta conta está agendada para exclusão em 2026-12-01.";
  const s = servidor();
  s.get = (r) => r.fulfill({ status: 403, json: { detail: DETALHE } });
  await comAfiliado(browser(), { s, espera: "[role=alert]" }, async (t) => {
    assert.match(await t.page.locator("[role=alert]").innerText(), /agendada para exclusão/);
    assert.equal(await t.page.getByText("Não foi possível carregar").count(), 0);
  });
});

// mutação: tirar o `lista.length >=` do Historico.tsx: a nota aparece em lista de 1 linha, que não foi cortada
test("'Mostrando as mais recentes' só no limite da API: 50 comissões e 20 saques", async () => {
  await comAfiliado(browser(), {}, async (t) => {
    await t.page.locator(".afil-lista").first().waitFor();
    assert.equal(await t.page.getByText(/Mostrando (as|os) mais recentes/).count(), 0);
  });
  const c = ATIVA.commissions[0], p = ATIVA.payouts[0];
  const cheio = { ...ATIVA, commissions: Array.from({ length: 50 }, (_, i) => ({ ...c, id: i + 1 })), payouts: Array.from({ length: 20 }, (_, i) => ({ ...p, id: i + 1 })) };
  await comAfiliado(browser(), { corpo: cheio }, async (t) => {
    await t.page.locator(".afil-lista").first().waitFor();
    assert.equal(await t.page.getByText(/Mostrando (as|os) mais recentes/).count(), 2);
  });
});

// mutação: tirar `word-break` de `.afil-lista li`: a nota sem espaço passa do cartão
for (const width of [320, 1280]) {
  test(`${width}: nota de rejeição sem espaço quebra dentro do cartão`, async () => {
    const corpo = { ...ATIVA, payouts: [{ id: 7, amount: 90, status: "rejected", requested_at: "2026-09-10T12:00:00+00:00", paid_at: null, note: "x".repeat(240) }] };
    await comAfiliado(browser(), { corpo, width, height: 800 }, async (t) => {
      const nota = t.page.locator(".afil-motivo");
      await nota.waitFor();
      const m = await nota.evaluate((n) => { const r = document.createRange(); r.selectNodeContents(n); const li = n.closest("li").getBoundingClientRect(); return { fim: r.getBoundingClientRect().right, li: li.right, sw: n.scrollWidth, cw: n.clientWidth }; });
      assert.ok(m.fim <= m.li + 0.5 && m.sw <= m.cw, JSON.stringify(m));
    });
  });
}

// mutação: tirar a conferência de `comissaoDe`: resposta fora do contrato quebra a tela (pageerror) em vez do erro com retentativa
test("resposta fora do contrato ou 500: erro com 'Tentar de novo', que recupera", async () => {
  await comAfiliado(browser(), { corpo: { ...ATIVA, stats: { ...ATIVA.stats, available: "120" } }, espera: "[role=alert]" }, async (t) => {
    t.s.corpo = ATIVA;
    await t.page.getByRole("button", { name: "Tentar de novo" }).click();
    await t.page.locator(".afil-quadros").waitFor();
  });
});
