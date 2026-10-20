// Bancos conectados, layout da referência do dono (conta/ResumoBancos.tsx, BancoCard.tsx, MenuBanco.tsx, SegurancaBancos.tsx; lib/bancos.ts::quandoSync/ultimaSync):
//   · RES  o card resumo (quantos bancos, última sincronização, "Concluída"/"Atenção") e a coluna "Última atualização" de cada banco ("Hoje, 11:47", "Ontem, 23:30", "30/09, 12:00", "—");
//   · SYN  "Sincronizar" (o "Atualizar agora" do aviso do topo): 1 POST, todos os botões travam juntos, o resultado na tela e na região viva, sem laço de retentativa;
//   · MEN  o menu "…" (Reconectar só quando reconectável, Desconectar): ARIA de menu, Esc/clique fora/Tab, um por vez, foco devolvido, e as ações são as MESMAS do detalhe (Confirma, widget).
// O relógio da página é 2026-10-02 15:00Z (São Paulo: 12:00), `AGORA` do _bancos.mjs = 14:47Z = 11:47 em São Paulo.
//
// Controles negativos (desligue, rebuild `npm --prefix webapp run build`, o grupo tem de ficar vermelho; restaure e rebuild):
//  - RES: em lib/bancos.ts, `timeZone: FUSO` do AAAA_MM_DD por "UTC" (o "Ontem, 23:30" vira "Hoje"); em ResumoBancos.tsx, `tons.includes("erro") ? "erro" : tons.includes("pendente") ? "pendente" : "ok"` por "ok" ("Concluída" com banco em atenção).
//  - SYN: em Bancos.tsx, `sincronizando={sincronizando}` por `sincronizando={false}` (nenhum botão trava); `automatico: false` por `true` (um banco em `updating` parte uma rodada sozinho ao abrir a página).
//  - MEN: em MenuBanco.tsx, tirar a linha do `Escape` (Esc não fecha), o `document.addEventListener("pointerdown", fora)` (clique fora não fecha), o `botao.current?.focus()` do Escape (foco vai ao body),
//    ou em Bancos.tsx `aberto={menu === c.id}` por `aberto={menu !== null}` (vários menus abertos juntos).
import { test } from "node:test";
import assert from "node:assert/strict";
import { abrir, navegador } from "./_guia.mjs";
import { ate } from "./_conta_perfil.mjs";
import { ERRO_500, con, item, refreshDe, semReticencias, servidorBancos } from "./_bancos.mjs";
import { aviso, card, confirmar, dlg, manter, esperaCards } from "./_bancos_desconectar.mjs";
import { depoisDo, fechaWidget, saves, sucesso, telaRecon, tokens, widgetAberto } from "./_bancos_reconectar.mjs";

const browser = navegador();
void browser;

const SEM_VAGAS = { vagas: "" };
const ATENCAO = "needs_user_action";
const texto = (page, seletor) => page.locator(seletor).allInnerTexts();
const foco = (page) => page.evaluate(() => document.activeElement?.getAttribute("aria-label") ?? document.activeElement?.tagName);
const noFoco = (page, rotulo) => page.waitForFunction((t) => document.activeElement?.getAttribute("aria-label") === t, rotulo, { timeout: 8000 });
const maisDe = (page, nome) => card(page, nome).getByRole("button", { name: `Mais ações de ${nome}` });
const itensDo = (page) => page.getByRole("menuitem").allInnerTexts();

// ---- RES: resumo e "Última atualização" ----
test("RES o resumo e a coluna 'Última atualização' (fuso de São Paulo, não o do relógio UTC): Hoje, Ontem (23:30 de ontem em SP já é 'hoje' em UTC), dd/mm e '—'", async () => {
  const conexoes = [con(11, "Nubank"), con(12, "Inter", "updated", "Atualizado", "2026-10-02T02:30:00+00:00"), con(13, "Itaú", ATENCAO, "Atenção", "2026-09-30T15:00:00+00:00"), con(14, "Caixa", "updated", "Atualizado", null), con(15, "XP", "updated", "Atualizado", "lixo")];
  const { ctx, page } = await telaRecon({ conexoes }, SEM_VAGAS);
  const r = {
    linhas: await texto(page, ".banco-card .banco-quando > span:last-child"), titulo: await texto(page, ".banco-resumo .resumo-n"), zona: await texto(page, ".banco-resumo .resumo-txt"),
    estado: await page.locator(".banco-resumo .banco-estado").evaluate((e) => [e.textContent, e.className]), icone: await page.locator(".banco-resumo .resumo-ico i").evaluateAll((l) => l.map((e) => e.className)),
  };
  await ctx.close();
  assert.deepEqual(r.linhas, ["Hoje, 11:47", "Ontem, 23:30", "30/09, 12:00", "—", "—"]);
  assert.deepEqual(r.titulo, ["5"]);
  assert.match(r.zona[0], /^5\nbancos conectados/);
  assert.match(r.zona[1], /^Última sincronização\nHoje, 11:47\nAtenção$/); // a mais recente entre as conexões
  assert.deepEqual(r.estado, ["Atenção", "banco-estado estado-erro"]); // o pior tom (erro vence pendente)
  assert.deepEqual(r.icone, ["ph ph-bank", "ph ph-warning"]);
});

