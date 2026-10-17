// A seção "Conta" do /painel (App.tsx, parts/MenuTelas.tsx, conta/): Perfil (página de verdade, testada em
// dashboard_v2_conta_perfil.test.mjs), duas páginas-ponte (Cobrança, Configurações) e o Sair na própria barra lateral.
//   · o Sair faz POST no /auth/logout com o fetch GLOBAL (o auth-refresh.js limpa o aparelho), com o
//     CSRF, grava `finbot_logout_at` e vai para /?logout=1, inclusive com o fetch REJEITADO;
//   · logout em outra aba derruba esta (evento `storage`);
//   · nome e e-mail só em memória; o protótipo (DEMO) não tem a seção nem as rotas;
//   · o Sair cabe na altura do trilho sem rolar de 1440×900 até 593px de altura; abaixo disso (o trilho tem overflow-y:auto
//     em ≤620px) ele rola e o Sair se alcança com Tab, com o menu da Ajuda fora do corte;
//   · a Conta fica presa ao fim do trilho; nome/e-mail longos não criam rolagem lateral; 2 cliques no Sair = 1 POST.
import { test } from "node:test";
import assert from "node:assert/strict";
import { PAINEL, PROTOTIPO, RAIZ, ORIGIN, servir } from "./_painel.mjs";
import { abrir, navegador } from "./_guia.mjs";

const browser = navegador();

// Roda na página: TODOS os botões do menu da Ajuda (centro e os 4 cantos, 1px para dentro) estão por cima de qualquer coisa (a .askbar do Piggy, fixa).
const menuPorCima = () => [...document.querySelectorAll(".rail .ajuda-menu button")].every((el) => {
  const b = el.getBoundingClientRect();
  return [[.5, .5], [0, 0], [1, 0], [0, 1], [1, 1]].every(([x, y]) => document.elementFromPoint(b.left + 1 + (b.width - 2) * x, b.top + 1 + (b.height - 2) * y)?.closest(".ajuda-menu") != null);
});

const CONTA = { display_name: "Ana Teste", email: "ana.teste@exemplo.com", user_id: 7, whatsapp_linked: false };
// `logout`: "ok" responde 200; "offline" derruba a conexão (o fetch REJEITA); "lento" responde em 3s (o clique e a leitura do `disabled` cabem folgados antes de a página navegar).
// `perfil`: o corpo do /auth/dashboard-profile. `posts`: o que chegou no logout; `perfis`: os caminhos pedidos do perfil.
function conta({ logout = "ok", perfil = CONTA } = {}) {
  const s = { posts: [], perfis: [] };
  const antes = async (ctx) => {
    // o caminho EXATO: "**/auth/dashboard-profile" casaria também /api/v2/auth/dashboard-profile
    await ctx.route((u) => u.pathname === "/auth/dashboard-profile", (r) => { s.perfis.push(new URL(r.request().url()).pathname); return r.fulfill({ json: perfil }); });
    // o Perfil lê a segurança da conta por /settings/{uid}/security (fora da /api/v2)
    await ctx.route((u) => u.pathname === "/settings/7/security", (r) => r.fulfill({ json: { email: CONTA.email, display_name: CONTA.display_name, phone: null, created_at: "2025-10-01T12:00:00+00:00" } }));
    await ctx.route("**/auth/logout", async (r) => {
      s.posts.push({ metodo: r.request().method(), url: new URL(r.request().url()).pathname, csrf: r.request().headers()["x-csrf-token"] });
      if (logout === "lento") await new Promise((ok) => setTimeout(ok, 3000));
      return logout === "offline" ? r.abort("internetdisconnected") : r.fulfill({ json: { ok: true } });
    });
  };
  return { s, antes };
}

for (const logout of ["ok", "offline"]) {
  test(`Sair (${logout === "ok" ? "servidor responde" : "fetch rejeitado, offline"}): POST global em /auth/logout com o CSRF, grava finbot_logout_at e vai para /?logout=1`, async () => {
    const { s, antes } = conta({ logout });
    const { ctx, page, erros } = await abrir({ guia: "concluido", rota: "/conta/cobranca", antes });
    await page.locator(".rail-conta").getByRole("button", { name: "Sair" }).click();
    await page.waitForURL(`${ORIGIN}/?logout=1`);
    const gravado = await page.evaluate(() => localStorage.getItem("finbot_logout_at"));
    await ctx.close();
    assert.deepEqual(s.posts, [{ metodo: "POST", url: "/auth/logout", csrf: "tok-123" }]); // não /api/v2/auth/logout
    assert.match(gravado, /^\d{13}$/);
    assert.deepEqual(erros, []);
  });
}

