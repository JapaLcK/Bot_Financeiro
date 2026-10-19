// Reconectar um banco no /painel (#/conta/bancos: conta/Bancos.tsx + BancoDetalhe.tsx + reconectar.ts). Plano: docs/plano-bancos-conectados-painel.md, W3.
// O legado (/open-finance-connect.js) é o REAL; o SDK da Pluggy é falso (_bancos_reconectar.mjs). O isolamento por usuário do connect-token
// e o teto já são provados em tests/test_of_connect_token_reconectar.py e tests/test_of_limite_route.py::test_b_p1_reconexao_passa_no_teto.
//   · R1 quando o botão aparece (estado, provider_item_id, vaga); R2 o caminho feliz e o widget ACIMA do detalhe; R3 uma conexão por vez; R4 fechar sem concluir.
// Os erros e as bordas estão em dashboard_v2_bancos_reconectar_bordas.test.mjs.
//
// Controles negativos (desligue, rebuild `npm --prefix webapp run build`, o grupo tem de ficar vermelho; restaure e rebuild):
//  - R1: em conta/BancoCard.tsx tirar "paused" de `SEM_RECONEXAO` (o Itaú pausado ganha o botão); ou, em Bancos.tsx `reconectavel`, trocar `lim?.of_banks_max !== 0`
//    por `lim?.pode_adicionar !== false && lim?.of_banks_max !== 0` (some com a conta cheia). Positivos do grupo: os estados que o têm e a conta cheia.
//  - R2: em Bancos.tsx `reconectarBanco`, tirar a linha `fecharDet();` (o detalhe modal fica na top layer e COBRE o widget: o elementFromPoint cai no <dialog>).
//    Positivo: o mesmo teste com o detalhe fechado vê o widget (e o R4 volta o foco à linha).
//  - R2 (foco): em conta/reconectar.ts, `onConnected: () => terminar({ ok: true }, true)` por `terminar({ ok: true })` (o foco vai à linha com o widget por cima); ou tirar `else if (pendente.current !== null) {...}` do `onClose` em Bancos.tsx (o foco fica no <body> depois que o widget sai).
//  - R1 (classe): em BancoDetalhe.tsx, trocar `c.ui.state === "needs_user_action" ? "btn-primary" : "btn-ghost"` por `"btn-ghost"`.
//  - R3: em Bancos.tsx `reconectarBanco`, tirar `|| emCurso.current` do `if` (dois cliques no mesmo tick pedem dois connect-tokens... o legado barra o 2º, mas o
//    `ocupado` apareceria como alerta); tirar `dialogo.current ||` (Desconectar e Reconectar no mesmo tick abrem o diálogo E o widget).
//    Positivo: depois de "Manter banco" o Reconectar abre.
import { test } from "node:test";
import assert from "node:assert/strict";
import { navegador } from "./_guia.mjs";
import { ate, portao } from "./_conta_perfil.mjs";
import { con, LIMITE } from "./_bancos.mjs";
import { aviso, alerta, dlg, det, manter, linha, esperaCards, semReticencias } from "./_bancos_desconectar.mjs";
import { naLinha, telaRecon, botaoRecon, reconectar, widgetAberto, widgetsAbertos, sucesso, fechaWidget, tokens, saves, depoisDo, comReconectar } from "./_bancos_reconectar.mjs";

const browser = navegador();
void browser;

const SEM_ID = { ...con(19, "Sem id", "updated", "Atualizado"), provider_item_id: null };
const MUITOS = () => [con(11, "Nubank"), con(12, "Inter", "updating", "Atualizando…"), con(13, "Itaú", "paused", "Pausado"), con(14, "C6 Bank", "needs_user_action", "Ação necessária"),
  con(15, "XP", "error_recoverable", "Erro temporário"), con(16, "Neon", "item_missing", "Sumiu"), con(17, "Bradesco", "removed", "Removido"), SEM_ID];
const NOMES = ["Nubank", "Inter", "Itaú", "C6 Bank", "XP", "Neon", "Bradesco", "Sem id"];

test("R1 o botão aparece em updated/updating/needs_user_action/error_recoverable; some em paused, item_missing, removed e sem provider_item_id", async () => {
  const { ctx, page } = await telaRecon({ conexoes: MUITOS() }, { vagas: "" });
  const r = await comReconectar(page, NOMES);
  await ctx.close();
  assert.deepEqual(r, ["Nubank", "Inter", "C6 Bank", "XP"]);
});

