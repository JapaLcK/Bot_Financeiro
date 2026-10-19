// Bancos conectados, achados do Tester sobre o layout novo (conta/Bancos.tsx, ResumoBancos.tsx, BancoCard.tsx, MenuBanco.tsx):
//   · MEN1 o menu "…" aberto não reabre sozinho quando o banco sai do snapshot e volta;
//   · RES2 a 3ª zona do resumo (Conectar banco) não existe vazia (sem vaga e sem mensagem, nem enquanto o /limite lê): o divisor só nasce junto com o botão;
//   · TRV  Sincronizar × Reconectar × Desconectar nos dois sentidos, no HANDLER (clique direto no DOM, que ignora o aria-disabled) e não só no visual;
//   · VIVA a região viva não guarda "Atualizando os dados do banco" depois da rodada, e o Sincronizar não apaga o aviso de outra ação ("X desconectado.").
// Controles negativos (desligue, rebuild `npm --prefix webapp run build`, o grupo tem de ficar vermelho; restaure e rebuild):
//  - MEN1: em Bancos.tsx, tirar o `useEffect` que zera `menu` quando o id sai de `lista`.
//  - RES2: em ResumoBancos.tsx, `{conectar && <div className="resumo-conectar">…</div>}` por `<div className="resumo-conectar">{conectar}</div>`.
//  - TRV: em Bancos.tsx, `sincronizar` sem a guarda `alvo || dialogo.current || emCurso.current || seletorOF()?.busy()` (3 testes); `bloqueado={!!alvo || recon || sincronizando}` do MenuBanco por `bloqueado={!!alvo}`
//    (itens do menu liberados na reconexão e na rodada); `sync.emVoo()` de `abrir` e `reconectarBanco` MAIS `|| sincronizando` do menu (o `disabled` do React já cobre o clique: a guarda do handler sozinha é redundante neste caminho).
//  - VIVA: em Bancos.tsx, tirar o `useEffect` que chama `sync.calar()` no fim da rodada (a região fica com "Atualizando…"); recolocar `setAviso("")` em `sincronizar` (o "Nubank desconectado." some).
import { test } from "node:test";
import assert from "node:assert/strict";
import { navegador } from "./_guia.mjs";
import { ate } from "./_conta_perfil.mjs";
import { LIMITE, item, refreshDe } from "./_bancos.mjs";
import { aviso, card, confirmar, dlg, det, esperaCards, linha, manter, refoco, tiraDoSnapshot } from "./_bancos_desconectar.mjs";
import { fechaWidget, saves, sucesso, telaRecon, tokens, widgetAberto, widgetsAbertos, PADRAO } from "./_bancos_reconectar.mjs";

const browser = navegador();
void browser;

const SEM_VAGAS = { vagas: "" };
const maisDe = (page, nome) => card(page, nome).getByRole("button", { name: `Mais ações de ${nome}` });
const sincronizar = (page, n = 0) => page.locator(".banco-card").nth(n).locator("button[aria-busy]");
const estadoSync = (b) => b.evaluate((e) => [e.getAttribute("aria-disabled"), e.innerText.trim()]);
const clicaDireto = (b) => b.evaluate((e) => e.click()); // o DOM, sem a checagem de visibilidade/overlay do Playwright e sem passar pelo `disabled` visual
const itensTravados = (page) => page.getByRole("menuitem").evaluateAll((l) => l.map((b) => [b.textContent, b.disabled]));
const fimDaRodada = (page, n) => page.locator(".banco-card button[aria-busy=false]").nth(n - 1).waitFor();
const segura = () => { let solta; const preso = new Promise((ok) => { solta = ok; }); return { preso, solta }; };

// ---- MEN1 ----
test("MEN1 o '…' aberto de um banco que sai do snapshot some junto: ao voltar (e refocar) o menu NÃO reabre e o foco não é roubado", async () => {
  const { ctx, page, srv } = await telaRecon({}, SEM_VAGAS);
  await maisDe(page, "Nubank").click();
  const aberto = await page.getByRole("menu").count();
  tiraDoSnapshot(srv, 11);
  await refoco(page);
  await esperaCards(page, 2);
  const saiu = { menus: await page.getByRole("menu").count(), foco: await page.evaluate(() => document.activeElement?.id) };
  srv.conexoes = PADRAO();
  await refoco(page);
  await esperaCards(page, 3);
  await page.waitForTimeout(300);
  const voltou = { menus: await page.getByRole("menu").count(), expandido: await maisDe(page, "Nubank").getAttribute("aria-expanded"), foco: await page.evaluate(() => document.activeElement?.getAttribute("role") ?? document.activeElement?.id) };
  await maisDe(page, "Nubank").click(); // positivo: abrir de novo à mão segue funcionando
  const manual = await page.getByRole("menu").count();
  await ctx.close();
  assert.equal(aberto, 1);
  assert.deepEqual(saiu, { menus: 0, foco: "page-title" });
  assert.deepEqual(voltou, { menus: 0, expandido: "false", foco: "page-title" });
  assert.equal(manual, 1);
});

