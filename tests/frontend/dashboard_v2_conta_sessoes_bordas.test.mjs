/**
 * Dispositivos conectados (conta/Sessoes.tsx, conta/api.ts), as bordas:
 *   · o foco depois de um DELETE que falha: a linha (ou o "Encerrar todas as outras") sumiu na releitura → título; ficou → o botão;
 *   · resposta fora do contrato (item null, sem jti, rótulo objeto) não derruba o painel: o item ruim some, o rótulo ruim vira o texto do servidor;
 *   · o jti vai codificado na URL; ícone de celular (iOS, Android, PigBank app); `horaRelativa` com o ISO de 6 casas no Safari 14;
 *   · a releitura que FALHA depois do DELETE que falhou (DELETE 404/500 x GET 500/rede/401): o bloco de erro substitui a lista, foco no título;
 *   · corpo 200 fora do contrato (null, {}, [], ok:false, sessions string, itens todos descartados) = "Não foi possível carregar", não lista vazia;
 *   · "Recarregar" (401 na carga e no DELETE) recarrega de verdade; o "outras" do cache vale para is_current e current_jti incoerentes.
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { navegador } from "./_guia.mjs";
import { DOIS, JSON_ERRO, SESSAO, abrirSeguranca, ha, listaPadrao, portao } from "./_conta_seguranca.mjs";

const browser = navegador();
void browser;
const encerrar = (p, nome) => p.sess.locator("li").filter({ hasText: nome }).getByRole("button", { name: "Encerrar", exact: true });
const todas = (p) => p.sess.getByRole("button", { name: "Encerrar todas as outras", exact: true });
const confirmar = (p, nome = "Encerrar") => p.dialogo.getByRole("button", { name: nome, exact: true });
const volta = (p) => p.page.evaluate(() => document.dispatchEvent(new Event("visibilitychange", { bubbles: true })));
const ondeEstaOFoco = (p) => p.page.evaluate(() => { const a = document.activeElement; return [a?.tagName, a?.textContent?.trim(), a?.closest("li")?.querySelector("b")?.textContent ?? null]; });

test("foco depois de um DELETE que falha: a releitura tirou a linha → título; a linha ficou → o botão que abriu o diálogo", async () => {
  const casos = [
    ["404 e a linha sumiu", (r) => r.fulfill({ status: 404, json: { detail: "Sessão não encontrada ou já encerrada." } }), true, false, ["H2", "Dispositivos conectados", null]],
    ["500 e a linha ficou", (r) => r.fulfill({ status: 500, body: "x" }), false, false, ["BUTTON", "Encerrar", "Safari • iOS"]],
    ["rede e a linha ficou", (r) => r.abort(), false, false, ["BUTTON", "Encerrar", "Safari • iOS"]],
    ["outras: 500 e já não havia outras", (r) => r.fulfill({ status: 500, body: "x" }), true, true, ["H2", "Dispositivos conectados", null]],
    ["outras: 500 e as outras ficaram", (r) => r.fulfill({ status: 500, body: "x" }), false, true, ["BUTTON", "Encerrar todas as outras", null]],
  ];
  for (const [rotulo, hook, some, outras, esperado] of casos) {
    const p = await abrirSeguranca();
    await p.sess.locator("li").first().waitFor();
    p.s.sessDel = hook;
    if (some) p.s.sessoes = outras ? p.s.sessoes.slice(0, 1) : p.s.sessoes.filter((x) => x.jti !== DOIS); // sumiu por trás
    if (outras) { await todas(p).click(); await confirmar(p, "Encerrar outras").click(); } else { await encerrar(p, "Safari").click(); await confirmar(p).click(); }
    await p.fechado();
    await p.alertaSess.waitFor();
    const foco = await ondeEstaOFoco(p);
    await p.ctx.close();
    assert.deepEqual(foco, esperado, rotulo);
  }
});

test("resposta fora do contrato: item null, sem jti, jti número, rótulo/ip/data objeto — o painel segue de pé e só o item válido aparece", async () => {
  const lixo = [null, 7, "x", {}, { jti: 1 }, { jti: "" }, { jti: "so-jti", device_label: { a: 1 }, ip: { b: 2 }, last_seen_at: { c: 3 }, is_current: "true" }];
  const p = await abrirSeguranca({ sessoes: [...lixo, ...listaPadrao()] });
  const erros = [];
  p.page.on("pageerror", (e) => erros.push(e.message));
  await p.sess.locator("li").first().waitFor();
  const linhas = await p.linhas();
  const vivo = await p.painel.count();
  await p.ctx.close();
  assert.deepEqual(erros, []);
  assert.equal(vivo, 1);
  // o item com jti string e o resto ruim fica: rótulo "Dispositivo desconhecido" (core/sessions.py), "IP desconhecido", sem "ativa", com botão (is_current não é `true`)
  assert.deepEqual(linhas.map((l) => [l.nome, l.meta, l.botao]), [["Dispositivo desconhecido", "IP desconhecido", true], ["Chrome • macOS", "203.0.113.5 · ativa há 30s", false], ["Safari • iOS", "198.51.100.7 · ativa há 5 min", true], ["Edge • Windows", "IP desconhecido · ativa há 3h", true]]);
});

test("o jti vai codificado na URL (um jti com # e ? continua um só segmento)", async () => {
  const sessoes = [...listaPadrao(), { ...listaPadrao()[1], jti: "x#y?z", device_label: "Firefox • Linux" }];
  const p = await abrirSeguranca({ sessoes });
  await p.sess.locator("li").first().waitFor();
  await encerrar(p, "Firefox").click();
  await confirmar(p).click();
  await p.fechado();
  const url = new URL(p.s.dels[0].url);
  await p.ctx.close();
  assert.equal(url.pathname + url.hash, "/settings/7/sessions/x%23y%3Fz");
});

test("ícone de celular para iOS, Android e PigBank app; computador para o resto", async () => {
  const [a, b, c, d] = ["Chrome • Android", "PigBank app", "Firefox • Linux", "Safari • iPadOS"];
  const p = await abrirSeguranca({ sessoes: [a, b, c, d].map((device_label, i) => ({ ...listaPadrao()[1], jti: `m${i}`, device_label })) });
  await p.sess.locator("li").first().waitFor();
  const icones = (await p.linhas()).map((l) => [l.nome, l.icone]);
  await p.ctx.close();
  assert.deepEqual(icones, [[a, "ph ph-device-mobile"], [b, "ph ph-device-mobile"], [c, "ph ph-desktop"], [d, "ph ph-device-mobile"]]);
});

test("'ativa há X' com o ISO de 6 casas num navegador que o recusa (Safari 14): o `instante` o poda, `new Date(iso)` daria NaN", async () => {
  const p = await abrirSeguranca();
  await p.sess.locator("li").first().waitFor();
  await p.page.evaluate(() => { // o corte do Safari 14: fração com mais de 3 casas não é data
    const ruim = (s) => typeof s === "string" && /\.\d{4,}/.test(s), parse = Date.parse;
    Date.parse = (s) => (ruim(s) ? NaN : parse(s));
    window.Date = new Proxy(Date, { construct: (t, a) => (ruim(a[0]) ? new t(NaN) : new t(...a)) });
  });
  const antes = await p.page.evaluate(() => [Date.parse("2026-10-02T15:00:00.123456+00:00"), new Date("2026-10-02T15:00:00.123456+00:00").getTime()]);
  p.s.sessoes[1].last_seen_at = ha(600e3); // outro dado: o React só repinta o que mudou
  await volta(p);
  await p.page.waitForFunction(() => document.querySelectorAll(".conta-sessoes li")[1].querySelector(".sess-meta").textContent.includes("10 min"));
  const metas = (await p.linhas()).map((l) => l.meta);
  await p.ctx.close();
  assert.deepEqual(antes.map(Number.isNaN), [true, true]); // a simulação vale
  assert.deepEqual(metas, ["203.0.113.5 · ativa há 30s", "198.51.100.7 · ativa há 10 min", "IP desconhecido · ativa há 3h"]);
});

test("'Recarregar' (401 na carga e 401 no DELETE) recarrega a página de verdade", async () => {
  for (const noDelete of [false, true]) {
    const p = await abrirSeguranca();
    await p.sess.locator("li").first().waitFor();
    if (noDelete) { p.s.sessDel = JSON_ERRO(401, "Não autenticado.", SESSAO); await encerrar(p, "Safari").click(); await confirmar(p).click(); await p.fechado(); }
    else { p.s.sessGetErro = JSON_ERRO(401, "Não autenticado.", SESSAO); await volta(p); }
    await p.alertaSess.waitFor();
    await p.page.evaluate(() => { window.__antes = true; });
    const [carregou] = await Promise.all([p.page.waitForEvent("load"), p.sess.getByRole("button", { name: "Recarregar" }).click()]);
    const restou = await p.page.evaluate(() => window.__antes === true);
    await p.ctx.close();
    assert.ok(carregou, String(noDelete));
    assert.equal(restou, false, noDelete ? "DELETE" : "carga");
  }
});

test("'Encerrar todas as outras' com is_current e current_jti INCOERENTES: a atual (por qualquer um dos dois) fica no cache, só a outra sai", async () => {
  const p = await abrirSeguranca();
  p.s.atual = DOIS; // current_jti aponta para a 2ª, que diz is_current:false; a 1ª diz is_current:true
  await p.page.reload();
  await p.sess.locator("li").first().waitFor();
  const [segura, solta] = portao();
  p.s.sessHold = segura; // a releitura não responde: o que se vê é o cache do setQueryData
  await todas(p).click();
  await confirmar(p, "Encerrar outras").click();
  await p.fechado();
  await p.page.waitForFunction(() => document.querySelectorAll(".conta-sessoes li").length === 2);
  const linhas = (await p.linhas()).map((l) => [l.nome, l.selo, l.botao]);
  solta();
  await p.ctx.close();
  assert.deepEqual(linhas, [["Chrome • macOS", true, false], ["Safari • iOS", true, false]]);
});

test("a releitura que FALHA depois do DELETE que falhou troca a lista pelo bloco de erro: o foco vai ao título, não ao <body>", async () => {
  const dels = [["404", (r) => r.fulfill({ status: 404, json: { detail: "Sessão não encontrada ou já encerrada." } })], ["500", (r) => r.fulfill({ status: 500, body: "x" })]];
  const gets = [["500", (r) => r.fulfill({ status: 500, body: "x" })], ["rede", (r) => r.abort()], ["401", JSON_ERRO(401, "Não autenticado.", SESSAO)]];
  for (const [dn, del] of dels) for (const [gn, get] of gets) {
    const p = await abrirSeguranca();
    await p.sess.locator("li").first().waitFor();
    p.s.sessDel = del;
    await encerrar(p, "Safari").click();
    await p.page.waitForTimeout(500);
    p.s.sessGetErro = get;
    await confirmar(p).click();
    await p.fechado();
    await p.page.locator(".conta-sessoes .empty[role=alert]").waitFor();
    const foco = await ondeEstaOFoco(p);
    await p.ctx.close();
    assert.deepEqual(foco, ["H2", "Dispositivos conectados", null], `DELETE ${dn} + GET ${gn}`);
  }
});

test("corpo 200 fora do contrato é erro de carga ('Tentar de novo'), nunca 'Nenhuma sessão ativa.'; a lista vazia de verdade continua vazia", async () => {
  const ruins = [null, {}, [], { ok: false }, { ok: true, sessions: "x", current_jti: "a" }, { ok: true, sessions: [{ jti: 1 }, null, "x"], current_jti: "a" }];
  for (const corpo of [...ruins, { ok: true, sessions: [], current_jti: "a" }]) {
    const p = await abrirSeguranca();
    p.s.sessGetErro = (r) => r.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(corpo) });
    await p.page.reload();
    await p.sess.locator("p, ul").first().waitFor();
    await p.page.waitForFunction(() => !document.querySelector(".conta-sessoes [role=status].faint"));
    const [texto, tentar, todasN] = [await p.sess.innerText(), await p.sess.getByRole("button", { name: "Tentar de novo" }).count(), await todas(p).count()];
    await p.ctx.close();
    const vazia = !!corpo?.ok && Array.isArray(corpo.sessions) && !corpo.sessions.length;
    assert.equal(texto.includes(vazia ? "Nenhuma sessão ativa." : "Não foi possível carregar as sessões."), true, JSON.stringify(corpo));
    assert.deepEqual([tentar, todasN], [vazia ? 0 : 1, 0], JSON.stringify(corpo));
  }
});

test("rótulo vazio: o nome e a descrição do 'Encerrar' resolvem para o texto do servidor, não para o vazio", async () => {
  const sessoes = listaPadrao();
  sessoes[1].device_label = ""; sessoes[2].device_label = "   ";
  const p = await abrirSeguranca({ sessoes });
  await p.sess.locator("li").first().waitFor();
  const r = await p.page.evaluate(() => [...document.querySelectorAll(".conta-sessoes li button")].map((b) => document.getElementById(b.getAttribute("aria-describedby"))?.textContent));
  await p.ctx.close();
  assert.deepEqual(r, ["Dispositivo desconhecido", "Dispositivo desconhecido"]);
});