test("logout em outra aba derruba o painel; outra chave do storage não", async () => {
  const { antes } = conta();
  const { ctx, page } = await abrir({ guia: "concluido", antes });
  const outra = await ctx.newPage();
  await outra.goto(PAINEL);
  await outra.evaluate(() => localStorage.setItem("pigbank_theme", "dark"));
  await page.waitForTimeout(200);
  const ficou = page.url().endsWith("#/");
  await outra.evaluate(() => localStorage.setItem("finbot_logout_at", String(Date.now())));
  await page.waitForURL(`${ORIGIN}/?logout=1`);
  await ctx.close();
  assert.ok(ficou, "outra chave do storage tirou o painel da tela");
});

test("nome e e-mail aparecem na página e ficam só em memória (nada no localStorage nem no sessionStorage)", async () => {
  const { s, antes } = conta();
  const { ctx, page } = await abrir({ guia: "concluido", rota: "/conta/cobranca", antes });
  await page.locator(".conta-ponte", { hasText: CONTA.email }).waitFor();
  const r = await page.evaluate(() => {
    const todos = [localStorage, sessionStorage].flatMap((st) => Object.keys(st).map((k) => k + "=" + st.getItem(k)));
    return { texto: document.querySelector(".conta-ponte").innerText, guardado: todos.join("\n"), link: document.querySelector(".conta-ponte a").getAttribute("href") };
  });
  await ctx.close();
  assert.match(r.texto, /Ana Teste/);
  assert.ok(!/Ana Teste|ana\.teste@exemplo\.com/.test(r.guardado), r.guardado);
  assert.equal(r.link, "/conta");
  assert.deepEqual([...new Set(s.perfis)], ["/auth/dashboard-profile"]); // não /api/v2/auth/dashboard-profile
});

// Janela de 320 e 375: nome/e-mail sem ponto de quebra não criam rolagem lateral; e-mail só de espaços não deixa <span> vazio.
const LONGOS = [
  { display_name: "W".repeat(50), email: "joao.da.silva.pereira.de.souza.neto@empresa-corporativa-grande-demais-do-brasil.com.br" },
  { display_name: "Abcdefghijklmnopqrstuvwxyzabcdefghijklmnopqrstu", email: "joao.da.silva.pereira.de.souza.neto@empresa-corporativa-grande.com.br" },
];
for (const width of [320, 375]) {
  test(`${width}: nome e e-mail longos quebram dentro do painel, sem rolagem lateral da página`, async () => {
    const lateral = [];
    for (const perfil of LONGOS) {
      const { antes } = conta({ perfil });
      const { ctx, page } = await abrir({ guia: "concluido", width, height: 800, rota: "/conta/cobranca", antes });
      await page.locator(".conta-ponte span").waitFor();
      lateral.push(await page.evaluate(() => document.documentElement.scrollWidth - innerWidth));
      await ctx.close();
    }
    assert.deepEqual(lateral, [0, 0]);
  });
}

test("nome com espaços nas pontas é aparado e e-mail só de espaços não renderiza <span> vazio", async () => {
  const { antes } = conta({ perfil: { display_name: "  Ana  ", email: "   " } });
  const { ctx, page } = await abrir({ guia: "concluido", rota: "/conta/cobranca", antes });
  await page.locator(".conta-ponte b").waitFor();
  const r = await page.evaluate(() => ({ nome: document.querySelector(".conta-ponte b").textContent, spans: document.querySelectorAll(".conta-ponte span").length }));
  await ctx.close();
  assert.deepEqual(r, { nome: "Ana", spans: 0 });
});

test("Sair: dois cliques seguidos mandam um POST só e o botão fica desabilitado", async () => {
  const { s, antes } = conta({ logout: "lento" });
  const { ctx, page } = await abrir({ guia: "concluido", rota: "/conta/cobranca", antes });
  const sair = page.locator(".rail-conta").getByRole("button", { name: "Sair" });
  await sair.dblclick();
  const desabilitado = await sair.isDisabled();
  await page.waitForURL(`${ORIGIN}/?logout=1`);
  await ctx.close();
  assert.equal(s.posts.length, 1);
  assert.ok(desabilitado);
});