test("RES 'Concluída' só com todos em tom ok (verde, ícone de check); banco em coleta (updating) NÃO tira o 'Concluída' (decisão do dono), mas 'partial' sim, em amarelo; um banco só diz 'banco conectado'", async () => {
  const lido = async (conexoes) => {
    const { ctx, page } = await telaRecon({ conexoes }, SEM_VAGAS);
    const r = { estado: await page.locator(".banco-resumo .banco-estado").evaluate((e) => [e.textContent, e.className]), icone: await page.locator(".banco-resumo .resumo-ico i").last().getAttribute("class"), rotulo: await page.locator(".banco-resumo .resumo-txt").first().innerText() };
    await ctx.close();
    return r;
  };
  assert.deepEqual(await lido([con(11, "Nubank")]), { estado: ["Concluída", "banco-estado estado-ok"], icone: "ph ph-check-circle", rotulo: "1\nbanco conectado\n2 de 3 bancos" }); // positivo; as vagas, discretas, na mesma zona
  assert.deepEqual((await lido([con(11, "Nubank"), con(12, "Inter", "updating", "Atualizando…")])).estado, ["Concluída", "banco-estado estado-ok"]); // coleta é normal
  assert.deepEqual((await lido([con(11, "Nubank"), con(12, "Inter", "partial", "Parcial")])).estado, ["Atenção", "banco-estado estado-pendente"]); // positivo: pendente de verdade segue sendo atenção
});

test("RES o pior tom vence nas DUAS ordens: erro vence pendente (banco em erro antes ou depois do que coleta); pendente sozinho fica pendente", async () => {
  const lido = async (conexoes) => {
    const { ctx, page } = await telaRecon({ conexoes }, SEM_VAGAS);
    const r = await page.locator(".banco-resumo .banco-estado").evaluate((e) => [e.textContent, e.className]);
    await ctx.close();
    return r;
  };
  const coleta = con(11, "Nubank", "updating", "Atualizando…"), erro = con(12, "Inter", ATENCAO, "Atenção");
  assert.deepEqual(await lido([coleta, erro]), ["Atenção", "banco-estado estado-erro"]);
  assert.deepEqual(await lido([erro, coleta]), ["Atenção", "banco-estado estado-erro"]);
  assert.deepEqual(await lido([coleta, con(13, "Itaú", "partial", "Parcial")]), ["Atenção", "banco-estado estado-pendente"]); // positivo: sem erro, o pendente aparece
  assert.deepEqual(await lido([coleta, con(13, "Itaú")]), ["Concluída", "banco-estado estado-ok"]); // a coleta sozinha não conta
});

test("RES a 'última sincronização' do resumo é a MAIS RECENTE, esteja na 1ª, no meio ou na última posição da lista", async () => {
  const velha = "2026-09-30T15:00:00+00:00", meio = "2026-10-02T02:30:00+00:00", nova = "2026-10-02T14:47:59+00:00";
  const lido = async (datas) => {
    const { ctx, page } = await telaRecon({ conexoes: datas.map((d, i) => con(11 + i, ["Nubank", "Inter", "Itaú"][i], "updated", "Atualizado", d)) }, SEM_VAGAS);
    const r = await page.locator(".banco-resumo .resumo-quando").innerText();
    await ctx.close();
    return r;
  };
  assert.equal(await lido([velha, meio, nova]), "Hoje, 11:47"); // a mais nova por último
  assert.equal(await lido([velha, nova, meio]), "Hoje, 11:47"); // no meio
  assert.equal(await lido([nova, velha, meio]), "Hoje, 11:47"); // no começo
  assert.equal(await lido([velha, meio, "lixo"]), "Ontem, 23:30"); // data inválida não vence nem apaga
});

