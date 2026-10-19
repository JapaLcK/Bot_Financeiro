// Reconectar um banco: os erros e as bordas (conta/Bancos.tsx `terminou`, conta/reconectar.ts). O caminho feliz está em dashboard_v2_bancos_reconectar.test.mjs.
// O legado é o REAL; o SDK é falso (_bancos_reconectar.mjs). Cada teste espera a tela assentar (`telaRecon` já espera) antes de injetar erro.
//
// Controles negativos (desligue, rebuild `npm --prefix webapp run build`, o caso tem de ficar vermelho; restaure e rebuild):
//  - E1 (404): em reconectar.ts `textoDaFalha`, trocar `semId: f.status === 404` por `semId: false` (o alerta some com o card); positivo: o E3 (o genérico leva o id).
//  - E2 (403): em `textoDaFalha`, trocar o texto fixo do 403 por `f.msg`. E3/E8/E9: em `textoDaFalha`, `DO_SERVIDOR.includes(f.status)` por `true` (o "boom" do 502 vai à tela),
//    ou `!/[<{[]/.test(servidor)` por `!servidor.includes("<")` (o JSON cru do 402 vai à tela). Positivo do grupo: o 429 em JSON curto (E8).
//  - E4 (prazo): em reconectar.ts apagar a linha `prazo = setTimeout(...)` (a tela fica presa).
//  - E5 (erro com o widget aberto): em `iniciar`, `onError`, trocar `aberta ?` por `false ?` (termina sem `semFoco`: o foco vai a uma linha atrás do overlay).
//  - E6 (sair da rota antes do widget): positivo do E6c (nada abre e nada trava).
//  - E7 (conta trocada): em Bancos.tsx `terminou`, tirar `!trocada &&` do `setAviso`; ou trocar `if (status === 403 || trocada) conta.refetch();` por `if (status === 403) conta.refetch();` (o perfil não é relido). E7b: tirar `setRecon(false);` do cleanup do efeito do seletor (a conta nova nasce com o Reconectar travado) ou `setAviso("")` dele (o "Reconectando…" da conta velha fica no status da nova).
//  - E6c (prazo velho): em Bancos.tsx, no cleanup do efeito do seletor, apagar `emCurso.current?.cancelar();` (o prazo da montagem que saiu destrói o módulo da montagem nova).
//  - E10 (ocupado): em Bancos.tsx `reconectarBanco`, apagar `if (seletor.reconnect(...) === false) s.ocupado();` (a tela fica presa em "Reconectando…" até o prazo).
//  - E5c (foco): como o R2 (foco) de dashboard_v2_bancos_reconectar.test.mjs: o `onClose` tardio devolve o foco à linha.
//  - E12 (seletor depois da reconexão): em Bancos.tsx, no efeito do seletor, trocar `emCurso.current && !emCurso.current.acabou() ? emCurso.current : null` por `emCurso.current` (o `onConnected` do seletor é engolido pela reconexão que já terminou).
//  - E13 (montada): em Bancos.tsx `focar`, apagar `if (!montada.current) return;`.
//  - E14: em reconectar.ts, trocar `semId: true` do "ocupado" por `semId: false`.
//  - E15 (releitura lenta + troca de conta): em Bancos.tsx `terminou`, trocar `if (!contaMudou() && montada.current) {` por `if (true) {` (o "<banco> reconectado." da conta 7 vaza para o status da 8).
//  - E18 (toast do seletor): em Bancos.tsx, no `onError` do seletor, trocar `(status === undefined ? msg : textoDoServidor({ status, msg }))` por `msg` (o corpo cru do 502 vai ao toast). Positivo: o 429 em JSON curto continua no toast.
//  - E16 (toast do seletor): tirar `setErroOF("");` do cleanup do efeito do seletor. E17: tirar `pendente.current = null;` de `reconectarBanco` (um `onClose` tardio da 1ª sessão puxa o foco para a linha depois que a 2ª já terminou).
//  - E19 (widget velho): em frontend/open-finance-connect.js, tirar `|| velha()` do `onClose` de openWidget (o `onClose` tardio do widget A termina a reconexão B).
//  - E11 (onClose antes do onSuccess): em `iniciar`, `onClose`, nada a desligar: o positivo é o módulo real (o save acontece e o `onConnected` tardio relê).
import { test } from "node:test";
import assert from "node:assert/strict";
import { navegador } from "./_guia.mjs";
import { ate, portao } from "./_conta_perfil.mjs";
import { JSON_ERRO, SESSAO } from "./_conta_seguranca.mjs";
import { con, LIMITE, PADRAO } from "./_bancos.mjs";
import { alerta, aviso, card, det, linha, esperaCards, refoco, tiraDoSnapshot } from "./_bancos_desconectar.mjs";
import { naLinha, telaRecon, botaoRecon, reconectar, widgetAberto, widgetsAbertos, sucesso, fechaWidget, erroDoSdk, tokens, saves, depoisDo } from "./_bancos_reconectar.mjs";