test("as duas páginas-ponte: título, aria-current no trilho e o link para a tela antiga (o Perfil tem teste próprio)", async () => {
  const { antes } = conta();
  const { ctx, page } = await abrir({ guia: "concluido", antes });
  const vistas = [];
  const botoes = [];
  for (const [rota, titulo, destino] of [["/conta/cobranca", "Cobrança", "/conta"], ["/conta/configuracoes", "Configurações", "/settings"]]) {
    await page.evaluate((h) => { location.hash = h; }, `#${rota}`);
    await page.locator("#page-title", { hasText: titulo }).waitFor();
    vistas.push([await page.locator("#page-title").innerText(), await page.locator('.rail-conta [aria-current="page"]').innerText(), await page.locator(".conta-ponte a").getAttribute("href"), await page.title(), await page.locator(".topbar .month-switch").count()]);
    assert.equal(destino, vistas.at(-1)[2]);
    botoes.push([await page.locator(".conta-ponte a").innerText(), await page.locator(".page-lede").count()]); // dono 2026-10-17: sem subtítulo
  }
  await ctx.close();
  assert.deepEqual(vistas.map((v) => [v[0], v[1], v[3], v[4]]), [["Cobrança", "Cobrança", "PigBank · Cobrança", 0], ["Configurações", "Configurações", "PigBank · Configurações", 0]]);
  assert.deepEqual(botoes, [["Abrir cobrança", 0], ["Abrir configurações", 0]]);
});

test("⌘K lista as rotas da Conta", async () => {
  const { antes } = conta();
  const { ctx, page } = await abrir({ guia: "concluido", antes });
  await page.evaluate(() => window.dispatchEvent(new Event("dash:command")));
  await page.locator(".cmdk-item").first().waitFor();
  const itens = await page.locator(".cmdk-item").allTextContents();
  await ctx.close();
  for (const rotulo of ["Perfil", "Cobrança", "Configurações"]) assert.ok(itens.some((t) => t.includes(rotulo)), rotulo);
});

// O trilho é sticky, com a altura da janela e sem rolagem: o Sair é o último e tem de estar à vista.
// 1366×657 é a janela útil de um 1366×768 (barras do navegador). 1280×600 já cai no @media (max-height:620px) (overflow-y:auto, menu da
// Ajuda fixo), mas o trilho cheio ainda cabe: 593px é a menor altura sem rolar (592 dá railRola=1); um 1280×720 útil (~590px) rola.
for (const [width, height] of [[1440, 900], [1366, 657], [1280, 600], [1024, 768], [1181, 657], [1180, 657], [761, 640]]) {
  test(`trilho ${width}×${height}: o Sair cabe sem rolar o trilho, nada se sobrepõe e o popover da Ajuda não é cortado`, async () => {
    const { antes } = conta();
    const { ctx, page } = await abrir({ guia: "concluido", width, height, rota: "/assinaturas", antes });
    await page.locator(".rail-conta").waitFor();
    await page.locator(".rail .ajuda > button").click();
    const r = await page.evaluate(() => {
      const rect = (el) => el.getBoundingClientRect();
      const rail = document.querySelector(".rail"), sair = [...document.querySelectorAll(".rail-conta button")].at(-1);
      const filhos = [...rail.children].filter((e) => e.getClientRects().length).map(rect);
      const pop = rect(document.querySelector(".rail .ajuda-menu"));
      return {
        sairFundo: Math.round(rect(sair).bottom), altura: innerHeight,
        railRola: rail.scrollHeight - rail.clientHeight,
        sobrepoe: filhos.some((f, i) => i && f.top < filhos[i - 1].bottom - 0.5),
        popover: pop.top >= 0 && pop.bottom <= innerHeight && pop.right <= innerWidth,
        sairNaTela: document.elementFromPoint(rect(sair).left + 4, rect(sair).top + rect(sair).height / 2)?.closest(".rail-conta button") === sair,
        lateral: document.documentElement.scrollWidth - innerWidth,
      };
    });
    r.popoverPorCima = await page.evaluate(menuPorCima);
    await ctx.close();
    assert.ok(r.sairFundo <= r.altura, `Sair termina em ${r.sairFundo} e a janela tem ${r.altura}`);
    assert.deepEqual({ ...r, sairFundo: 0, altura: 0 }, { sairFundo: 0, altura: 0, railRola: 0, sobrepoe: false, popover: true, popoverPorCima: true, sairNaTela: true, lateral: 0 });
  });
}