test("RES data de OUTRO ano leva o ano ('02/10/2025, 09:00'), do ano corrente não; na virada de ano, 'Ontem' é de ontem e o dia 30/12 já é do ano passado (relógio fixo)", async () => {
  const conexoes = [con(11, "Nubank", "updated", "Atualizado", "2025-10-02T12:00:00+00:00"), con(12, "Inter", "updated", "Atualizado", "2026-01-05T12:00:00+00:00"), con(13, "Itaú", "updated", "Atualizado", "2026-10-02T02:30:00+00:00")];
  const { ctx, page, srv } = await telaRecon({ conexoes }, SEM_VAGAS);
  const antes = { linhas: await texto(page, ".banco-card .banco-quando > span:last-child"), resumo: await page.locator(".banco-resumo .resumo-quando").innerText() };
  // Vira o relógio para 2027-01-01 09:00 em São Paulo e remonta a tela (a data é calculada ao renderizar), com o snapshot de datas da virada.
  srv.conexoes = [con(11, "Nubank", "updated", "Atualizado", "2026-12-31T22:30:00+00:00"), con(12, "Inter", "updated", "Atualizado", "2026-12-30T12:00:00+00:00"), con(13, "Itaú", "updated", "Atualizado", "2027-01-01T11:00:00+00:00")];
  await page.clock.setFixedTime(new Date("2027-01-01T12:00:00Z"));
  await page.evaluate(() => { location.hash = "#/"; });
  await page.waitForFunction(() => !document.querySelector(".banco-card"), null, { timeout: 8000 });
  await page.evaluate(() => { location.hash = "#/conta/bancos"; });
  await page.locator(".banco-card", { hasText: "Ontem, 19:30" }).waitFor(); // o cache velho aparece primeiro: espera o snapshot novo
  const virada = { linhas: await texto(page, ".banco-card .banco-quando > span:last-child"), resumo: await page.locator(".banco-resumo .resumo-quando").innerText() };
  await ctx.close();
  assert.deepEqual(antes.linhas, ["02/10/2025, 09:00", "05/01, 09:00", "Ontem, 23:30"]);
  assert.equal(antes.resumo, "Ontem, 23:30");
  assert.deepEqual(virada.linhas, ["Ontem, 19:30", "30/12/2026, 09:00", "Hoje, 08:00"]);
  assert.equal(virada.resumo, "Hoje, 08:00");
});

test("RES sem banco: '0 bancos conectados', '—' na sincronização, ladrilho NEUTRO (relógio, sem verde nem alerta), nenhum 'Concluída' e um único 'Conectar banco' (no resumo, não no estado vazio)", async () => {
  const { ctx, page } = await telaRecon({ conexoes: [] }, SEM_VAGAS);
  await page.locator(".empty img").waitFor();
  const ladrilho = await page.locator(".banco-resumo .resumo-ico").evaluateAll((l) => l.map((e) => [e.className, e.querySelector("i").className]));
  const r = { resumo: await page.locator(".banco-resumo").innerText(), conectar: await page.getByRole("link", { name: "Conectar banco", exact: true }).count(), vazio: await page.locator(".empty").innerText(), seguro: await page.locator(".banco-seguro").count() };
  await ctx.close();
  assert.match(r.resumo, /^0\nbancos conectados/);
  assert.ok(r.resumo.includes("—") && !r.resumo.includes("Concluída") && !r.resumo.includes("Atenção"), r.resumo);
  assert.deepEqual([r.conectar, r.vazio, r.seguro], [1, "Nenhum banco conectado.", 1]);
  assert.deepEqual(ladrilho, [["resumo-ico", "ph ph-bank"], ["resumo-ico", "ph ph-clock"]]); // sem `estado-ok`: nada de verde com 0 bancos
});

test("RES a segurança fecha a página: texto e o link 'Saiba mais' externo (nova aba, noopener) para o Open Finance do Banco Central", async () => {
  const { ctx, page } = await telaRecon({}, { vagas: "2 de 3 bancos" });
  const r = await page.locator(".banco-seguro").evaluate((e) => { const a = e.querySelector("a"); return { texto: e.innerText, href: a.href, alvo: a.target, rel: a.rel, nome: a.getAttribute("aria-label"), ultimo: !e.nextElementSibling }; });
  await ctx.close();
  assert.match(r.texto, /^Seus dados estão seguros\nUtilizamos o Open Finance, um padrão do Banco Central\. Você pode revogar as conexões a qualquer momento\./);
  assert.deepEqual([r.href, r.alvo, r.rel, r.ultimo], ["https://www.bcb.gov.br/estabilidadefinanceira/openfinance", "_blank", "noopener noreferrer", true]);
  assert.match(r.nome, /^Saiba mais/); // o nome acessível começa pelo texto visível
});

