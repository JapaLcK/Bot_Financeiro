/**
 * /painel#/conta/configuracoes (conta/Sessoes.tsx, conta/api.ts): "Dispositivos conectados", as sessões ativas dentro da Segurança.
 *   · a lista: aparelho, selo "Esta sessão" SEM botão (por is_current e por current_jti), IP ("IP desconhecido" sem ele), "ativa há X";
 *   · encerrar uma: confirmação, DELETE com o jti codificado e o CSRF, anúncio, a linha some; "Encerrar todas as outras" idem, e some sem outras;
 *   · um GET que já estava em voo NÃO repinta a sessão revogada (cancelQueries); 404 e 400 saem verbatim; erro de carga e 401 de sessão;
 *   · toda URL usa o uid do dashboard-profile; layout em 1440, 390 e 320; `horaRelativa` igual à `formatActivityTime` do settings.html (§0.7).
 * O voo (duplo clique, Esc, prazo) mora em dashboard_v2_conta_sessoes_voo.test.mjs.
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { navegador } from "./_guia.mjs";
import { RAIZ } from "./_painel.mjs";
import { AGORA, DOIS, JSON_ERRO, SESSAO, abrirSeguranca, ha, listaPadrao, portao } from "./_conta_seguranca.mjs";

const browser = navegador();
void browser;
const cartao = (p, nome) => p.sess.locator("li").filter({ hasText: nome });
const encerrar = (p, nome) => cartao(p, nome).getByRole("button", { name: "Encerrar", exact: true });
// A aba volta ao foco: o React Query relê o que está "vivo" (refetchOnWindowFocus).
const volta = (p) => p.page.evaluate(() => document.dispatchEvent(new Event("visibilitychange", { bubbles: true })));
const confirmar = (p, nome = "Encerrar") => p.dialogo.getByRole("button", { name: nome, exact: true });

test("a lista: aparelho, ícone, 'Esta sessão' sem botão, IP desconhecido, 'ativa há X'; as outras têm 'Encerrar'", async () => {
  const p = await abrirSeguranca();
  await p.sess.locator("li").first().waitFor();
  const linhas = await p.linhas();
  const titulo = await p.sess.locator("h2").innerText();
  const todas = await p.sess.getByRole("button", { name: "Encerrar todas as outras", exact: true }).count();
  const [selos, nomeados] = [await p.sess.locator(".selo").allInnerTexts(), // o botão é descrito pelo nome do aparelho
    await p.sess.locator("li button[aria-describedby]").evaluateAll((bs) => bs.map((b) => document.getElementById(b.getAttribute("aria-describedby"))?.textContent))];
  await p.ctx.close();
  assert.deepEqual([selos, nomeados], [["Esta sessão"], ["Safari • iOS", "Edge • Windows"]]);
  assert.equal(titulo, "Dispositivos conectados");
  assert.deepEqual(linhas, [
    { nome: "Chrome • macOS", selo: true, meta: "203.0.113.5 · ativa há 30s", icone: "ph ph-desktop", botao: false },
    { nome: "Safari • iOS", selo: false, meta: "198.51.100.7 · ativa há 5 min", icone: "ph ph-device-mobile", botao: true },
    { nome: "Edge • Windows", selo: false, meta: "IP desconhecido · ativa há 3h", icone: "ph ph-desktop", botao: true },
  ]);
  assert.equal(todas, 1);
});

test("a sessão atual sai por current_jti mesmo sem is_current (e vice-versa): nunca tem botão", async () => {
  for (const [rotulo, muda] of [["só current_jti", (l) => { l[0].is_current = false; }], ["só is_current", null]]) {
    const sessoes = listaPadrao();
    muda?.(sessoes);
    const p = await abrirSeguranca({ sessoes });
    if (!muda) p.s.atual = "outro-jti";
    await p.page.reload();
    await p.sess.locator("li").first().waitFor();
    const linhas = await p.linhas();
    await p.ctx.close();
    assert.deepEqual(linhas.map((l) => [l.selo, l.botao]), [[true, false], [false, true], [false, true]], rotulo);
  }
});

test("encerrar uma: Cancelar não manda nada; Encerrar manda UM DELETE com o jti e o CSRF, anuncia e a linha some", async () => {
  const p = await abrirSeguranca();
  await encerrar(p, "Safari").click();
  const abre = [await p.dialogo.locator("h2").innerText(), await p.dialogo.locator("p").innerText()];
  await p.dialogo.getByRole("button", { name: "Cancelar" }).click(); await p.fechado();
  const semDelete = p.s.dels.length;
  await encerrar(p, "Safari").click();
  await confirmar(p).click();
  await p.fechado();
  await p.page.waitForFunction(() => document.querySelectorAll(".conta-sessoes li").length === 2);
  const [aviso, linhas, foco] = [await p.avisoSess.innerText(), await p.linhas(), await p.page.evaluate(() => document.activeElement?.textContent)];
  await p.ctx.close();
  assert.deepEqual(abre, ["Encerrar esta sessão?", "O dispositivo será deslogado imediatamente."]);
  assert.equal(semDelete, 0);
  assert.deepEqual(p.s.dels.map((d) => [d.metodo, new URL(d.url).pathname, d.csrf, d.corpo]), [["DELETE", `/settings/7/sessions/${DOIS}`, "tok-123", null]]);
  assert.equal(p.s.dels[0].jti, DOIS);
  assert.equal(aviso, "Sessão encerrada");
  assert.deepEqual(linhas.map((l) => l.nome), ["Chrome • macOS", "Edge • Windows"]);
  assert.equal(foco, "Dispositivos conectados", "a linha sumiu: o foco vai ao título, não ao <body>");
});

test("um GET em voo DURANTE o DELETE não repinta a sessão revogada (o invalidate o cancela)", async () => {
  const p = await abrirSeguranca();
  await p.sess.locator("li").first().waitFor();
  const [segura, solta] = portao();
  p.s.sessHold = segura; // o próximo GET leva a foto de ANTES do DELETE e só responde depois
  await volta(p); // refetchOnWindowFocus: o GET velho sai
  await p.page.waitForTimeout(300);
  assert.equal(p.s.sessGets >= 2, true);
  await encerrar(p, "Safari").click();
  await confirmar(p).click();
  await p.fechado();
  p.s.sessHold = null;
  solta(); // a foto velha chega agora, com a sessão revogada dentro
  await p.page.waitForTimeout(600);
  const linhas = (await p.linhas()).map((l) => l.nome);
  await p.ctx.close();
  assert.deepEqual(linhas, ["Chrome • macOS", "Edge • Windows"]);
});

test("a(s) linha(s) revogada(s) somem na hora, sem esperar a releitura da lista (que fica segurada)", async () => {
  for (const [rotulo, quais, restam] of [["uma", "Safari", ["Chrome • macOS", "Edge • Windows"]], ["as outras", null, ["Chrome • macOS"]]]) {
    const p = await abrirSeguranca();
    await p.sess.locator("li").first().waitFor();
    const [segura, solta] = portao();
    p.s.sessHold = segura; // a releitura depois do DELETE não responde
    if (quais) { await encerrar(p, quais).click(); await confirmar(p).click(); }
    else { await p.sess.getByRole("button", { name: "Encerrar todas as outras", exact: true }).click(); await confirmar(p, "Encerrar outras").click(); }
    await p.fechado();
    await p.page.waitForFunction((n) => document.querySelectorAll(".conta-sessoes li").length === n, restam.length);
    const nomes = (await p.linhas()).map((l) => l.nome);
    solta();
    await p.ctx.close();
    assert.deepEqual(nomes, restam, rotulo);
  }
});

test("404 (outra aba já encerrou): o detalhe do servidor, verbatim, e a lista é relida sem a sessão", async () => {
  const p = await abrirSeguranca();
  await p.sess.locator("li").first().waitFor();
  p.s.sessoes = p.s.sessoes.filter((x) => x.jti !== DOIS); // sumiu por trás
  await encerrar(p, "Safari").click();
  await confirmar(p).click();
  await p.fechado();
  await p.alertaSess.waitFor();
  await p.page.waitForFunction(() => document.querySelectorAll(".conta-sessoes li").length === 2);
  const [alerta, aviso] = [await p.alertaSess.innerText(), await p.avisoSess.innerText()];
  await p.ctx.close();
  assert.equal(alerta, "Sessão não encontrada ou já encerrada.");
  assert.equal(aviso, "");
  assert.equal(p.s.dels.length, 1);
});

test("400 da atual (rede de segurança: o servidor recusa) sai verbatim", async () => {
  const p = await abrirSeguranca();
  await p.sess.locator("li").first().waitFor();
  p.s.sessDel = JSON_ERRO(400, "Use o botão 'Sair' para encerrar a sessão atual.");
  await encerrar(p, "Safari").click();
  await confirmar(p).click();
  await p.fechado();
  await p.alertaSess.waitFor();
  const texto = await p.alertaSess.innerText();
  await p.ctx.close();
  assert.equal(texto, "Use o botão 'Sair' para encerrar a sessão atual.");
});

test("500 sem detalhe: a reserva 'Erro ao encerrar sessão'; um novo pedido limpa o alerta", async () => {
  const p = await abrirSeguranca();
  await p.sess.locator("li").first().waitFor();
  p.s.sessDel = (r) => r.fulfill({ status: 500, body: "x" });
  await encerrar(p, "Safari").click();
  await confirmar(p).click();
  await p.fechado();
  await p.alertaSess.waitFor();
  const texto = await p.alertaSess.innerText();
  p.s.sessDel = null;
  await encerrar(p, "Safari").click();
  const limpo = await p.alertaSess.count(); // abrir o diálogo já tirou o alerta antigo
  await confirmar(p).click();
  await p.fechado();
  await p.page.waitForFunction(() => document.querySelectorAll(".conta-sessoes li").length === 2);
  await p.ctx.close();
  assert.equal(texto, "Erro ao encerrar sessão");
  assert.equal(limpo, 0);
});

test("'Encerrar todas as outras': confirmação, DELETE sem corpo, anúncio com o revoked, e o botão some sem outras", async () => {
  const p = await abrirSeguranca();
  await p.sess.locator("li").first().waitFor();
  await p.sess.getByRole("button", { name: "Encerrar todas as outras", exact: true }).click();
  const abre = [await p.dialogo.locator("h2").innerText(), await p.dialogo.locator("p").innerText()];
  await p.dialogo.getByRole("button", { name: "Cancelar" }).click(); await p.fechado();
  const semDelete = p.s.dels.length;
  await p.sess.getByRole("button", { name: "Encerrar todas as outras", exact: true }).click();
  await p.dialogo.getByRole("button", { name: "Encerrar outras", exact: true }).click();
  await p.fechado();
  await p.page.waitForFunction(() => document.querySelectorAll(".conta-sessoes li").length === 1);
  const [aviso, linhas, botao] = [await p.avisoSess.innerText(), await p.linhas(), await p.sess.getByRole("button", { name: "Encerrar todas as outras" }).count()];
  await p.ctx.close();
  assert.deepEqual(abre, ["Encerrar todas as outras sessões?", "Todos os outros dispositivos serão deslogados imediatamente. Esta sessão atual continua ativa."]);
  assert.equal(semDelete, 0);
  assert.deepEqual(p.s.dels.map((d) => [d.metodo, new URL(d.url).pathname, d.csrf, d.corpo]), [["DELETE", "/settings/7/sessions", "tok-123", null]]);
  assert.equal(aviso, "2 sessão(ões) encerrada(s)");
  assert.deepEqual(linhas.map((l) => l.nome), ["Chrome • macOS"]);
  assert.equal(botao, 0);
});

test("só a sessão atual: sem 'Encerrar todas as outras'; lista vazia: 'Nenhuma sessão ativa.'", async () => {
  const sozinha = await abrirSeguranca({ sessoes: [listaPadrao()[0]] });
  await sozinha.sess.locator("li").first().waitFor();
  const botoes = await sozinha.sess.getByRole("button").count();
  await sozinha.ctx.close();
  assert.equal(botoes, 0);
  const vazia = await abrirSeguranca({ sessoes: [] });
  await vazia.sess.getByText("Nenhuma sessão ativa.").waitFor();
  const itens = await vazia.sess.locator("li").count();
  await vazia.ctx.close();
  assert.equal(itens, 0);
});

test("erro de carga: 'Não foi possível carregar as sessões.' + 'Tentar de novo', nunca a lista velha; 401 de sessão: 'Sua sessão terminou.' + Recarregar", async () => {
  const p = await abrirSeguranca();
  await p.sess.locator("li").first().waitFor();
  p.s.sessGetErro = (r) => r.fulfill({ status: 404, json: { detail: "x" } });
  await volta(p);
  await p.alertaSess.waitFor();
  const erro = [(await p.alertaSess.innerText()).replace(/\s+/g, " "), await p.sess.locator("li").count()];
  p.s.sessGetErro = null;
  await p.sess.getByRole("button", { name: "Tentar de novo" }).click();
  await p.sess.locator("li").first().waitFor();
  p.s.sessGetErro = JSON_ERRO(401, "Não autenticado.", SESSAO);
  await volta(p);
  await p.alertaSess.waitFor();
  const sessao = [(await p.alertaSess.innerText()).replace(/\s+/g, " "), await p.sess.locator("li").count(), await p.sess.getByRole("button", { name: "Recarregar" }).count()];
  await p.ctx.close();
  assert.deepEqual(erro, ["Não foi possível carregar as sessões. Tentar de novo", 0]);
  assert.deepEqual(sessao, ["Sua sessão terminou. Recarregar", 0, 1]);
});

test("401 de sessão no DELETE: 'Sua sessão terminou.' + Recarregar, sem confundir com o detalhe", async () => {
  const p = await abrirSeguranca();
  await p.sess.locator("li").first().waitFor();
  p.s.sessDel = JSON_ERRO(401, "Não autenticado.", SESSAO);
  await encerrar(p, "Safari").click();
  await confirmar(p).click();
  await p.fechado();
  await p.alertaSess.waitFor();
  const texto = await p.alertaSess.innerText();
  await p.ctx.close();
  assert.equal(texto.replace(/\s+/g, " ").startsWith("Sua sessão terminou."), true, texto);
});

test("toda URL de /settings usa o uid do dashboard-profile (7); com uid inválido ou ausente a seção nem monta", async () => {
  const p = await abrirSeguranca();
  await p.sess.locator("li").first().waitFor();
  await encerrar(p, "Safari").click();
  await confirmar(p).click();
  await p.fechado();
  await p.page.waitForTimeout(300);
  const urls = p.s.reqs.map((q) => new URL(q.url).pathname).filter((u) => u.startsWith("/settings/"));
  await p.ctx.close();
  assert.ok(urls.some((u) => u.includes("/sessions")), urls.join());
  assert.deepEqual(urls.filter((u) => !u.startsWith("/settings/7/")), []);
  for (const opts of [{ semUid: true }, { uid: 0 }, { uid: -3 }, { uid: 1.5 }]) {
    const q = await abrirSeguranca(opts);
    await q.page.waitForTimeout(400);
    const [secao, ruins] = [await q.sess.count(), q.s.reqs.filter((r) => /\/settings\/(undefined|0|null|-3|1\.5)\//.test(r.url))];
    await q.ctx.close();
    assert.deepEqual([secao, ruins.length], [0, 0], JSON.stringify(opts));
  }
});

// §0.7: a cópia de formatActivityTime (conta/api.ts: horaRelativa) tem de dar o que o settings.html dá. O relógio da página é fixo em AGORA.
// as bordas de cada faixa (59/60 s, 59 min/1 h, 23/24 h, 29/30 d), uma data antiga e uma no futuro (a original dá 'há 1s')
const IDADES = [1e3, 30e3, 59e3, 60e3, 61e3, 330e3, 3599e3, 3600e3, 3.5 * 3600e3, 23 * 3600e3, 24 * 3600e3, 25 * 3600e3, 29 * 86400e3, 30 * 86400e3, 31 * 86400e3, 400 * 86400e3, -5e3];
test("'ativa há X': tabela de idades (s, min, h, d, data) igual à formatActivityTime do settings.html; data inválida some", async () => {
  const html = readFileSync(join(RAIZ, "frontend", "settings.html"), "utf8");
  const formatActivityTime = new Function(`${html.match(/function formatActivityTime\(iso\) \{[\s\S]*?\n\}/)[0]}; return formatActivityTime;`)();
  const sessoes = IDADES.map((ms, i) => ({ ...listaPadrao()[1], jti: `s${i}`, last_seen_at: ha(ms) }));
  sessoes.push({ ...listaPadrao()[1], jti: "ruim", last_seen_at: "ontem" }, { ...listaPadrao()[1], jti: "nulo", last_seen_at: null });
  const p = await abrirSeguranca({ sessoes });
  await p.sess.locator("li").first().waitFor();
  const metas = (await p.linhas()).map((l) => l.meta);
  await p.ctx.close();
  const real = Date.now;
  Date.now = () => AGORA;
  const esperado = [...IDADES.map((ms, i) => formatActivityTime(sessoes[i].last_seen_at)), "", ""];
  Date.now = real;
  assert.equal(esperado[0], "há 1s");
  assert.deepEqual(esperado.filter((e) => /^\d\d\/\d\d\/\d{4}$/.test(e)).length, 3, esperado.join()); // 30 d, 31 d e 400 d
  assert.deepEqual(metas, esperado.map((e) => (e ? `198.51.100.7 · ativa ${e}` : "198.51.100.7")));
});

for (const [width, height] of [[1440, 900], [390, 844], [320, 640]]) {
  test(`layout em ${width}×${height}: sem rolagem lateral, alvos de 44px, tudo dentro do painel, diálogo cabe`, async () => {
    const sessoes = listaPadrao();
    sessoes[1].device_label = "Aparelho".repeat(12); // 96 letras sem espaço
    sessoes[1].ip = "2001:0db8:85a3:0000:0000:8a2e:0370:7334"; // IPv6 comprido
    const p = await abrirSeguranca({ width, height, sessoes });
    await p.sess.locator("li").first().waitFor();
    const medir = () => p.page.evaluate(() => {
      const painel = document.querySelector(".conta-sessoes").getBoundingClientRect();
      const alvos = [...document.querySelectorAll(".conta-sessoes .btn, dialog[open] .btn")].map((e) => e.getBoundingClientRect());
      return { lateral: document.documentElement.scrollWidth - innerWidth, pequenos: alvos.filter((a) => a.width < 43.5 || a.height < 43.5).length,
        fora: [...document.querySelectorAll(".conta-sessoes li *")].filter((e) => { const r = e.getBoundingClientRect(); return r.right > painel.right + 0.5 || r.left < painel.left - 0.5; }).length };
    });
    const lista = await medir();
    await p.sess.getByRole("button", { name: "Encerrar todas as outras", exact: true }).click();
    const dlg = await medir();
    const cabe = await p.dialogo.evaluate((d) => { const r = d.getBoundingClientRect(); return r.left >= 0 && r.right <= innerWidth && r.top >= 0 && r.bottom <= innerHeight; });
    await p.page.screenshot({ path: join(process.env.TMPDIR ?? "/tmp", `sessoes-${width}.png`) });
    await p.ctx.close();
    assert.deepEqual(lista, { lateral: 0, pequenos: 0, fora: 0 });
    assert.deepEqual({ ...dlg, cabe }, { lateral: 0, pequenos: 0, fora: 0, cabe: true });
  });
}