const browser = navegador();
void browser;

const GENERICO = "Não deu para abrir a conexão com Nubank. Tente de novo.";
const DO_WIDGET = "Não deu para concluir a reconexão de Nubank. Tente de novo.";
const OCUPADO = "Já tem uma conexão com banco aberta. Conclua ou feche e tente de novo.";
const alertaCom = async (page, texto) => { await alerta(page).filter({ hasText: texto }).waitFor(); return alerta(page).innerText(); };
const noTitulo = (page) => page.waitForFunction(() => document.activeElement?.id === "page-title", null, { timeout: 8000 });
const foraDoFoco = (page) => page.evaluate(() => document.activeElement === document.body || !!document.activeElement?.closest("#pluggy-falso"));
// Com um widget aberto o overlay cobre as linhas (como o da Pluggy): o teclado/leitor de tela ainda chega a elas, o ponteiro não.
const reconectarPorBaixo = async (page, nome) => { await linha(page, nome).evaluate((el) => el.click()); await det(page).waitFor(); await botaoRecon(page).click(); };
const perfis = (srv) => srv.reqs.filter((p) => p === "/auth/dashboard-profile").length;
const sai = async (page, hash) => { await page.evaluate((h) => { location.hash = h; }, hash); await page.waitForFunction((h) => location.hash === h, hash); };
const prazoCurto = (page, ms) => page.evaluate((m) => { const st = window.setTimeout; window.__ms = []; window.setTimeout = (f, d, ...a) => { if (d === 30000) { window.__ms.push(d); d = m; } return st(f, d, ...a); }; }, ms);

test("E1 404 OF_ITEM_NAO_ENCONTRADO: o texto do servidor, nenhum widget; o card some na releitura e o alerta fica (sem id), com o foco no título", async () => {
  const { ctx, page } = await telaRecon({ tokenRes: async (r, s) => { tiraDoSnapshot(s, 11); return JSON_ERRO(404, "Não achamos esse banco nas suas conexões.")(r); } });
  await reconectar(page, "Nubank");
  const texto = await alertaCom(page, "Não achamos");
  await esperaCards(page, 2);
  await noTitulo(page);
  const r = { alertas: await alerta(page).count(), widgets: await widgetsAbertos(page) };
  await ctx.close();
  assert.equal(texto, "Não achamos esse banco nas suas conexões.");
  assert.deepEqual(r, { alertas: 1, widgets: 0 });
});

test("E2 401: 'Sua sessão terminou.' com Recarregar e nenhum card", async () => {
  const { ctx, page } = await telaRecon({ tokenRes: JSON_ERRO(401, "Não autenticado.", SESSAO) });
  await reconectar(page, "Nubank");
  await page.getByText("Sua sessão terminou.").waitFor();
  const r = { cards: await page.locator(".banco-card").count(), recarregar: await page.getByRole("button", { name: "Recarregar" }).count() };
  await ctx.close();
  assert.deepEqual(r, { cards: 0, recarregar: 1 });
});

test("E2 403: texto fixo (nunca o do servidor), o foco no título e o perfil relido", async () => {
  const { ctx, page, srv } = await telaRecon({ tokenRes: JSON_ERRO(403, "CSRF token ausente.") });
  const antes = perfis(srv);
  await reconectar(page, "Nubank");
  const texto = await alertaCom(page, "Recarregue a página");
  await noTitulo(page);
  await ate(page, () => perfis(srv) > antes, "o perfil relido");
  await ctx.close();
  assert.equal(texto, "Não deu para reconectar. Recarregue a página e tente de novo.");
});