// ---- SYN: Sincronizar ----
const sincronizar = (page, n = 0) => page.locator(".banco-card").nth(n).locator("button[aria-busy]");
const estadoDosBotoes = (page) => page.locator(".banco-card button[aria-busy]").evaluateAll((l) => l.map((b) => [b.getAttribute("aria-busy"), b.getAttribute("aria-disabled"), b.innerText.trim(), b.getAttribute("aria-label")]));

test("SYN um clique: 1 POST /open-finance/7/refresh com CSRF; TODOS os botões travam juntos (aria-busy/aria-disabled, 'Sincronizando…') e o 2º clique em outro banco não manda outro POST; no fim voltam e o snapshot é relido", async () => {
  let solta;
  const preso = new Promise((ok) => { solta = ok; });
  const { ctx, page, srv } = await telaRecon({ refresh: async (r) => { await preso; return r.fulfill({ json: refreshDe([item("item-11"), item("item-12", "updated", false, null, "ok", "Inter"), item("item-13", "updated", false, null, "ok", "Itaú")]) }); } });
  const ocioso = await estadoDosBotoes(page);
  await sincronizar(page, 1).click();
  await ate(page, () => srv.posts.length === 1, "o POST do refresh");
  await page.locator(".banco-card button[aria-busy=true]").nth(2).waitFor(); // os 3
  const durante = await estadoDosBotoes(page);
  const lerDentro = await aviso(page).innerText();
  await sincronizar(page, 2).click({ force: true }); // clique no botão travado (aria-disabled; o Playwright o trata como desabilitado, daí o force): nada de 2º POST
  await page.waitForTimeout(300);
  const posts = srv.posts.length;
  solta();
  await page.locator(".banco-card button[aria-busy=false]").nth(2).waitFor();
  await ate(page, () => depoisDo(srv, "/open-finance/7/refresh").includes("/open-finance/7"), "o snapshot relido depois");
  const fim = await estadoDosBotoes(page);
  const csrf = srv.posts[0].headers["x-csrf-token"];
  await ctx.close();
  assert.deepEqual(ocioso, Array(3).fill(["false", null, "Sincronizar", "Sincronizar todos os bancos"]));
  assert.deepEqual(durante, Array(3).fill(["true", "true", "Sincronizando…", "Sincronizando…"]));
  assert.equal(lerDentro, "Atualizando os dados do banco"); // a região viva
  assert.equal(posts, 1);
  assert.deepEqual(fim, ocioso);
  assert.equal(csrf, "tok-123");
});

test("SYN falha do servidor (500): o texto da falha na tela e na região viva, os botões voltam, e NENHUMA retentativa sozinha (1 POST); um novo clique manda o 2º", async () => {
  const { ctx, page, srv } = await telaRecon({ refresh: ERRO_500 });
  await sincronizar(page).click();
  await page.locator(".banco-sync").waitFor();
  await page.locator(".banco-card button[aria-busy=false]").nth(2).waitFor();
  await page.waitForTimeout(1200); // sem laço: nada mais sai
  const r = { tela: await page.locator(".banco-sync").innerText(), classe: await page.locator(".banco-sync").getAttribute("class"), viva: await aviso(page).innerText(), posts: srv.posts.length, botoes: await estadoDosBotoes(page) };
  await sincronizar(page).click(); // positivo: o usuário pode tentar de novo
  await ate(page, () => srv.posts.length === 2, "o 2º POST, do clique");
  await ctx.close();
  assert.equal(r.tela, "Não conseguimos atualizar agora. Tente de novo.");
  assert.equal(r.classe, "banco-sync warn");
  assert.equal(r.viva, "Não conseguimos atualizar agora. Tente de novo.");
  assert.equal(r.posts, 1);
  assert.deepEqual(r.botoes, Array(3).fill(["false", null, "Sincronizar", "Sincronizar todos os bancos"]));
});

