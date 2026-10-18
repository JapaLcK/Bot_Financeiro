// Desconectar um banco no /painel (#/conta/bancos, conta/Bancos.tsx + Confirma.tsx + BancoCard.tsx). Plano: docs/plano-bancos-conectados-painel.md, W2.
// DELETE /open-finance/{uid}/connections/{id}; o isolamento por usuário da rota já é provado em tests/test_fase4_open_finance.py.
//   · um DELETE por vez, com o uid do clique e o x-csrf-token; duplo clique, outro banco e Esc/fundo em voo não mandam outro nem fecham;
//   · resposta perdida, 404, 5xx e prazo: o snapshot relido decide (sem o id = foi; com o id = falha com o motivo), nunca um 2º DELETE;
//   · 200 tira o banco, zera `/bancos` e `/limite` (o topo velho não fica na tela) e anuncia no leitor de tela; 401 = sessão; 403 = nada apagado.
// Antes de refocar/injetar erro cada teste espera as leituras iniciais (senão o React Query reaproveita a leitura em andamento).
import { test } from "node:test";
import assert from "node:assert/strict";
import { RESPOSTAS } from "./_painel.mjs";
import { abrir, navegador } from "./_guia.mjs";
import { ate, portao } from "./_conta_perfil.mjs";
import { JSON_ERRO, SESSAO } from "./_conta_seguranca.mjs";
import { con, servidorBancos } from "./_bancos.mjs";
import { DEL, semReticencias, esperaCards, refoco, dlg, confirmar, manter, card, botao, aviso, alerta, foco, noFoco, abreGerenciar, pedir, tiraDoSnapshot, lidosDepois, tela } from "./_bancos_desconectar.mjs";

const browser = navegador();
void browser;

test("feliz: o diálogo pergunta, o duplo clique manda 1 DELETE com o CSRF, o card some, o leitor de tela ouve e o foco vai ao título; as 3 leituras vêm DEPOIS", async () => {
  const { ctx, page, srv, erros } = await tela();
  await pedir(page, "Nubank");
  await noFoco(page, "Manter banco");
  const antes = { titulo: await dlg(page).getByRole("heading").innerText(), botoes: await dlg(page).getByRole("button").allInnerTexts() };
  await confirmar(page).dblclick();
  await esperaCards(page, 2);
  await aviso(page).filter({ hasText: "Nubank desconectado." }).waitFor({ state: "attached" });
  await noFoco(page, "page-title");
  await ate(page, () => ["/open-finance/7", "/open-finance/7/limite", "/api/v2/bancos"].every((x) => lidosDepois(srv).includes(x)), "as 3 leituras depois do DELETE");
  const r = { fechado: await dlg(page).count(), nomes: await page.locator(".banco-card h2").allInnerTexts() };
  await ctx.close();
  assert.deepEqual(antes, { titulo: "Desconectar Nubank?", botoes: ["Manter banco", "Desconectar"] });
  assert.deepEqual(srv.dels, [DEL(11)]);
  assert.deepEqual(r, { fechado: 0, nomes: ["Inter", "Itaú"] });
  assert.deepEqual(erros, []);
});

test("o último banco: 'Nenhum banco conectado.'", async () => {
  const { ctx, page, srv } = await tela({ conexoes: [con(11, "Nubank")] });
  await pedir(page, "Nubank");
  await confirmar(page).click();
  await page.getByText("Nenhum banco conectado.").waitFor();
  await ctx.close();
  assert.deepEqual(srv.dels, [DEL(11)]);
});