for (const [nome, tokenRes] of [["502 com o str() do erro da Pluggy", JSON_ERRO(502, "PluggyApiError: boom")], ["500", JSON_ERRO(500, "boom interno")], ["rede", (r) => r.abort()]]) {
  test(`E3 ${nome}: o texto genérico COM o banco, nunca o cru do servidor; o foco volta à linha`, async () => {
    const { ctx, page, srv } = await telaRecon({ tokenRes });
    await reconectar(page, "Nubank");
    const texto = await alertaCom(page, "Não deu para abrir");
    await page.waitForFunction(() => document.activeElement?.classList.contains("banco-linha"), null, { timeout: 8000 });
    const r = { naLinha: await naLinha(page, "Nubank"), corpo: await page.locator("body").innerText(), tokens: tokens(srv).length };
    await ctx.close();
    assert.equal(texto, GENERICO);
    assert.ok(!/boom/.test(r.corpo));
    assert.deepEqual([r.naLinha, r.tokens], [true, 1]);
  });
}

test("E4 o connect-token nunca responde: passado o prazo (30 s), o genérico, o foco na linha, destravado; a resposta tardia não abre widget", async () => {
  const [segura, solta] = portao();
  const { ctx, page } = await telaRecon({ tokenRes: async (r) => { await segura; await r.fulfill({ json: { accessToken: "acesso-xyz" } }).catch(() => {}); } });
  await prazoCurto(page, 400);
  await reconectar(page, "Nubank");
  const texto = await alertaCom(page, "Não deu para abrir");
  solta();
  await page.waitForTimeout(500);
  await linha(page, "Nubank").click();
  const r = { widgets: await widgetsAbertos(page), livre: await botaoRecon(page).isEnabled(), prazo: await page.evaluate(() => window.__ms) };
  await ctx.close();
  assert.equal(texto, GENERICO);
  assert.deepEqual(r, { widgets: 0, livre: true, prazo: [30000] });
});

test("E5 erro do SDK com o widget aberto e NUNCA o onClose: o alerta vem na hora, o foco NÃO vai à linha atrás do overlay, destrava; o sucesso tardio ainda salva e o onClose tardio não muda nada", async () => {
  const { ctx, page, srv } = await telaRecon();
  await reconectar(page, "Nubank");
  await widgetAberto(page);
  await erroDoSdk(page);
  const texto = await alertaCom(page, "Não deu para concluir");
  await page.waitForTimeout(300);
  const meio = { foraDoFoco: await foraDoFoco(page), naLinha: await naLinha(page, "Nubank") };
  await sucesso(page); // a pessoa tenta de novo no mesmo widget e dá certo: o save não se perde
  await ate(page, () => saves(srv).length === 1, "o save tardio");
  await fechaWidget(page);
  await page.waitForTimeout(300);
  const fim = { alertas: await alerta(page).allInnerTexts(), nenhumTravado: await page.evaluate(() => !document.querySelector("dialog[open]")) };
  await ctx.close();
  assert.equal(texto, DO_WIDGET);
  assert.deepEqual(meio, { foraDoFoco: true, naLinha: false });
  assert.deepEqual(fim, { alertas: [DO_WIDGET], nenhumTravado: true });
});

test("E5 o /pluggy-item dá 500 e o widget fecha: o genérico do widget (nunca o texto cru) e a lista relida", async () => {
  const { ctx, page, srv } = await telaRecon({ itemRes: JSON_ERRO(500, "boom interno") });
  await reconectar(page, "Nubank");
  await widgetAberto(page);
  await sucesso(page);
  await fechaWidget(page);
  const texto = await alertaCom(page, "Não deu para concluir");
  await ate(page, () => depoisDo(srv, "/open-finance/7/pluggy-item").includes("/open-finance/7"), "a releitura");
  const corpo = await page.locator("body").innerText();
  await ctx.close();
  assert.equal(texto, DO_WIDGET);
  assert.ok(!/boom/.test(corpo));
});

test("E5b sucesso SEM onClose (o SDK real pode não o mandar): anuncia, destrava e relê UMA vez; o onClose tardio não relê de novo", async () => {
  const { ctx, page, srv } = await telaRecon();
  await reconectar(page, "Nubank");
  await widgetAberto(page);
  await sucesso(page);
  await aviso(page).filter({ hasText: "Nubank reconectado." }).waitFor({ state: "attached" });
  await ate(page, () => depoisDo(srv, "/open-finance/7/pluggy-item").includes("/open-finance/7"), "a releitura do snapshot");
  await page.waitForTimeout(300);
  const leituras = () => depoisDo(srv, "/open-finance/7/pluggy-item").filter((p) => p === "/open-finance/7").length;
  const antes = leituras();
  await fechaWidget(page);
  await page.waitForTimeout(500);
  await ctx.close();
  assert.equal(antes, 1);
  assert.equal(leituras(), 1);
});