test("SYN banco ainda coletando (still_updating): a rodada acompanha, os botões seguem travados e a tela diz que o banco está atualizando sozinho; sem 2º POST", async () => {
  const { ctx, page, srv } = await telaRecon({ refresh: (r) => r.fulfill({ json: refreshDe([item("item-11", "updating", true, null, "ok", "Nubank")]) }) });
  await sincronizar(page).click();
  await page.locator(".banco-sync").waitFor();
  await page.waitForTimeout(600);
  const r = { tela: await page.locator(".banco-sync").innerText(), classe: await page.locator(".banco-sync").getAttribute("class"), botoes: await estadoDosBotoes(page), posts: srv.posts.length };
  await ctx.close();
  assert.equal(r.tela, "O banco está atualizando. Seus dados aparecem aqui sozinhos quando terminar.");
  assert.equal(r.classe, "banco-sync"); // não é falha: sem o tom de alerta
  assert.deepEqual(r.botoes, Array(3).fill(["true", "true", "Sincronizando…", "Sincronizando…"]));
  assert.equal(r.posts, 1);
});

test("SYN a página NÃO parte rodada sozinha: com todos os bancos em 'updating' (no aviso do topo uma leitura nova os acompanha sem clique) os botões seguem livres e nada de texto", async () => {
  const s = servidorBancos({ conexoes: [con(11, "Nubank", "updating", "Atualizando…"), con(12, "Inter", "updating", "Atualizando…")], bancos: "padrao" });
  const { ctx, page } = await abrir({ guia: "concluido", rota: "/conta/bancos", antes: s.antes });
  await esperaCards(page, 2);
  await semReticencias(page);
  // O relógio do harness é fixo, e a (a0) só parte de uma leitura POSTERIOR ao marco: anda o relógio e faz o "onConnected" do seletor reler o snapshot (invalida as consultas sem mexer no marco).
  await page.clock.setFixedTime(new Date("2026-10-02T15:05:00Z"));
  await page.evaluate(() => window.__of.cb.onConnected({}));
  await ate(page, () => s.reqs.filter((q) => q === "/open-finance/7").length >= 2, "a releitura do snapshot");
  await page.waitForTimeout(800);
  const r = { botoes: await estadoDosBotoes(page), texto: await page.locator(".banco-sync").count(), posts: s.posts.length };
  await ctx.close();
  assert.deepEqual(r, { botoes: Array(2).fill(["false", null, "Sincronizar", "Sincronizar todos os bancos"]), texto: 0, posts: 0 });
});

// ---- MEN: o menu "…" ----
test("MEN abre com o 1º item focado; Reconectar só em banco reconectável (o pausado só tem Desconectar); aria-haspopup/expanded/controls", async () => {
  const { ctx, page } = await telaRecon({ conexoes: [con(11, "Nubank"), con(12, "Inter", "paused", "Pausado")] }, SEM_VAGAS);
  const botao = maisDe(page, "Nubank");
  const fechado = { popup: await botao.getAttribute("aria-haspopup"), aberto: await botao.getAttribute("aria-expanded"), menus: await page.getByRole("menu").count() };
  await botao.click();
  const nubank = { itens: await itensDo(page), aberto: await botao.getAttribute("aria-expanded"), controla: await botao.getAttribute("aria-controls"), id: await page.getByRole("menu").getAttribute("id"), nome: await page.getByRole("menu").getAttribute("aria-label"), foco: await page.evaluate(() => document.activeElement?.textContent) };
  await page.keyboard.press("Escape");
  await maisDe(page, "Inter").click();
  const inter = await itensDo(page);
  await ctx.close();
  assert.deepEqual(fechado, { popup: "menu", aberto: "false", menus: 0 });
  assert.deepEqual(nubank.itens, ["Reconectar", "Desconectar"]);
  assert.deepEqual([nubank.aberto, nubank.controla === nubank.id, nubank.nome, nubank.foco], ["true", true, "Ações de Nubank", "Reconectar"]);
  assert.deepEqual(inter, ["Desconectar"]);
});

test("MEN teclado: setas, Home e End andam (com volta); Esc fecha e devolve o foco ao botão; Tab fecha e segue sem cair no body", async () => {
  const { ctx, page } = await telaRecon({}, SEM_VAGAS);
  const botao = maisDe(page, "Nubank");
  const itemFocado = () => page.evaluate(() => document.activeElement?.textContent);
  await botao.click();
  const passos = [await itemFocado()];
  for (const tecla of ["ArrowDown", "ArrowDown", "ArrowUp", "End", "Home"]) { await page.keyboard.press(tecla); passos.push(await itemFocado()); }
  await page.keyboard.press("Escape");
  const esc = { menus: await page.getByRole("menu").count(), foco: await foco(page), aberto: await botao.getAttribute("aria-expanded") };
  await botao.press("Enter"); // Enter no botão abre
  const reaberto = await page.getByRole("menu").count();
  await page.keyboard.press("Tab");
  const tab = { menus: await page.getByRole("menu").count(), corpo: await foco(page) === "BODY" };
  await ctx.close();
  assert.deepEqual(passos, ["Reconectar", "Desconectar", "Reconectar", "Desconectar", "Desconectar", "Reconectar"]); // abre no 1º; ↓ ↓ (volta ao 1º) ↑ (volta ao último) End Home
  assert.deepEqual(esc, { menus: 0, foco: "Mais ações de Nubank", aberto: "false" });
  assert.equal(reaberto, 1);
  assert.deepEqual(tab, { menus: 0, corpo: false });
});