test("o topo não fica velho: com /bancos preso depois do 200 não há .bancos-topo nem o total antigo; solto, aparece o novo", async () => {
  const velho = RESPOSTAS.bancos.barra, novo = { ...velho, total: "22000.00", bancos: [velho.bancos[1]] };
  let apagou = false;
  const [segura, solta] = portao();
  const { ctx, page, srv } = await tela({ conexoes: [con(11, "Nubank"), con(12, "Inter")],
    bancosRes: async (r) => { if (!apagou) return r.fulfill({ json: velho }); await segura; return r.fulfill({ json: novo }); },
    del: (r, s) => { apagou = true; tiraDoSnapshot(s, 11); return r.fulfill({ json: { ok: true, deleted: 1 } }); } });
  assert.ok((await page.locator(".bancos-topo").innerText()).includes("67.000,00"));
  await pedir(page, "Nubank");
  await confirmar(page).click();
  await esperaCards(page, 1);
  await ate(page, () => lidosDepois(srv).includes("/api/v2/bancos"), "a leitura de /bancos pedida (e presa)");
  const preso = { topo: await page.locator(".bancos-topo").count(), velho: (await page.locator("body").innerText()).includes("67.000,00") };
  solta();
  await page.locator(".bancos-topo").waitFor();
  const depois = await page.locator(".bancos-total").innerText();
  await ctx.close();
  assert.deepEqual(preso, { topo: 0, velho: false });
  assert.equal(depois, "R$ 22.000,00");
});

const JA_HA = "Já há uma sincronização em andamento. Tente em instantes.";
for (const [nome, del, tira, esperado] of [
  ["(a) resposta perdida e o banco saiu: sucesso, sem alerta, 1 DELETE", (r) => r.abort(), true, "sucesso"],
  ["(b) resposta perdida e o banco continua: alerta, o card fica, o foco volta ao botão", (r) => r.abort(), false, "Não deu para desconectar Nubank. Tente de novo."],
  ["(c) 404 e o banco já não está no snapshot: sucesso", JSON_ERRO(404, "Conexão não encontrada."), true, "sucesso"],
  ["(d) 503 do lock e o banco continua: o alerta é o texto do servidor", JSON_ERRO(503, JA_HA), false, JA_HA],
]) {
  test(`resposta perdida/erro, o snapshot relido decide: ${nome}`, async () => {
    const { ctx, page, srv } = await tela({ del: async (r, s) => { if (tira) tiraDoSnapshot(s, 11); return del(r); } });
    await pedir(page, "Nubank");
    await confirmar(page).click();
    await dlg(page).waitFor({ state: "detached" });
    let r;
    if (esperado === "sucesso") {
      await esperaCards(page, 2);
      await aviso(page).filter({ hasText: "Nubank desconectado." }).waitFor({ state: "attached" });
      r = { alertas: await alerta(page).count(), foco: await foco(page) };
      assert.deepEqual(r, { alertas: 0, foco: "page-title" });
    } else {
      await alerta(page).waitFor();
      await noFoco(page, "Desconectar Nubank");
      r = { texto: await alerta(page).innerText(), cards: await page.locator(".banco-card").count(), aviso: await aviso(page).innerText() };
      assert.deepEqual(r, { texto: esperado, cards: 3, aviso: "" });
    }
    await ctx.close();
    assert.deepEqual(srv.dels, [DEL(11)]); // nunca uma retentativa
  });
}

test("401 no DELETE: 'Sua sessão terminou.' com Recarregar, nenhum card, nenhuma leitura de /open-finance/7 depois, 1 DELETE", async () => {
  const { ctx, page, srv } = await tela({ del: JSON_ERRO(401, "Não autenticado.", SESSAO) });
  await pedir(page, "Nubank");
  await confirmar(page).click();
  await page.getByText("Sua sessão terminou.").waitFor();
  await page.waitForTimeout(500); // uma releitura indevida já teria saído
  const r = { cards: await page.locator(".banco-card").count(), recarregar: await page.getByRole("button", { name: "Recarregar" }).count(), foco: await foco(page), depois: lidosDepois(srv).filter((x) => x === "/open-finance/7") };
  await ctx.close();
  assert.deepEqual(r, { cards: 0, recarregar: 1, foco: "page-title", depois: [] });
  assert.deepEqual(srv.dels, [DEL(11)]);
});