const HTML = "<html><head><title>Error 1015</title></head><body><h1>Rate limited</h1></body></html>";
for (const [nome, tokenRes, esperado] of [
  ["429 com corpo HTML de proxy", (r) => r.fulfill({ status: 429, contentType: "text/html", body: HTML }), GENERICO],
  ["400 com mensagem de 400 caracteres", JSON_ERRO(400, "x".repeat(400)), GENERICO],
  ["402 do gate de plano com o JSON cru em 'detail'", JSON_ERRO(402, { error: "subscription_required" }), GENERICO],
  ["429 em JSON com texto curto (positivo)", JSON_ERRO(429, "Muitas tentativas. Espere um pouco."), "Muitas tentativas. Espere um pouco."],
]) {
  test(`E8 ${nome}: ${esperado === GENERICO ? "o genérico, nunca o corpo" : "o texto do servidor continua indo à tela"}`, async () => {
    const { ctx, page } = await telaRecon({ tokenRes });
    await reconectar(page, "Nubank");
    await alerta(page).waitFor();
    const r = { texto: await alerta(page).innerText(), corpo: await page.locator("body").innerText() };
    await ctx.close();
    assert.equal(r.texto, esperado);
    assert.ok(!/Error 1015|<html|xxxx|subscription_required/.test(r.corpo));
  });
}

test("E9 conexão em updating: só a pílula 'Atualizando…' que a lista já mostra, sem 'Acompanhar' e sem polling", async () => {
  const { ctx, page, srv } = await telaRecon({ conexoes: [con(11, "Nubank", "updating", "Atualizando…"), con(12, "Inter"), con(13, "Itaú", "paused", "Pausado")] });
  const n = srv.reqs.filter((p) => p === "/open-finance/7").length;
  await linha(page, "Nubank").click();
  await det(page).waitFor();
  await page.waitForTimeout(1500);
  const r = { pilula: await card(page, "Nubank").locator(".banco-estado").innerText(), acompanhar: await page.getByText("Acompanhar").count(), leituras: srv.reqs.filter((p) => p === "/open-finance/7").length - n };
  await ctx.close();
  assert.deepEqual(r, { pilula: "Atualizando…", acompanhar: 0, leituras: 0 });
});

for (const [width, height] of [[1280, 800], [390, 844]]) {
  test(`E10 layout ${width}x${height}: Reconectar acima do Desconectar, 44 px, dentro do painel, sem rolagem horizontal`, async () => {
    const { ctx, page } = await telaRecon({}, { width, height });
    await linha(page, "Nubank").click();
    await det(page).waitFor();
    const rec = await botaoRecon(page).boundingBox(), des = await det(page).getByRole("button", { name: "Desconectar banco", exact: true }).boundingBox(), painel = await det(page).boundingBox();
    const largura = await page.evaluate(() => [document.documentElement.scrollWidth, innerWidth]);
    await ctx.close();
    assert.ok(rec.height >= 44 && des.height >= 44, `alturas ${rec.height} / ${des.height}`);
    assert.ok(rec.y + rec.height <= des.y, "Reconectar acima do Desconectar");
    assert.ok(rec.x >= painel.x && rec.x + rec.width <= painel.x + painel.width + 0.5, "dentro do painel");
    assert.ok(largura[0] <= largura[1], `rolagem horizontal ${largura}`);
  });
}

test("E6 sair da rota com o connect-token pendente e voltar: nenhum widget abre, nada fica travado", async () => {
  const [segura, solta] = portao();
  const { ctx, page, srv } = await telaRecon({ tokenRes: async (r) => { await segura; await r.fulfill({ json: { accessToken: "acesso-xyz" } }).catch(() => {}); } });
  await reconectar(page, "Nubank");
  await ate(page, () => tokens(srv).length === 1, "o connect-token");
  await sai(page, "#/gastos");
  solta();
  await page.waitForTimeout(500);
  await sai(page, "#/conta/bancos");
  await esperaCards(page, 3);
  await linha(page, "Nubank").click();
  await det(page).waitFor();
  const r = { widgets: await widgetsAbertos(page), livre: await botaoRecon(page).isEnabled() };
  await ctx.close();
  assert.deepEqual(r, { widgets: 0, livre: true });
});

