/**
 * Sessão de leitor (credencial de visualização, PR 2b) no /painel: faixa fixa, nenhuma escrita à vista, telas fechadas por URL direta e
 * NENHUMA requisição não-GET durante o carregamento (o Guia e a Dica postam sozinhos para o dono).
 *
 * O leitor é o `/api/v2/me` com `somente_leitura: true` (como o servidor o devolve) e os GET que o servidor nega a ele (`NEGADO_LEITOR`:
 * perfil, conta, família) respondendo 403 `somente_leitura`. Quem pede um deles sem precisar derruba o teste (`negados`).
 * Controle POSITIVO em cada grupo: o dono, no mesmo cenário, vê as escritas, as telas e os POST.
 * NEGATIVOS (medidos): `useLeitor` sempre falso → o grupo do leitor inteiro fica vermelho e o do dono segue verde; tirar o `barrada` do App.tsx →
 * só o das URLs; tirar o `!leitor` do Guia e da Dica → só o dos POST de boot.
 *
 * Rodar: npm run test:frontend (abre o artefato commitado: mudou webapp/src, rode `npm --prefix webapp run build`).
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { abrir, navegador } from "./_guia.mjs";
import { ehNegado, rotasDoLeitor } from "./_leitor.mjs";

navegador();

// O que o leitor muda no servidor (frontend/routes/leitura_guard.py): os GET de `ehNegado` dão 403 e a tela nunca deveria pedi-los.

// As rotas a mais, antes de a página abrir. `leitor: false` é o dono: só o registro das requisições.
const cenario = (leitor, { naoGet, negados }) => async (ctx) => {
  ctx.on("request", (r) => {
    const u = new URL(r.url());
    if (u.origin !== "http://127.0.0.1:1") return;
    if (r.method() !== "GET") naoGet.push(`${r.method()} ${u.pathname}`);
    if (leitor && ehNegado(u)) negados.push(`${r.method()} ${u.pathname}`);
  });
  if (leitor) await rotasDoLeitor(ctx);
};

async function abrirComo(leitor, opcoes) {
  const reg = { naoGet: [], negados: [] };
  const a = await abrir({ guia: "oferecer", dica: "nova", ...opcoes, antes: cenario(leitor, reg) });
  return { ...a, ...reg };
}

const ir = async (page, rota) => {
  await page.evaluate((h) => { location.hash = h; }, `#${rota}`);
  await page.waitForFunction((h) => location.hash === h, `#${rota}`);
  await page.locator("#page-title").waitFor();
  await page.waitForTimeout(250);
};
// Tudo o que se toca para escrever, conversar ou sair do painel (o Sair é do leitor e fica fora).
const ESCRITA = /^(\+\s*)?(Lançar na Carteira|Nova meta|Criar minha primeira meta|Montar reserva|Editar.*|Novo orçamento|Criar orçamento|Gasto fixo|Ignorar.*|É assinatura.*|Não é assinatura.*|Voltar a mostrar.*|Desfazer.*|Simul.*|Perguntar.*|Enviar|Atualizar agora|Reconectar.*|Mostrar o guia.*|.*Piggy.*)$/i;
const botoes = (page) => page.evaluate(() => [...document.querySelectorAll("main button, main a.btn, .rail a, .tabbar a, .tabbar button, .topbar a, .topbar button, .askbar button")]
  .filter((e) => e.offsetParent !== null).map((e) => (e.getAttribute("aria-label") || e.textContent).trim()).filter(Boolean));
const TELAS = ["/", "/previsao", "/gastos", "/assinaturas", "/metas", "/patrimonio", "/lancamentos", "/orcamento", "/orcamentos-categoria", "/milhao", "/ferramentas", "/ajuda"];

for (const [width, height] of [[1440, 700], [390, 844]]) {
  test(`${width}: leitor: faixa fixa ao rolar, nenhuma ação que escreve em ${TELAS.length} telas e nenhum POST/PUT/DELETE no boot nem na navegação`, async () => {
    const { ctx, page, naoGet, negados, erros } = await abrirComo(true, { width, height, rota: "/" });
    const faixa = page.locator(".faixa-leitura");
    await faixa.waitFor();
    assert.equal((await faixa.textContent()).trim(), "Modo visualização · só leitura");
    // Fixa: rola a página e a faixa segue no topo, com a barra do topo logo abaixo dela.
    const antes = await page.evaluate(() => document.documentElement.scrollHeight - innerHeight);
    assert.ok(antes > 300, `a página precisa rolar para o teste medir (sobra ${antes}px)`);
    await page.evaluate(() => scrollTo(0, 300));
    await page.waitForFunction(() => scrollY > 250);
    const m = await page.evaluate(() => {
      const f = document.querySelector(".faixa-leitura").getBoundingClientRect(), t = document.querySelector(".topbar").getBoundingClientRect();
      return { fTop: f.top, fAltura: f.height, tTop: t.top, ow: document.documentElement.scrollWidth <= document.documentElement.clientWidth };
    });
    assert.equal(m.fTop, 0);
    assert.equal(m.tTop, m.fAltura); // a barra do topo gruda embaixo da faixa, não por baixo dela
    assert.equal(m.ow, true);
    await page.evaluate(() => scrollTo(0, 0));

    const vistos = [];
    for (const rota of TELAS) {
      await ir(page, rota);
      vistos.push(...(await botoes(page)).map((t) => `${rota} → ${t}`));
    }
    const proibidos = vistos.filter((t) => ESCRITA.test(t.split(" → ")[1]));
    assert.deepEqual(proibidos, []);
    // A navegação inteira (trilho, abas, Menu) sem as telas fechadas.
    const hrefs = await page.evaluate(() => [...document.querySelectorAll(".rail a, .tabbar a, .menu-telas a")].map((a) => a.getAttribute("href")));
    assert.deepEqual(hrefs.filter((h) => /#\/(piggy|conta|casa|afiliado|simulador|agentes)/.test(h ?? "")), []);
    assert.equal(await page.locator(".askbar, .piggy-band, #board-profile").count(), 0);
    // Nenhuma escrita, nenhum GET que o servidor nega a ele, nenhum erro de página.
    assert.deepEqual(naoGet, []);
    assert.deepEqual(negados, []);
    assert.deepEqual(erros, []);
    await ctx.close();
  });

  test(`${width}: controle positivo: o dono, nas mesmas telas, vê as ações, o Piggy e a navegação inteira, e não vê a faixa`, async () => {
    const { ctx, page, s } = await abrirComo(false, { width, height, rota: "/" });
    await page.locator("#board-profile").waitFor();
    assert.equal(await page.locator(".faixa-leitura").count(), 0);
    assert.ok(await page.locator(".askbar, .piggy-band").count() > 0);
    const achados = new Set();
    for (const rota of TELAS) {
      await ir(page, rota);
      for (const t of await botoes(page)) if (ESCRITA.test(t)) achados.add(rota);
    }
    for (const rota of ["/lancamentos", "/metas", "/orcamento", "/orcamentos-categoria", "/assinaturas", "/ferramentas"]) assert.ok(achados.has(rota), `o dono não vê nenhuma ação em ${rota}`);
    assert.ok(await page.locator(width > 500 ? '.rail a[href="#/conta/perfil"]' : '.tabbar a[data-tab="piggy"]').count() > 0, "o dono perdeu a Conta ou a aba do Piggy");
    // E o guia posta sozinho para o dono (a dica de Assinaturas, o convite do Resumo): é o POST que o leitor não pode mandar.
    assert.ok(s.dicas.length + s.posts.length > 0, "o controle precisa postar para o leitor ter o que não fazer");
    await ctx.close();
  });
}

const FECHADAS = ["/conta/perfil", "/conta/cobranca", "/conta/configuracoes", "/conta/bancos", "/conta/familia", "/casa", "/afiliado", "/piggy", "/simulador", "/simulador/compra", "/agentes"];

test("leitor: URL direta (carga e troca de hash) de tela fechada cai no Resumo sem entrada no histórico e sem pedir o que o servidor nega", async () => {
  const { ctx, page, naoGet, negados, erros } = await abrirComo(true, { width: 1440, height: 900, rota: FECHADAS[0] });
  for (const rota of FECHADAS) {
    // pela barra de endereço: carga nova da página
    await page.goto(`http://127.0.0.1:1/painel.html#${rota}`);
    await page.waitForFunction(() => location.hash === "#/");
    await page.locator("#page-title").waitFor();
    assert.match(await page.locator("#page-title").textContent(), /^Resumo/, rota);
    // por link colado com a página aberta: a mesma coisa, e o histórico não ganha entrada
    const h = await page.evaluate(() => history.length);
    await page.evaluate((x) => { location.hash = x; }, `#${rota}`);
    await page.waitForFunction(() => location.hash === "#/");
    assert.equal(await page.evaluate(() => history.length), h + 1, `${rota}: só a entrada do hash colado, a volta ao Resumo troca`); // a colagem entra; o redirecionamento não soma outra
    assert.match(await page.locator("#page-title").textContent(), /^Resumo/, rota);
  }
  assert.deepEqual(negados, []);
  assert.deepEqual(naoGet, []);
  assert.deepEqual(erros, []);
  await ctx.close();
});

test("controle positivo: o dono abre as mesmas URLs e fica nelas", async () => {
  const { ctx, page } = await abrirComo(false, { width: 1440, height: 900, rota: "/" });
  for (const rota of ["/conta/perfil", "/piggy", "/simulador", "/casa"]) {
    await ir(page, rota);
    assert.equal(await page.evaluate(() => location.hash), `#${rota}`);
  }
  await ctx.close();
});

test("leitor: Guia e Dica não montam, então o convite e a dica novos não postam nada; o dono posta os dois", async () => {
  const leitor = await abrirComo(true, { width: 1440, height: 900, rota: "/assinaturas" });
  await leitor.page.waitForTimeout(1200);
  assert.equal(await leitor.page.locator(".dica-tela, .guia-balao, .guia-piggy").count(), 0);
  await ir(leitor.page, "/");
  await leitor.page.waitForTimeout(1200);
  assert.deepEqual([leitor.s.dicas, leitor.s.posts, leitor.naoGet], [[], [], []]);
  await leitor.ctx.close();

  const dono = await abrirComo(false, { width: 1440, height: 900, rota: "/assinaturas" });
  await dono.page.locator(".dica-tela").waitFor();
  await ir(dono.page, "/");
  await dono.page.waitForTimeout(1200);
  assert.deepEqual(dono.s.dicas, ["assinaturas.marcas"]);
  assert.ok(dono.naoGet.some((x) => x === "POST /api/v2/guia/dica"));
  await dono.ctx.close();
});