// ---- RES2 ----
test("RES2 sem vaga e sem mensagem o resumo tem 2 zonas (nenhuma 3ª vazia com divisor); com mensagem do servidor, 3", async () => {
  const lido = async (limite) => {
    const { ctx, page } = await telaRecon({ limite }, { ...SEM_VAGAS, width: 1280, height: 800 });
    await page.waitForTimeout(300);
    const r = await page.locator(".banco-resumo > *").evaluateAll((l) => l.map((e) => [e.className, e.textContent.trim() !== ""]));
    await ctx.close();
    return r;
  };
  assert.deepEqual(await lido(LIMITE({ pode_adicionar: false, message: null })), [["resumo-zona", true], ["resumo-zona", true]]);
  assert.deepEqual(await lido(LIMITE({ pode_adicionar: false, message: "Você chegou ao limite do plano." })), [["resumo-zona", true], ["resumo-zona", true], ["resumo-conectar", true]]);
  assert.deepEqual((await lido(LIMITE())).map(([c]) => c), ["resumo-zona", "resumo-zona", "resumo-conectar"]); // positivo: com vaga, o botão
});

test("RES2 enquanto o /limite lê a 3ª zona não existe (sem divisor solto); a resposta traz a zona e o botão juntos", async () => {
  const { preso, solta } = segura();
  const { ctx, page } = await telaRecon({ limiteRes: async (r) => { await preso; return r.fulfill({ json: LIMITE() }); } }, { ...SEM_VAGAS, width: 1280, height: 800 });
  await page.waitForTimeout(300);
  const lendo = await page.locator(".banco-resumo > *").count();
  solta();
  await page.locator(".resumo-conectar a").waitFor();
  const depois = await page.locator(".banco-resumo > *").count();
  await ctx.close();
  assert.deepEqual([lendo, depois], [2, 3]);
});

// ---- TRV ----
test("TRV reconexão em curso: o Sincronizar fica aria-disabled (rótulo segue 'Sincronizar') e o clique DIRETO não manda POST; os itens do '…' de outro banco ficam desativados; ao terminar tudo volta", async () => {
  const { ctx, page, srv } = await telaRecon({}, SEM_VAGAS);
  await maisDe(page, "Nubank").click();
  await page.getByRole("menuitem", { name: "Reconectar" }).click();
  await widgetAberto(page);
  const pintado = await estadoSync(sincronizar(page, 1));
  await clicaDireto(sincronizar(page, 1));
  await page.waitForTimeout(300);
  const posts = srv.posts.length;
  await clicaDireto(maisDe(page, "Inter"));
  const itens = await itensTravados(page);
  await sucesso(page);
  await ate(page, () => saves(srv).length === 1, "o POST do /pluggy-item");
  await aviso(page).filter({ hasText: "Nubank reconectado." }).waitFor({ state: "attached" });
  await fechaWidget(page);
  await page.waitForFunction(() => document.querySelector(".banco-card button[aria-busy]")?.getAttribute("aria-disabled") === null, null, { timeout: 8000 });
  const liberado = await estadoSync(sincronizar(page, 1));
  await sincronizar(page, 1).click();
  await ate(page, () => srv.posts.length === 1, "o POST depois de liberar");
  await ctx.close();
  assert.deepEqual(pintado, ["true", "Sincronizar"]);
  assert.equal(posts, 0);
  assert.deepEqual(itens, [["Reconectar", true], ["Desconectar", true]]);
  assert.deepEqual(liberado, [null, "Sincronizar"]);
});

test("TRV diálogo do Desconectar aberto: o clique DIRETO no Sincronizar não manda POST (aria-disabled); fechado o diálogo, o Sincronizar volta", async () => {
  const { ctx, page, srv } = await telaRecon({}, SEM_VAGAS);
  await maisDe(page, "Nubank").click();
  await page.getByRole("menuitem", { name: "Desconectar" }).click();
  await dlg(page).waitFor();
  const pintado = await estadoSync(sincronizar(page, 1));
  await clicaDireto(sincronizar(page, 1));
  await page.waitForTimeout(300);
  const posts = srv.posts.length;
  await manter(page).click();
  await dlg(page).waitFor({ state: "detached" });
  await sincronizar(page, 1).click();
  await ate(page, () => srv.posts.length === 1, "o POST depois de fechar o diálogo");
  await ctx.close();
  assert.equal(pintado[0], "true");
  assert.equal(posts, 0);
});

test("TRV seletor ocupado (busy()): o clique no Sincronizar não manda POST; livre, manda", async () => {
  const { ctx, page, srv } = await telaRecon({}, SEM_VAGAS);
  await page.evaluate(() => { window.PBOpenFinance.busy = () => true; }); // o módulo real, só a resposta de `busy` é posta
  await clicaDireto(sincronizar(page, 1));
  await page.waitForTimeout(300);
  const posts = srv.posts.length;
  await page.evaluate(() => { window.PBOpenFinance.busy = () => false; });
  await sincronizar(page, 1).click();
  await ate(page, () => srv.posts.length === 1, "o POST com o seletor livre");
  await ctx.close();
  assert.equal(posts, 0);
});