test("403 com a conta trocada em outra aba: nada é dado como desconectado, o alerta do 403 aparece, o perfil é relido e a conta nova (C6 Bank) surge sem refoco", async () => {
  let uid = 7;
  const nova = { ...RESPOSTAS.bancos.barra, total: "300.00", bancos: [{ ...RESPOSTAS.bancos.barra.bancos[0], conexao_id: 21, total: "300.00" }] };
  const srv = servidorBancos({ bancos: "padrao", perfil: () => ({ display_name: "Ana", email: "ana@exemplo.com", user_id: uid, whatsapp_linked: false }),
    bancosRes: (r) => r.fulfill({ json: uid === 7 ? RESPOSTAS.bancos.padrao : nova }),
    del: (r) => { uid = 8; return r.fulfill({ status: 403, json: { detail: "Acesso negado para este usuário." } }); } });
  const p = await abrir({ guia: "concluido", rota: "/conta/bancos", antes: async (ctx) => {
    await srv.antes(ctx);
    await ctx.route((u) => u.pathname === "/open-finance/8", (r) => r.fulfill({ json: { ok: true, connections: [con(21, "C6 Bank")] } }));
    await ctx.route((u) => u.pathname === "/open-finance/8/limite", (r) => r.fulfill({ json: { ok: true, of_banks_max: 3, em_uso: 1, pode_adicionar: true, code: null, message: null } }));
  } });
  const { ctx, page } = p;
  page.setDefaultTimeout(15000);
  await esperaCards(page, 3);
  await semReticencias(page);
  await page.getByText("2 de 3 bancos").waitFor();
  await pedir(page, "Nubank");
  await confirmar(page).click();
  await card(page, "C6 Bank").waitFor();
  await alerta(page).filter({ hasText: "Recarregue a página" }).waitFor();
  const corpo = await page.locator("body").innerText();
  const aviso403 = await aviso(page).innerText();
  await ctx.close();
  assert.ok(!/Nubank|Inter|Itaú/.test(corpo), corpo);
  assert.equal(aviso403, "");
  assert.deepEqual(lidosDepois(srv).filter((x) => x === "/open-finance/7"), []);
  assert.deepEqual(srv.dels, [DEL(11)]);
});

test("uma remoção por vez: com o DELETE do Nubank preso o 'Desconectar Inter' está desabilitado e o clique forçado não troca o diálogo; solto, 1 DELETE só", async () => {
  const [segura, solta] = portao();
  const { ctx, page, srv } = await tela({ del: async (r, s) => { await segura; tiraDoSnapshot(s, 11); return r.fulfill({ json: { ok: true, deleted: 1 } }); } });
  await abreGerenciar(page, "Inter");
  await pedir(page, "Nubank");
  await confirmar(page).click();
  await confirmar(page, "Desconectando…").waitFor();
  const desabilitado = await botao(page, "Inter").isDisabled();
  await botao(page, "Inter").dispatchEvent("click");
  await page.waitForTimeout(150);
  const titulo = await dlg(page).getByRole("heading").innerText();
  solta();
  await dlg(page).waitFor({ state: "detached" });
  await ctx.close();
  assert.deepEqual([desabilitado, titulo], [true, "Desconectar Nubank?"]);
  assert.deepEqual(srv.dels, [DEL(11)]);
});

for (const semDialog of [false, true]) {
  test(`${semDialog ? "sem <dialog>" : "nativo"}: Esc e 'Manter banco' fecham sem DELETE e devolvem o foco ao botão; em voo, Esc (6×) e o fundo não fecham; solto, fecha`, async () => {
    const [segura, solta] = portao();
    const { ctx, page, srv } = await tela({ del: async (r, s) => { await segura; tiraDoSnapshot(s, 11); return r.fulfill({ json: { ok: true, deleted: 1 } }); } }, { semDialog });
    await pedir(page, "Nubank");
    await page.waitForTimeout(500); // a janela do clique no fundo
    await page.keyboard.press("Escape");
    await dlg(page).waitFor({ state: "detached" });
    await noFoco(page, "Desconectar Nubank");
    await botao(page, "Nubank").click();
    await manter(page).click();
    await dlg(page).waitFor({ state: "detached" });
    await noFoco(page, "Desconectar Nubank");
    const semEnvio = srv.dels.length;
    await botao(page, "Nubank").click();
    await page.waitForTimeout(500);
    await confirmar(page).click();
    await confirmar(page, "Desconectando…").waitFor();
    for (let i = 0; i < 6; i++) { await page.keyboard.press("Escape"); await page.waitForTimeout(40); }
    await page.mouse.click(3, 3);
    const emVoo = [await dlg(page).count(), await manter(page).isDisabled()];
    solta();
    await dlg(page).waitFor({ state: "detached" });
    await ctx.close();
    assert.deepEqual([semEnvio, emVoo], [0, [1, true]]);
    assert.deepEqual(srv.dels, [DEL(11)]);
  });
}