// A Conta fica embaixo: no fim do trilho, colada ao rodapé (RailSync) quando ele existe (>1180px), e abaixo da lista.
for (const [width, height] of [[1440, 900], [1024, 768]]) {
  test(`trilho ${width}×${height}: a Conta fica presa ao fim do trilho, abaixo da lista`, async () => {
    const { antes } = conta();
    const { ctx, page } = await abrir({ guia: "concluido", width, height, rota: "/assinaturas", antes });
    await page.locator(".rail-conta").waitFor();
    const r = await page.evaluate(() => {
      const rect = (q) => document.querySelector(q).getBoundingClientRect();
      const foot = document.querySelector(".rail-foot"), temFoot = getComputedStyle(foot).display !== "none";
      return { acima: rect(".rail-conta").top - rect(".rail-list").bottom, abaixo: (temFoot ? rect(".rail-foot").top : rect(".rail").bottom) - rect(".rail-conta").bottom };
    });
    await ctx.close();
    assert.ok(r.acima > 40, `a Conta devia estar longe da lista (${r.acima}px)`);
    assert.ok(r.abaixo >= 0 && r.abaixo <= 30, `a Conta devia colar no fim do trilho (${r.abaixo}px de sobra)`);
  });
}

// Janela mais baixa que o trilho (zoom 150% num 1366×657 vira ~911×438): o trilho rola. Sair se alcança com Tab, numa página mais
// alta que a janela (/lancamentos: o trilho sticky não anda com a página), e o menu da Ajuda (/assinaturas) abre inteiro, ao lado do trilho.
for (const [width, height] of [[1440, 500], [1440, 440], [911, 438], [1024, 420], [1280, 600], [1181, 438], [1180, 438], [761, 420], [769, 500], [1000, 500]]) {
  test(`janela baixa ${width}×${height}: o Sair se alcança com Tab e o menu da Ajuda não é cortado`, async () => {
    const { antes } = conta();
    const { ctx, page } = await abrir({ guia: "concluido", width, height, rota: "/lancamentos", antes });
    await page.locator(".rail-conta").waitFor();
    await page.locator(".rail-conta a").last().focus();
    await page.keyboard.press("Tab");
    const r = await page.evaluate(() => {
      const sair = document.activeElement, s = sair.getBoundingClientRect();
      return {
        ehSair: sair.textContent.trim() === "Sair",
        paginaAlta: document.documentElement.scrollHeight > innerHeight,
        noAlto: s.top >= 0 && s.bottom <= innerHeight,
        porCima: document.elementFromPoint(s.left + s.width / 2, s.top + s.height / 2)?.closest("button") === sair,
      };
    });
    await page.evaluate(() => { location.hash = "#/assinaturas"; });
    await page.locator(".rail .ajuda > button").click();
    const m = await page.evaluate(() => {
      const rect = (e) => e.getBoundingClientRect();
      const pop = rect(document.querySelector(".rail .ajuda-menu")), rail = rect(document.querySelector(".rail"));
      return {
        dentro: pop.top >= 0 && pop.bottom <= innerHeight && pop.right <= innerWidth,
        direita: pop.left >= rail.right,
        lateral: document.documentElement.scrollWidth - innerWidth,
      };
    });
    m.porCima = await page.evaluate(menuPorCima);
    await ctx.close();
    assert.deepEqual(r, { ehSair: true, paginaAlta: true, noAlto: true, porCima: true });
    assert.deepEqual(m, { dentro: true, direita: true, porCima: true, lateral: 0 });
  });
}

// A barra do Piggy (.askbar, fixa, z-index 40) ficava POR CIMA do menu da Ajuda em janela baixa: o clique no centro do 2º item era morto.
test("janela baixa 769×500: clicar no centro de 'Como funciona esta tela' abre a dica (a barra do Piggy não cobre o menu)", async () => {
  const { antes } = conta();
  const { ctx, page } = await abrir({ guia: "concluido", width: 769, height: 500, rota: "/assinaturas", antes });
  await page.locator(".rail .ajuda > button").click();
  const b = await page.locator(".rail .ajuda-menu button").nth(1).boundingBox();
  await page.mouse.click(b.x + b.width / 2, b.y + b.height / 2);
  const aberta = await page.locator(".dica-tela").waitFor({ timeout: 3000 }).then(() => true, () => false);
  await ctx.close();
  assert.ok(aberta, "a dica não abriu: o clique caiu fora do item");
});