test("TRV rodada do Sincronizar em voo: Reconectar e Desconectar (menu e detalhe) desativados, sem diálogo nem widget; fechar o detalhe segue livre; ao terminar voltam", async () => {
  const { preso, solta } = segura();
  const { ctx, page, srv } = await telaRecon({ refresh: async (r) => { await preso; return r.fulfill({ json: refreshDe([item("item-11")]) }); } }, SEM_VAGAS);
  await sincronizar(page, 1).click();
  await ate(page, () => srv.posts.length === 1, "o POST");
  await maisDe(page, "Nubank").click();
  const menu = await itensTravados(page);
  await page.getByRole("menuitem", { name: "Reconectar" }).click({ force: true });
  await page.getByRole("menuitem", { name: "Desconectar" }).click({ force: true });
  await page.keyboard.press("Escape");
  await linha(page, "Nubank").click();
  await det(page).waitFor();
  const detalhe = await det(page).locator(".banco-det-acoes button").evaluateAll((l) => l.map((b) => [b.textContent, b.disabled]));
  await page.keyboard.press("Escape");
  await det(page).waitFor({ state: "detached" });
  const r = { dialogo: await dlg(page).count(), tokens: tokens(srv).length, widgets: await widgetsAbertos(page) };
  solta();
  await fimDaRodada(page, 3);
  await maisDe(page, "Nubank").click();
  const depois = await itensTravados(page);
  await ctx.close();
  assert.deepEqual(menu, [["Reconectar", true], ["Desconectar", true]]);
  assert.deepEqual(detalhe, [["Reconectar banco", true], ["Desconectar banco", true]]);
  assert.deepEqual(r, { dialogo: 0, tokens: 0, widgets: 0 });
  assert.deepEqual(depois, [["Reconectar", false], ["Desconectar", false]]);
});

// ---- VIVA ----
test("VIVA no fim da rodada sem texto próprio (sucesso, rate_limited) a região viva esvazia; durante, diz 'Atualizando os dados do banco'", async () => {
  for (const estado of ["updated", "rate_limited"]) {
    const { preso, solta } = segura();
    const { ctx, page } = await telaRecon({ refresh: async (r) => { await preso; return r.fulfill({ json: refreshDe([item("item-11", estado)]) }); } }, SEM_VAGAS);
    await sincronizar(page).click();
    await page.waitForFunction(() => document.querySelector("#main p[role=status].sr-only")?.textContent === "Atualizando os dados do banco", null, { timeout: 8000 });
    solta();
    await fimDaRodada(page, 3);
    await page.waitForTimeout(200);
    const fim = await aviso(page).textContent();
    await ctx.close();
    assert.equal(fim, "", estado);
  }
});

test("VIVA a falha continua dita (texto próprio) e o Sincronizar não apaga o aviso de outra ação: 'Nubank desconectado.' fica durante e depois da rodada", async () => {
  const { preso, solta } = segura();
  const { ctx, page, srv } = await telaRecon({ refresh: async (r) => { await preso; return r.fulfill({ json: refreshDe([item("item-12", "updated", false, null, "ok", "Inter")]) }); } }, SEM_VAGAS);
  await maisDe(page, "Nubank").click();
  await page.getByRole("menuitem", { name: "Desconectar" }).click();
  await confirmar(page).click();
  await esperaCards(page, 2);
  await aviso(page).filter({ hasText: "Nubank desconectado." }).waitFor({ state: "attached" });
  await sincronizar(page, 0).click();
  await ate(page, () => srv.posts.length === 1, "o POST");
  await page.waitForFunction(() => document.querySelector("#main p[role=status].sr-only")?.textContent.includes("Atualizando os dados do banco"), null, { timeout: 8000 });
  const durante = await aviso(page).textContent();
  solta();
  await fimDaRodada(page, 2);
  await page.waitForTimeout(200);
  const depois = await aviso(page).textContent();
  await ctx.close();
  assert.equal(durante, "Nubank desconectado.Atualizando os dados do banco");
  assert.equal(depois, "Nubank desconectado.");
});

test("VIVA a falha do servidor segue anunciada na região viva depois da rodada (o esvaziamento é só para o que não tem texto próprio)", async () => {
  const { ctx, page } = await telaRecon({ refresh: (r) => r.fulfill({ status: 500, json: { error: { code: "internal_error", message: "x" } } }) }, SEM_VAGAS);
  await sincronizar(page).click();
  await fimDaRodada(page, 3);
  await page.waitForTimeout(300);
  const viva = await aviso(page).textContent();
  await ctx.close();
  assert.equal(viva, "Não conseguimos atualizar agora. Tente de novo.");
});