for (const [nome, limite, vagas, esperado] of [
  ["of_banks_max 0 (plano sem Open Finance): nenhum botão", LIMITE({ of_banks_max: 0, em_uso: 0, pode_adicionar: false }), "", []],
  ["conta cheia {pode_adicionar:false, em_uso:3, of_banks_max:3}: reconectar não gasta vaga, o botão APARECE", LIMITE({ pode_adicionar: false, em_uso: 3, message: "Limite" }), "3 de 3 bancos", ["Nubank", "Inter"]],
]) {
  test(`R1 vaga: ${nome}`, async () => {
    const { ctx, page } = await telaRecon({ limite }, { vagas });
    const r = await comReconectar(page, ["Nubank", "Inter"]);
    await ctx.close();
    assert.deepEqual(r, esperado);
  });
}

test("R1 o módulo do seletor não carregou: sem botão (não há o que abrir)", async () => {
  const { ctx, page } = await telaRecon({ semSeletor: true, seletorReal: false });
  const r = await comReconectar(page, ["Nubank"]);
  await ctx.close();
  assert.deepEqual(r, []);
});

for (const [width, height] of [[1280, 800], [390, 844]]) {
  test(`R2 feliz ${width}x${height}: o detalhe fecha ANTES do widget, que fica ACIMA de tudo; connect-token com item_id e CSRF; updateItem; o save anuncia e relê`, async () => {
    const { ctx, page, srv, erros } = await telaRecon({}, { width, height });
    await reconectar(page, "Nubank");
    await widgetAberto(page);
    const r = await page.evaluate(() => {
      const w = document.getElementById("pluggy-falso"), t = document.elementFromPoint(innerWidth / 2, innerHeight / 2);
      return { acima: !!t && (t === w || w.contains(t)), dialogos: document.querySelectorAll("dialog[open]").length, widget: { updateItem: window.__w.updateItem, selecionado: window.__w.selectedConnectorId, token: window.__w.connectToken } };
    });
    await sucesso(page);
    await ate(page, () => saves(srv).length === 1, "o POST do /pluggy-item");
    await aviso(page).filter({ hasText: "Nubank reconectado." }).waitFor({ state: "attached" }); // sem onClose: o SDK real pode nunca mandá-lo
    const comWidget = await naLinha(page, "Nubank"); // o widget segue por cima: o foco NÃO é roubado para a linha atrás dele
    await ate(page, () => ["/open-finance/7", "/open-finance/7/limite", "/api/v2/bancos"].every((x) => depoisDo(srv, "/open-finance/7/pluggy-item").includes(x)), "as leituras depois do save");
    await fechaWidget(page);
    await page.waitForTimeout(300);
    const fim = { alertas: await alerta(page).count(), aviso: await aviso(page).innerText(), naLinha: await naLinha(page, "Nubank") }; // o widget saiu: o foco volta à linha, não fica no <body>
    await ctx.close();
    assert.deepEqual(r, { acima: true, dialogos: 0, widget: { updateItem: "item-11", selecionado: undefined, token: "acesso-xyz" } });
    assert.deepEqual(srv.conex, [["POST", "/open-finance/7/connect-token", "tok-123", '{"item_id":"item-11"}'], ["POST", "/open-finance/7/pluggy-item", "tok-123", '{"item":{"id":"item-11"}}']]);
    assert.equal(comWidget, false);
    assert.deepEqual(fim, { alertas: 0, aviso: "Nubank reconectado.", naLinha: true });
    assert.deepEqual(erros, []);
  });
}