test("o card some durante o voo (outra aba tirou o banco): o diálogo segue 'Desconectando…'; a resposta fecha, anuncia e leva o foco ao título", async () => {
  const [segura, solta] = portao();
  const { ctx, page, srv } = await tela({ del: async (r) => { await segura; return r.fulfill({ json: { ok: true, deleted: 1 } }); } });
  await pedir(page, "Nubank");
  await confirmar(page).click();
  await confirmar(page, "Desconectando…").waitFor();
  tiraDoSnapshot(srv, 11);
  await refoco(page);
  await esperaCards(page, 2);
  const emVoo = [await dlg(page).count(), await confirmar(page, "Desconectando…").count()];
  solta();
  await dlg(page).waitFor({ state: "detached" });
  await aviso(page).filter({ hasText: "Nubank desconectado." }).waitFor({ state: "attached" });
  await noFoco(page, "page-title");
  await ctx.close();
  assert.deepEqual(emVoo, [1, 1]);
});

test("a falha de um card some quando o card some (outra aba tirou o banco)", async () => {
  const { ctx, page, srv } = await tela({ del: (r) => r.abort() });
  await pedir(page, "Nubank");
  await confirmar(page).click();
  await alerta(page).waitFor();
  tiraDoSnapshot(srv, 11);
  await refoco(page);
  await esperaCards(page, 2);
  await alerta(page).waitFor({ state: "detached" });
  await ctx.close();
});

test("layout: summary e botões a 44 px, sem rolagem lateral e diálogo dentro da janela em 1280×800, 390×844 e 320×640, com nome de 60 letras SEM espaço", async () => {
  const longo = "A".repeat(60); // sem espaço: só o overflow-wrap/word-break do botão, do título e do texto do diálogo o contém
  for (const [width, height] of [[1280, 800], [390, 844], [320, 640]]) {
    const { ctx, page } = await tela({ conexoes: [con(11, longo), con(12, "Inter")] }, { width, height });
    await abreGerenciar(page, longo);
    const medida = () => page.evaluate(() => {
      const alt = (e) => Math.round(e.getBoundingClientRect().height);
      const dl = document.querySelector("dialog[open]"), d = dl?.getBoundingClientRect(), cartao = document.querySelector(".banco-card").getBoundingClientRect();
      return { cortado: dl ? dl.scrollWidth - dl.clientWidth : 0, noCartao: document.querySelector(".banco-acoes .btn").getBoundingClientRect().right <= cartao.right, lateral: document.documentElement.scrollWidth - innerWidth, summary: alt(document.querySelector(".banco-acoes summary")), botao: alt(document.querySelector(".banco-acoes .btn")),
        dialogo: d ? d.left >= 0 && d.right <= innerWidth && d.top >= 0 && d.bottom <= innerHeight : null, botoes: [...document.querySelectorAll("dialog[open] .btn")].map(alt) };
    });
    const a = await medida();
    await botao(page, longo).click();
    await dlg(page).waitFor();
    const b = await medida();
    await ctx.close();
    assert.equal(a.lateral, 0, `${width}: rolagem lateral`);
    assert.ok(a.noCartao && a.summary >= 44 && a.botao >= 44, `${width}: ${JSON.stringify(a)}`);
    assert.equal(b.lateral, 0, `${width}: rolagem lateral com o diálogo`);
    assert.ok(b.cortado === 0 && b.dialogo && b.botoes.length === 2 && b.botoes.every((h) => h >= 44), `${width}: ${JSON.stringify(b)}`);
  }
});