test("E6b widget aberto, sai da rota (sem voltar): o widget NÃO é destruído e o save acontece", async () => {
  const { ctx, page, srv } = await telaRecon();
  await reconectar(page, "Nubank");
  await widgetAberto(page);
  await sai(page, "#/gastos");
  await page.waitForTimeout(300);
  await sucesso(page);
  await ate(page, () => saves(srv).length === 1, "o save");
  await ctx.close();
});

test("E10b widget aberto, sai da rota, volta e clica Reconectar: o aviso de 'conexão aberta' (sem derrubar o widget), o save do widget aberto acontece e, fechado, o clique seguinte abre outro", async () => {
  const { ctx, page, srv } = await telaRecon();
  await prazoCurto(page, 1200);
  await reconectar(page, "Nubank");
  await widgetAberto(page);
  await sai(page, "#/gastos");
  await sai(page, "#/conta/bancos");
  await esperaCards(page, 3);
  await reconectarPorBaixo(page, "Nubank");
  await alertaCom(page, "Já tem uma conexão");
  await page.waitForTimeout(1800); // passa do prazo encurtado: um prazo armado derrubaria a sessão aberta
  const r = { alertas: await alerta(page).allInnerTexts(), widgets: await widgetsAbertos(page), tokens: tokens(srv).length };
  await sucesso(page); // a pessoa autoriza no widget que continuou aberto
  await ate(page, () => saves(srv).length === 1, "o save");
  await fechaWidget(page);
  await linha(page, "Nubank").click();
  await botaoRecon(page).click();
  await page.waitForFunction(() => window.__n === 2, null, { timeout: 8000 });
  await ctx.close();
  assert.deepEqual(r, { alertas: [OCUPADO], widgets: 1, tokens: 1 });
});

test("E10c outra conexão em curso (o seletor ou outra montagem): 'ocupado' sem destruí-la; o save dela acontece e o clique seguinte, depois de fechada, abre", async () => {
  const { ctx, page, srv } = await telaRecon();
  await page.evaluate(() => { window.PBOpenFinance.reconnect("item-12"); }); // uma conexão que a página não iniciou
  await widgetAberto(page);
  await reconectarPorBaixo(page, "Nubank");
  await alertaCom(page, "Já tem uma conexão");
  const r = { widgets: await widgetsAbertos(page), tokens: tokens(srv).length, travado: await page.evaluate(() => !!document.querySelector("dialog[open]")) };
  await sucesso(page, "item-12");
  await ate(page, () => saves(srv).length === 1, "o save da outra conexão");
  await fechaWidget(page);
  await linha(page, "Nubank").click();
  await botaoRecon(page).click();
  await page.waitForFunction(() => window.__n === 2, null, { timeout: 8000 });
  await ctx.close();
  assert.deepEqual(r, { widgets: 1, tokens: 1, travado: false });
  assert.match(saves(srv)[0][3], /item-12/);
});

test("E11 o SDK manda onClose ANTES do onSuccess: o banco autorizado é salvo mesmo assim e a lista é relida", async () => {
  const { ctx, page, srv } = await telaRecon();
  await reconectar(page, "Nubank");
  await widgetAberto(page);
  await fechaWidget(page);
  await page.waitForTimeout(300);
  const antes = depoisDo(srv, "/open-finance/7/connect-token").filter((p) => p === "/open-finance/7").length;
  await sucesso(page);
  await ate(page, () => saves(srv).length === 1, "o save");
  await ate(page, () => depoisDo(srv, "/open-finance/7/pluggy-item").includes("/open-finance/7"), "a releitura depois do save");
  await ctx.close();
  assert.ok(antes >= 1);
});

test("E7 conta trocada com o widget aberto: o POST do save vai à conta do clique (7; o servidor recusa com a sessão da 8), nada é dito na nova (8) e o perfil é relido", async () => {
  let uid = 7;
  const posts = [];
  const { ctx, page, srv } = await telaRecon({ perfil: () => ({ display_name: "Ana", email: "ana@exemplo.com", user_id: uid, whatsapp_linked: false }) }, { antes: async (c) => {
    c.on("request", (q) => { if (q.method() === "POST" && /pluggy-item/.test(q.url())) posts.push(new URL(q.url()).pathname); });
    await c.route((u) => /^\/open-finance\/8/.test(u.pathname), (r) => r.fulfill({ json: { ok: true, connections: [], connectors: [], of_banks_max: 3, em_uso: 0, pode_adicionar: true } }));
  } });
  await reconectar(page, "Nubank");
  await widgetAberto(page);
  uid = 8;
  const antes = perfis(srv);
  await refoco(page);
  await ate(page, () => perfis(srv) > antes, "o perfil da conta nova");
  await page.waitForTimeout(400);
  const antesDoSave = perfis(srv);
  await sucesso(page);
  await ate(page, () => posts.length === 1, "o save");
  await page.waitForTimeout(600);
  const r = { posts: [...posts], aviso: await aviso(page).innerText(), alertas: await alerta(page).count(), perfilRelido: perfis(srv) > antesDoSave };
  await ctx.close();
  assert.deepEqual(r, { posts: ["/open-finance/7/pluggy-item"], aviso: "", alertas: 0, perfilRelido: true });
});