test("R3 com o connect-token pendente: a linha não abre outro detalhe, o '+' não abre o seletor, dois cliques no mesmo tick mandam 1 POST e o widget aberto sobrevive ao prazo", async () => {
  const [segura, solta] = portao();
  const { ctx, page, srv } = await telaRecon({ tokenRes: async (r) => { await segura; return r.fulfill({ json: { accessToken: "acesso-xyz" } }); } });
  await page.evaluate(() => { const st = window.setTimeout; window.setTimeout = (f, d, ...a) => st(f, d === 30000 ? 2500 : d, ...a); });
  await linha(page, "Nubank").click();
  await det(page).waitFor();
  await page.evaluate(() => { const b = [...document.querySelectorAll("dialog.banco-det button")].find((x) => x.textContent === "Reconectar banco"); b.click(); b.click(); });
  await ate(page, () => tokens(srv).length > 0, "o connect-token");
  await linha(page, "Inter").click();
  await page.getByRole("link", { name: "Conectar banco", exact: true }).click();
  await page.waitForTimeout(400);
  const r = { detalhes: await det(page).count(), seletor: await page.evaluate(() => !!document.querySelector("#bankpick-overlay.open")), posts: tokens(srv).length };
  solta();
  await widgetAberto(page);
  await page.waitForTimeout(2700); // passa do prazo encurtado: um 2º prazo (sem a guarda) derrubaria o widget já aberto
  const depois = { alertas: await alerta(page).count(), widgets: await widgetsAbertos(page) };
  await ctx.close();
  assert.deepEqual(r, { detalhes: 0, seletor: false, posts: 1 });
  assert.deepEqual(depois, { alertas: 0, widgets: 1 });
});

test("R3 Desconectar e Reconectar no MESMO tick: só o diálogo anda; com ele aberto o Reconectar está travado e, depois de 'Manter banco', abre", async () => {
  const { ctx, page, srv } = await telaRecon();
  await linha(page, "Nubank").click();
  await det(page).waitFor();
  await page.evaluate(() => {
    const bs = [...document.querySelectorAll("dialog.banco-det button")];
    bs.find((b) => b.textContent === "Desconectar banco").click();
    bs.find((b) => b.textContent === "Reconectar banco").click();
  });
  await dlg(page).waitFor();
  await page.waitForTimeout(500);
  const r = { dialogo: await dlg(page).count(), detalhe: await det(page).count(), tokens: tokens(srv).length, travado: await botaoRecon(page).isDisabled() };
  await manter(page).click();
  await page.waitForFunction(() => !document.querySelector("dialog[open].lanc-form"));
  await botaoRecon(page).click();
  await widgetAberto(page);
  await ctx.close();
  assert.deepEqual(r, { dialogo: 1, detalhe: 1, tokens: 0, travado: true });
});

test("R4 fechar o widget sem concluir: sem aviso nem alerta, o foco volta à linha do banco, tudo destravado e a lista relida", async () => {
  const { ctx, page, srv } = await telaRecon();
  await reconectar(page, "Nubank");
  await widgetAberto(page);
  await fechaWidget(page);
  await ate(page, () => depoisDo(srv, "/open-finance/7/connect-token").includes("/open-finance/7"), "a releitura do snapshot");
  await esperaCards(page, 3);
  await semReticencias(page);
  const r = { naLinha: await linha(page, "Nubank").evaluate((el) => el === document.activeElement), aviso: await aviso(page).innerText(), alertas: await alerta(page).count(), saves: saves(srv).length };
  await linha(page, "Nubank").click(); // destravado: o detalhe abre e o Reconectar também
  await det(page).waitFor();
  const livre = await botaoRecon(page).isEnabled();
  await ctx.close();
  assert.deepEqual(r, { naLinha: true, aviso: "", alertas: 0, saves: 0 });
  assert.ok(livre);
});

test("R1 estilo: 'Reconectar banco' é o botão principal só em needs_user_action (como o app); nos outros estados é o secundário", async () => {
  const { ctx, page } = await telaRecon({ conexoes: [con(11, "Nubank"), con(14, "C6 Bank", "needs_user_action", "Ação necessária"), con(15, "XP", "error_recoverable", "Erro temporário")] }, { vagas: "" });
  const r = {};
  for (const n of ["Nubank", "C6 Bank", "XP"]) {
    await linha(page, n).click();
    await det(page).waitFor();
    r[n] = await botaoRecon(page).evaluate((b) => (b.classList.contains("btn-primary") ? "principal" : b.classList.contains("btn-ghost") ? "secundario" : "?"));
    await det(page).getByRole("button", { name: "Fechar" }).click();
    await det(page).waitFor({ state: "detached" });
  }
  await ctx.close();
  assert.deepEqual(r, { Nubank: "secundario", "C6 Bank": "principal", XP: "secundario" });
});