test("MEN clique fora fecha; só um menu aberto por vez (abrir o do Nubank fecha o do Inter); 'Detalhes' fecha o menu e abre o detalhe", async () => {
  const { ctx, page } = await telaRecon({}, SEM_VAGAS);
  await maisDe(page, "Inter").click();
  await page.locator("#page-title").click();
  const fora = await page.getByRole("menu").count();
  await maisDe(page, "Inter").click();
  await maisDe(page, "Nubank").click(); // o menu aberto cobre o "…" da linha de baixo (como todo menu): por isso abre o da linha de cima
  const um = { menus: await page.getByRole("menu").count(), dono: await page.getByRole("menu").getAttribute("aria-label"), inter: await maisDe(page, "Inter").getAttribute("aria-expanded") };
  await card(page, "Nubank").getByRole("button", { name: "Detalhes de Nubank" }).click();
  await page.locator("dialog.banco-det[open]").waitFor();
  const detalhe = await page.getByRole("menu").count();
  await ctx.close();
  assert.equal(fora, 0);
  assert.deepEqual(um, { menus: 1, dono: "Ações de Nubank", inter: "false" });
  assert.equal(detalhe, 0);
});

test("MEN 'Desconectar' abre o MESMO Confirma do detalhe: 'Manter banco' devolve o foco ao '…' sem DELETE; confirmar manda 1 DELETE, o banco sai, o aviso é lido e o foco vai ao título", async () => {
  const { ctx, page, srv } = await telaRecon({});
  await maisDe(page, "Nubank").click();
  await page.getByRole("menuitem", { name: "Desconectar" }).click();
  await dlg(page).waitFor();
  const pergunta = await dlg(page).innerText();
  const menuFechou = await page.getByRole("menu").count();
  await manter(page).click();
  await dlg(page).waitFor({ state: "detached" });
  await noFoco(page, "Mais ações de Nubank");
  const semDelete = srv.dels.length;
  await maisDe(page, "Nubank").click();
  await page.getByRole("menuitem", { name: "Desconectar" }).click();
  await confirmar(page).click();
  await esperaCards(page, 2);
  await ate(page, () => srv.dels.length === 1, "o DELETE");
  await page.waitForFunction(() => document.activeElement?.id === "page-title", null, { timeout: 8000 });
  const fim = { dels: srv.dels, aviso: await aviso(page).innerText(), nomes: await texto(page, ".banco-card .banco-nome") };
  await ctx.close();
  assert.match(pergunta, /Desconectar Nubank\?/);
  assert.equal(menuFechou, 0);
  assert.equal(semDelete, 0);
  assert.deepEqual(fim.dels, [["DELETE", "/open-finance/7/connections/11", "tok-123", null]]);
  assert.deepEqual([fim.aviso, fim.nomes], ["Nubank desconectado.", ["Inter", "Itaú"]]);
});

test("MEN 'Reconectar' usa a MESMA reconexão do detalhe: connect-token com item_id, widget acima da página, o save anuncia, e o foco volta ao '…' da linha quando o widget sai", async () => {
  const { ctx, page, srv } = await telaRecon({});
  await maisDe(page, "Nubank").click();
  await page.getByRole("menuitem", { name: "Reconectar" }).click();
  await widgetAberto(page);
  const menuFechou = await page.getByRole("menu").count();
  await sucesso(page);
  await ate(page, () => saves(srv).length === 1, "o POST do /pluggy-item");
  await aviso(page).filter({ hasText: "Nubank reconectado." }).waitFor({ state: "attached" });
  await fechaWidget(page);
  await noFoco(page, "Mais ações de Nubank");
  const t = tokens(srv);
  await ctx.close();
  assert.equal(menuFechou, 0);
  assert.deepEqual(t.map((p) => p[3]), ['{"item_id":"item-11"}']);
});