test("E6c o prazo da montagem que saiu NÃO destrói o módulo da montagem nova: sai da rota com o connect-token pendente, volta, reconecta, passa do prazo e o save acontece", async () => {
  const [segura, solta] = portao();
  let n = 0;
  const { ctx, page, srv } = await telaRecon({ tokenRes: async (r) => { if (++n === 1) { await segura; return r.abort().catch(() => {}); } return r.fulfill({ json: { accessToken: "acesso-xyz" } }); } });
  await prazoCurto(page, 700);
  await reconectar(page, "Nubank");
  await ate(page, () => tokens(srv).length === 1, "o 1º connect-token");
  await sai(page, "#/gastos");
  await sai(page, "#/conta/bancos");
  await esperaCards(page, 3);
  solta();
  await reconectar(page, "Nubank");
  await widgetAberto(page);
  await page.waitForTimeout(1200); // passa do prazo (700 ms) da montagem velha
  await sucesso(page);
  await ate(page, () => saves(srv).length === 1, "o save depois do prazo velho");
  await ctx.close();
});

test("E7b troca de conta com o connect-token pendente: a conta nova nasce destravada (o Reconectar dela abre)", async () => {
  let uid = 7;
  const [segura, solta] = portao();
  const { ctx, page, srv } = await telaRecon({ perfil: () => ({ display_name: "Ana", email: "ana@exemplo.com", user_id: uid, whatsapp_linked: false }), tokenRes: async (r) => { await segura; return r.abort().catch(() => {}); } }, { antes: async (c) => {
    await c.route((u) => u.pathname === "/open-finance/8", (r) => r.fulfill({ json: { ok: true, connections: [con(31, "Outro")] } }));
    await c.route((u) => /^\/open-finance\/8\/(limite|connectors)$/.test(u.pathname), (r) => r.fulfill({ json: u_ok(r) }));
  } });
  await reconectar(page, "Nubank");
  await ate(page, () => tokens(srv).length === 1, "o connect-token");
  uid = 8;
  await refoco(page);
  await card(page, "Outro").waitFor();
  await linha(page, "Outro").click();
  await det(page).waitFor();
  const livre = await botaoRecon(page).isEnabled();
  const status = await aviso(page).innerText(); // o "Reconectando Nubank…" da conta 7 não fica no status da 8
  solta();
  await ctx.close();
  assert.ok(livre);
  assert.equal(status, "");
});
const u_ok = (r) => (r.request().url().endsWith("/limite") ? LIMITE() : { ok: true, connectors: [] });

test("E5c erro do SDK com o widget aberto e depois o widget fecha: o foco volta à linha (não fica no <body>)", async () => {
  const { ctx, page } = await telaRecon();
  await reconectar(page, "Nubank");
  await widgetAberto(page);
  await erroDoSdk(page);
  await alertaCom(page, "Não deu para concluir");
  const comWidget = await naLinha(page, "Nubank");
  await fechaWidget(page);
  await page.waitForTimeout(300);
  const r = { comWidget, naLinha: await naLinha(page, "Nubank") };
  await ctx.close();
  assert.deepEqual(r, { comWidget: false, naLinha: true });
});

test("E12 reconexão concluída com a releitura lenta: o que vem do seletor depois (o mesmo widget salva de novo) NÃO é engolido: o seletor relê", async () => {
  const [segura, solta] = portao();
  let retido = false;
  const { ctx, page, srv } = await telaRecon({ snap: async (r) => { if (retido) await segura; return r.fulfill({ json: { ok: true, connections: PADRAO() } }).catch(() => {}); } });
  await reconectar(page, "Nubank");
  await widgetAberto(page);
  retido = true; // a releitura do `terminou` fica presa
  await sucesso(page);
  await ate(page, () => saves(srv).length === 1, "o 1º save");
  await ate(page, () => depoisDo(srv, "/open-finance/7/pluggy-item").includes("/open-finance/7"), "a releitura (presa) começou");
  const leituras = () => srv.reqs.filter((p) => p === "/open-finance/7").length;
  const antes = leituras();
  await sucesso(page); // o mesmo widget salva de novo: o onConnected vem pelo seletor, a reconexão já terminou
  await ate(page, () => saves(srv).length === 2, "o 2º save");
  await page.waitForTimeout(500);
  const depois = leituras();
  solta();
  await ctx.close();
  assert.ok(depois > antes, `o seletor não releu: ${antes} → ${depois}`);
});