// display_name/email que não são texto (123, {}) não derrubam o painel: a página abre sem nome nem e-mail.
test("perfil com display_name número e email objeto: a página da Conta abre, sem nome nem e-mail e sem erro", async () => {
  const { antes } = conta({ perfil: { display_name: 123, email: {} } });
  const { ctx, page, erros } = await abrir({ guia: "concluido", rota: "/conta/cobranca", antes });
  await page.locator("#page-title", { hasText: "Cobrança" }).waitFor();
  await page.waitForTimeout(300);
  const r = await page.evaluate(() => ({ painel: document.querySelector(".conta-ponte a") != null, nome: document.querySelectorAll(".conta-ponte b").length, spans: document.querySelectorAll(".conta-ponte span").length }));
  await ctx.close();
  assert.deepEqual(r, { painel: true, nome: 0, spans: 0 });
  assert.deepEqual(erros, []);
});

test("celular 390×844: a folha do Menu tem o grupo Conta (Perfil, Cobrança, Configurações, Sair) e o Sair se alcança rolando", async () => {
  const { s, antes } = conta({ logout: "lento" });
  const { ctx, page } = await abrir({ guia: "concluido", width: 390, height: 844, antes });
  await page.locator(".tabbar").getByRole("button", { name: "Menu" }).click();
  const r = await page.evaluate(() => {
    const grupo = document.querySelector('.menu-telas [role="group"].menu-telas-conta');
    const folha = document.querySelector(".menu-telas");
    folha.scrollTop = folha.scrollHeight;
    const sair = grupo.querySelector("button").getBoundingClientRect(), fechar = document.querySelector(".menu-telas-fechar").getBoundingClientRect();
    return { titulo: grupo.querySelector(".menu-telas-sec").textContent, itens: [...grupo.querySelectorAll("li")].map((li) => li.textContent.trim()), rail: getComputedStyle(document.querySelector(".rail")).display, alcanca: sair.top >= 0 && sair.bottom <= fechar.top + 1 };
  });
  await page.locator(".menu-telas-conta").getByRole("button", { name: "Sair" }).dblclick(); // dois cliques seguidos: um POST só
  await page.waitForURL(`${ORIGIN}/?logout=1`);
  await ctx.close();
  assert.deepEqual(r, { titulo: "Conta", itens: ["Perfil", "Cobrança", "Configurações", "Sair"], rail: "none", alcanca: true });
  assert.equal(s.posts.length, 1);
});

test("protótipo (DEMO, mesmo bundle): sem seção Conta, sem Sair, rota da Conta cai no Resumo e fora do ⌘K", async () => {
  const ctx = await browser().newContext({ viewport: { width: 1440, height: 900 }, reducedMotion: "reduce" });
  await ctx.addInitScript(() => localStorage.setItem("pigbank.dashboard.profile.v1", '"padrao"')); // sem o seletor de perfil por cima
  await servir(ctx, RAIZ);
  const page = await ctx.newPage();
  await page.goto(`${PROTOTIPO}#/conta/perfil`);
  await page.locator("#page-title").waitFor();
  const r = { hash: new URL(page.url()).hash, titulo: await page.locator("#page-title").innerText(), conta: await page.locator(".rail-conta").count() };
  await page.evaluate(() => window.dispatchEvent(new Event("dash:command")));
  await page.locator(".cmdk-item").first().waitFor();
  const itens = (await page.locator(".cmdk-item").allTextContents()).join("|");
  await page.keyboard.press("Escape");
  await page.setViewportSize({ width: 390, height: 844 });
  await page.locator(".tabbar").getByRole("button", { name: "Menu" }).click();
  r.folha = await page.locator(".menu-telas-conta").count();
  // o aviso de logout de outra aba não derruba o protótipo (que não tem conta)
  await page.evaluate(() => window.dispatchEvent(new StorageEvent("storage", { key: "finbot_logout_at" })));
  await page.waitForTimeout(300);
  r.ficou = new URL(page.url()).pathname === new URL(PROTOTIPO).pathname;
  await ctx.close();
  assert.deepEqual(r, { hash: "#/", titulo: r.titulo, conta: 0, folha: 0, ficou: true });
  assert.match(r.titulo, /^Resumo/);
  assert.ok(!/Perfil|Cobrança|Configurações|Sair/.test(itens), itens);
});