test("E13 a reconexão de uma montagem que saiu NÃO puxa o foco para o título da página nova", async () => {
  const { ctx, page } = await telaRecon();
  await reconectar(page, "Nubank");
  await widgetAberto(page);
  await sai(page, "#/gastos");
  await page.locator("#page-title").waitFor();
  await page.evaluate(() => { document.getElementById("page-title").blur?.(); (document.querySelector("#main a, #main button, button") ?? document.body).focus(); });
  const antes = await page.evaluate(() => document.activeElement?.id || document.activeElement?.tagName);
  await fechaWidget(page);
  await page.waitForTimeout(800);
  const depois = await page.evaluate(() => document.activeElement?.id || document.activeElement?.tagName);
  await ctx.close();
  assert.notEqual(depois, "page-title");
  assert.equal(depois, antes);
});

test("E14 'Já tem uma conexão aberta' (sem id) fica na tela mesmo se o card clicado sumir na releitura", async () => {
  const { ctx, page, srv } = await telaRecon();
  await page.evaluate(() => { window.PBOpenFinance.reconnect("item-12"); });
  await widgetAberto(page);
  await reconectarPorBaixo(page, "Nubank");
  await alertaCom(page, "Já tem uma conexão");
  tiraDoSnapshot(srv, 11);
  await refoco(page);
  await esperaCards(page, 2);
  await page.waitForTimeout(300);
  const r = { nubank: await card(page, "Nubank").count(), alertas: await alerta(page).allInnerTexts() };
  await ctx.close();
  assert.deepEqual(r, { nubank: 0, alertas: [OCUPADO] });
});

const rotas8 = async (c) => {
  await c.route((u) => u.pathname === "/open-finance/8", (r) => r.fulfill({ json: { ok: true, connections: [con(31, "Outro")] } }));
  await c.route((u) => /^\/open-finance\/8\/(limite|connectors)$/.test(u.pathname), (r) => r.fulfill({ json: r.request().url().endsWith("/limite") ? LIMITE() : { ok: true, connectors: [] } }));
};
const focoEm = (page) => page.evaluate(() => document.activeElement?.id || document.activeElement?.tagName);

test("E15 releitura lenta + troca de conta: o 'Nubank reconectado.' da conta 7 NÃO aparece no status da 8 e o foco não é tocado", async () => {
  let uid = 7, retido = false;
  const [segura, solta] = portao();
  const { ctx, page, srv } = await telaRecon({ perfil: () => ({ display_name: "Ana", email: "a@e.com", user_id: uid, whatsapp_linked: false }),
    snap: async (r) => { if (retido) await segura; return r.fulfill({ json: { ok: true, connections: PADRAO() } }).catch(() => {}); } }, { antes: rotas8 });
  await reconectar(page, "Nubank");
  await widgetAberto(page);
  retido = true;
  await sucesso(page);
  await ate(page, () => saves(srv).length === 1, "o save");
  await ate(page, () => depoisDo(srv, "/open-finance/7/pluggy-item").includes("/open-finance/7"), "a releitura presa");
  uid = 8;
  await refoco(page);
  await card(page, "Outro").waitFor();
  const antes = { aviso: await aviso(page).innerText(), foco: await focoEm(page) };
  solta();
  await page.waitForTimeout(800);
  const depois = { aviso: await aviso(page).innerText(), foco: await focoEm(page) };
  await ctx.close();
  assert.equal(depois.aviso, "");
  assert.deepEqual(depois, { aviso: antes.aviso, foco: antes.foco });
});

test("E16 troca de conta: o toast do seletor (erroOF) da conta 7 não fica na conta 8", async () => {
  let uid = 7;
  const { ctx, page } = await telaRecon({ perfil: () => ({ display_name: "Ana", email: "a@e.com", user_id: uid, whatsapp_linked: false }) }, { antes: rotas8 });
  await page.evaluate(() => { window.PBOpenFinance.reconnect(""); }); // id inválido: onError do módulo -> toast
  await page.locator(".banco-toast").waitFor();
  uid = 8;
  await refoco(page);
  await card(page, "Outro").waitFor();
  await page.waitForTimeout(300);
  const n = await page.locator(".banco-toast").count();
  await ctx.close();
  assert.equal(n, 0);
});

test("E17 o onClose tardio de uma reconexão antiga (o SDK não o mandou a tempo) NÃO puxa o foco para a linha depois que a seguinte terminou", async () => {
  const { ctx, page } = await telaRecon();
  await reconectar(page, "Nubank");
  await widgetAberto(page);
  await page.evaluate(() => { window.__w1 = window.__w; });
  await sucesso(page); // fim com o widget por cima (`pendente`); o onClose dele não chega
  await aviso(page).filter({ hasText: "Nubank reconectado." }).waitFor({ state: "attached" });
  await page.evaluate(() => document.getElementById("pluggy-falso").remove()); // o iframe saiu, sem onClose
  await reconectar(page, "Nubank"); // a 2ª reconexão (a guarda do módulo já foi solta pelo save)
  await page.waitForFunction(() => window.__n === 2, null, { timeout: 8000 });
  await fechaWidget(page); // fecha o 2º: termina com foco na linha
  await page.waitForTimeout(500);
  await page.evaluate(() => document.getElementById("page-title").focus());
  await page.evaluate(() => { window.__w1.onClose(); }); // o onClose atrasado do 1º
  await page.waitForTimeout(300);
  const foco = await focoEm(page);
  await ctx.close();
  assert.equal(foco, "page-title");
});

// O caminho irmão: o "+" do seletor (o MESMO init) tem de filtrar o texto do servidor como o Reconectar.
const CRU = "HTTPSConnectionPool(host='api.pluggy.ai', port=443): Max retries exceeded boom-cru";
const toastDoSeletor = async (tokenRes) => {
  const { ctx, page } = await telaRecon({ tokenRes }, { antes: (c) => c.route((u) => u.pathname === "/auth/me", (r) => r.fulfill({ json: { of_banks_max: 3 } })) });
  await page.getByRole("link", { name: "Conectar Bradesco", exact: true }).click();
  await page.locator("#bankpick-go:not([disabled])").waitFor();
  await page.waitForTimeout(400); // passada a janela do duplo clique
  await page.locator("#bankpick-go").click();
  await page.locator(".banco-toast").waitFor({ timeout: 8000 });
  const toast = await page.locator(".banco-toast").innerText();
  await ctx.close();
  return toast;
};
test("E18 o toast do '+' do seletor não mostra o corpo cru do servidor (502); o texto de pessoa do servidor (429 curto) continua indo", async () => {
  const cru = await toastDoSeletor(JSON_ERRO(502, CRU));
  const curto = await toastDoSeletor(JSON_ERRO(429, "Muitas tentativas. Espere um pouco."));
  assert.ok(!cru.includes("boom-cru") && !/HTTPSConnectionPool/.test(cru), `o toast mostra o corpo cru: ${cru}`);
  assert.match(cru, /Não foi possível conectar o banco\./);
  assert.match(curto, /Muitas tentativas\. Espere um pouco\./);
});

test("E19 o onClose TARDIO do widget A, com a reconexão B em curso, NÃO termina a B: o sucesso de B ainda é anunciado como dela", async () => {
  const { ctx, page } = await telaRecon();
  await reconectar(page, "Nubank");
  await widgetAberto(page);
  await page.evaluate(() => { window.__w1 = window.__w; });
  await sucesso(page, "item-11"); // A conclui com o widget por cima
  await aviso(page).filter({ hasText: "Nubank reconectado." }).waitFor({ state: "attached" });
  await page.evaluate(() => document.getElementById("pluggy-falso").remove());
  await reconectar(page, "Inter"); // B começa (a guarda do módulo foi solta pelo save de A)
  await page.waitForFunction(() => window.__n === 2, null, { timeout: 8000 });
  await page.evaluate(() => { window.__w1.onClose(); }); // o onClose atrasado de A
  await page.waitForTimeout(500);
  await sucesso(page, "item-12");
  await aviso(page).filter({ hasText: "Inter reconectado." }).waitFor({ state: "attached", timeout: 5000 });
  const r = await aviso(page).innerText();
  await ctx.close();
  assert.equal(r, "Inter reconectado.");
});
