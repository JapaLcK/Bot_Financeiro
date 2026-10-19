/**
 * /painel#/conta/configuracoes (conta/Notificacoes.tsx, conta/api.ts): "Notificações", as 7 preferências da /settings.
 *   · a carga bate com o GET; cada interruptor salva sozinho, com o corpo mínimo (só o campo tocado) e o CSRF; UM PATCH em voo, no máximo (também se a rota é deixada e reaberta: a trava é do módulo);
 *   · a tela segue a RESPOSTA do PATCH; 400 sai verbatim, 500/prazo viram a reserva, 401 vira "sessão"; em toda falha o interruptor volta ao servidor;
 *   · 402 no GET = sem plano; sem e-mail/WhatsApp = interruptores travados e link para o Perfil; semanal fora do plano (travado, ou gravado e desligável);
 *   · o horário NÃO religa o diário (D1: o corpo leva o `daily_report_enabled` atual; o servidor de mentira imita o `set_daily_report_hour`);
 *   · um GET em voo durante o PATCH não sobrescreve; hora fora da lista aparece; as horas são as do settings.html (§0.7); sem uid nada é pedido;
 *   · invariante: nenhum PATCH com `whatsapp_updates_enabled` sem o clique no interruptor dele (é consentimento); layout em 1280 e 390.
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { navegador } from "./_guia.mjs";
import { RAIZ } from "./_painel.mjs";
import { JSON_ERRO, SESSAO, abrirSeguranca, ate, notifPadrao, portao } from "./_conta_seguranca.mjs";

const browser = navegador();
void browser;
const volta = (p) => p.page.evaluate(() => document.dispatchEvent(new Event("visibilitychange", { bubbles: true })));
const alerta = (p) => p.notif.locator("[role=alert]");
const marcados = (p) => p.notif.locator("input[role=switch]").evaluateAll((is) => is.map((i) => [i.labels[0].firstChild.textContent.trim(), i.checked, i.disabled]));
const hora = (p) => p.notif.locator("select").evaluate((s) => [s.selectedOptions[0].textContent, s.disabled]);
// A invariante do consentimento vale em TODO teste: fecha o contexto e confere quantos PATCH levaram `whatsapp_updates_enabled` (só o clique nele manda).
const fecha = async (p, cliquesNoWhatsApp = 0) => {
  await p.ctx.close();
  assert.equal(p.s.nPatches.filter((x) => "whatsapp_updates_enabled" in x.corpo).length, cliquesNoWhatsApp, "whatsapp_updates_enabled só vai com o clique no interruptor dele");
};
const semPlano = (r) => r.fulfill({ status: 402, json: { detail: { error: "subscription_required" } } });

test("a carga bate com o GET: título, e-mail e telefone formatado, os 6 interruptores e o horário", async () => {
  const p = await abrirSeguranca();
  await p.sw("Dicas de uso").waitFor();
  const [titulo, textos, rotulos, estado, h] = [await p.notif.locator("h2").innerText(), await p.notif.locator(".notif-dado").allInnerTexts(),
    await p.notif.locator("label").allInnerTexts(), await marcados(p), await hora(p)];
  const grupos = await p.notif.locator("[role=group] > h3").allTextContents();
  await fecha(p);
  assert.equal(titulo, "Notificações");
  assert.deepEqual(grupos, ["E-mail", "WhatsApp"]);
  assert.deepEqual(textos, ["ana.teste@exemplo.com", "(11) 98765-4321"]);
  assert.deepEqual(rotulos, ["Dicas de uso", "Insights financeiros", "Atualizações do Piggy", "Resumo diário", "Resumo semanal", "Resumo mensal", "Horário dos resumos"]);
  assert.deepEqual(estado, [["Dicas de uso", true, false], ["Insights financeiros", false, false], ["Atualizações do Piggy", true, false], ["Resumo diário", true, false],
    ["Resumo semanal", true, false], ["Resumo mensal", false, false]]);
  assert.deepEqual(h, ["09:00", false]);
});

test("um interruptor: UM PATCH com o corpo só do campo tocado e o CSRF; anuncia e o valor fica", async () => {
  const p = await abrirSeguranca();
  await p.sw("Dicas de uso").click();
  await p.page.waitForFunction(() => document.querySelector(".conta-notif > p[role=status]").textContent === "Preferências salvas");
  const [marca, gets] = [await p.sw("Dicas de uso").isChecked(), p.s.nGets];
  await p.page.waitForTimeout(300);
  await fecha(p);
  assert.deepEqual(p.s.nPatches.map((x) => [x.metodo, x.url, x.csrf, x.corpo]), [["PATCH", "/settings/7/notifications", "tok-123", { tip_email_enabled: false }]]);
  assert.equal(marca, false);
  assert.ok(gets >= 2, "a releitura depois do PATCH confirma");
});

test("NEG: cliques rápidos (o mesmo e outro interruptor) no mesmo tick = UM PATCH, mesmo com a resposta demorando", async () => {
  const p = await abrirSeguranca();
  await p.sw("Dicas de uso").waitFor();
  const [segura, solta] = portao();
  p.s.nPatch = async (r) => { await segura; p.s.notif.tip_email_enabled = false; await r.fulfill({ json: structuredClone(p.s.notif) }); };
  await p.page.evaluate(() => {
    const [a, b] = ["Dicas de uso", "Insights financeiros"].map((t) => [...document.querySelectorAll(".conta-notif label")].find((l) => l.textContent.trim() === t).control);
    a.click(); a.click(); b.click();
  });
  await p.page.waitForTimeout(400);
  const durante = [p.s.nPatches.length, await p.page.locator(".conta-notif > p[role=status]").innerText(), await p.sw("Dicas de uso").getAttribute("aria-disabled")];
  solta();
  await p.page.waitForFunction(() => document.querySelector(".conta-notif > p[role=status]").textContent === "Preferências salvas");
  await fecha(p);
  assert.deepEqual(durante, [1, "Salvando…", "true"]);
  assert.deepEqual(p.s.nPatches.map((x) => x.corpo), [{ tip_email_enabled: false }]);
});

test("a tela segue a resposta do PATCH, mesmo diferente do que foi clicado", async () => {
  const p = await abrirSeguranca();
  await p.sw("Dicas de uso").waitFor();
  p.s.nPatch = async (r) => { Object.assign(p.s.notif, { tip_email_enabled: true, monthly_report_enabled: true }); await r.fulfill({ json: structuredClone(p.s.notif) }); }; // o servidor ignorou o clique e ligou o mensal
  await p.sw("Dicas de uso").click();
  await p.page.waitForFunction(() => document.querySelector(".conta-notif > p[role=status]").textContent === "Preferências salvas");
  const estado = await marcados(p);
  await fecha(p);
  assert.deepEqual([estado[0][1], estado[5][1]], [true, true]);
});

test("falhas: 400 verbatim, 500 e prazo = reserva, 401 = sessão com Recarregar; o interruptor volta ao servidor e a lista é relida", async () => {
  const casos = [
    ["400", JSON_ERRO(400, "Vincule um e-mail para configurar notificações por e-mail."), "Vincule um e-mail para configurar notificações por e-mail."],
    ["500", JSON_ERRO(500, "x"), "Não foi possível salvar. Tente de novo."],
    ["403", JSON_ERRO(403, "Acesso negado para este usuário."), "Recarregue a página antes de salvar de novo."],
    ["401", JSON_ERRO(401, "Não autenticado.", SESSAO), "Sua sessão terminou."],
    ["prazo", () => new Promise(() => {}), "Não foi possível salvar. Tente de novo."],
  ];
  for (const [nome, gancho, texto] of casos) {
    const p = await abrirSeguranca();
    await p.sw("Dicas de uso").waitFor();
    if (nome === "prazo") await p.encurtaPrazo(400);
    p.s.nPatch = gancho;
    const antes = p.s.nGets;
    await p.sw("Dicas de uso").click();
    await alerta(p).waitFor();
    await p.page.waitForFunction(() => !document.querySelector(".conta-notif > p[role=status]").textContent && document.querySelector(".conta-notif input[role=switch]:not([aria-disabled])"));
    const [msg, marca, recarregar] = [await alerta(p).innerText(), await p.sw("Dicas de uso").isChecked(), await p.notif.getByRole("button", { name: "Recarregar" }).count()];
    const gets = p.s.nGets;
    await fecha(p);
    assert.ok(msg.startsWith(texto), `${nome}: ${msg}`);
    assert.equal(marca, true, `${nome}: o interruptor volta ao que o servidor tem`);
    assert.equal(recarregar, nome === "401" ? 1 : 0, nome);
    assert.ok(gets > antes, `${nome}: relê depois da falha`);
  }
});

const salvo = (p) => p.page.waitForFunction(() => document.querySelector(".conta-notif > p[role=status]").textContent === "Preferências salvas");

test("falha com a releitura PRESA: o interruptor volta ao servidor na hora (sem esperar o GET) e o prazo da releitura destrava os cliques", async () => {
  const p = await abrirSeguranca();
  await p.sw("Dicas de uso").waitFor();
  await p.encurtaPrazo(400);
  const [segura, solta] = portao();
  p.s.nHold = segura; // a releitura depois da falha nunca responde
  p.s.nPatch = (r) => { p.s.nPatch = null; return JSON_ERRO(500, "x")(r); };
  await p.sw("Atualizações do Piggy").click();
  await alerta(p).waitFor();
  const [antes, trava] = [await p.sw("Atualizações do Piggy").isChecked(), await p.sw("Atualizações do Piggy").getAttribute("aria-disabled")];
  await p.page.waitForTimeout(1000); // > prazo encurtado: sem comPrazoLeitura a trava (voando) nunca solta
  await p.sw("Dicas de uso").click();
  await p.page.waitForTimeout(400);
  const nPatches = p.s.nPatches.length;
  solta();
  await fecha(p, 1);
  assert.deepEqual([antes, trava], [true, "true"], "o interruptor mostra o servidor e segue travado (aria-disabled) com o GET ainda preso");
  assert.equal(nPatches, 2, "depois do prazo o próximo clique sai");
});

test("falha seguida de sucesso: o alerta da falha some depois do save bom", async () => {
  const p = await abrirSeguranca();
  await p.sw("Dicas de uso").waitFor();
  p.s.nPatch = (r) => { p.s.nPatch = null; return JSON_ERRO(500, "x")(r); };
  await p.sw("Dicas de uso").click();
  await alerta(p).waitFor();
  await p.page.waitForTimeout(500); // a releitura da falha termina e a trava solta
  await p.sw("Dicas de uso").click();
  await salvo(p);
  const [alertas, marca] = [await alerta(p).count(), await p.sw("Dicas de uso").isChecked()];
  await fecha(p);
  assert.deepEqual([alertas, marca], [0, false]);
});

test("a tela segue a RESPOSTA do PATCH, não a releitura: com o GET seguinte preso, vale o valor da resposta", async () => {
  const p = await abrirSeguranca();
  await p.sw("Dicas de uso").waitFor();
  const [segura, solta] = portao();
  p.s.nHold = segura; // o GET da releitura leva a foto antiga (mensal desligado) e não responde
  p.s.nPatch = (r) => r.fulfill({ json: { ...structuredClone(p.s.notif), tip_email_enabled: true, monthly_report_enabled: true } });
  await p.sw("Dicas de uso").click();
  await salvo(p);
  const estado = await marcados(p);
  solta();
  await fecha(p);
  assert.deepEqual([estado[0][1], estado[5][1]], [true, true]);
});

test("contrato: booleano que vem como string ou número NÃO conta como ligado (só `=== true`)", async () => {
  const p = await abrirSeguranca({ notif: { ...notifPadrao(), tip_email_enabled: "true", whatsapp_updates_enabled: 1, monthly_report_enabled: "true" } });
  await p.sw("Dicas de uso").waitFor();
  const estado = await marcados(p);
  await fecha(p);
  assert.deepEqual([estado[0][1], estado[2][1], estado[5][1]], [false, false, false]);
});

test("semanal fora do plano com o gravado ligado: durante o voo do desligar o interruptor já mostra desligado", async () => {
  const p = await abrirSeguranca({ notif: { ...notifPadrao(), weekly_report_available: false, weekly_report_enabled: false, weekly_report_stored_enabled: true } });
  await p.sw("Resumo semanal").waitFor();
  const [segura, solta] = portao();
  p.s.nPatch = async (r) => { await segura; p.s.notif.weekly_report_stored_enabled = false; await r.fulfill({ json: structuredClone(p.s.notif) }); };
  await p.sw("Resumo semanal").click();
  await p.page.waitForFunction(() => document.querySelector(".conta-notif > p[role=status]").textContent === "Salvando…");
  const voo = await p.sw("Resumo semanal").isChecked();
  solta();
  await salvo(p);
  await fecha(p);
  assert.equal(voo, false);
});

test("402 no GET: 'Indisponível sem plano ativo' e 'Ver planos' (/precos), nenhum interruptor; POS: com plano os 6 aparecem", async () => {
  const p = await abrirSeguranca();
  await p.sw("Dicas de uso").waitFor();
  p.s.nGetErro = semPlano;
  await p.page.reload();
  await p.notif.locator(".empty").waitFor();
  const [texto, href, n] = [await p.notif.locator(".empty p").innerText(), await p.notif.getByRole("link", { name: "Ver planos" }).getAttribute("href"), await p.sw(/./).count()];
  p.s.nGetErro = null;
  await p.page.reload();
  await p.sw("Dicas de uso").waitFor();
  const com = await p.sw(/./).count();
  await fecha(p);
  assert.deepEqual([texto, href, n, com], ["Indisponível sem plano ativo", "/precos", 0, 6]);
});

test("erro de carga (500 e contrato quebrado) = 'Tentar de novo' sem dado velho; 401 = 'Recarregar'; Tentar de novo relê", async () => {
  const p = await abrirSeguranca();
  await p.sw("Dicas de uso").waitFor();
  for (const [nome, gancho] of [["500", JSON_ERRO(500, "x")], ["contrato", (r) => r.fulfill({ json: { ok: true } })], ["hora 25", (r) => r.fulfill({ json: { ...notifPadrao(), daily_report_hour: 25 } })]]) {
    p.s.nGetErro = gancho;
    await volta(p);
    await alerta(p).waitFor();
    const [msg, n] = [await alerta(p).innerText(), await p.sw(/./).count()];
    assert.deepEqual([msg.split("\n")[0], n], ["Não foi possível carregar as notificações.", 0], nome);
    p.s.nGetErro = null;
    await p.notif.getByRole("button", { name: "Tentar de novo" }).click();
    await p.sw("Dicas de uso").waitFor();
  }
  p.s.nGetErro = JSON_ERRO(401, "Não autenticado.", SESSAO);
  await volta(p);
  await alerta(p).waitFor();
  const [msg, botao] = [await alerta(p).innerText(), await p.notif.getByRole("button", { name: "Recarregar" }).count()];
  await fecha(p);
  assert.deepEqual([msg.split("\n")[0], botao], ["Sua sessão terminou.", 1]);
});

test("sem e-mail: Dicas e Insights travados, '—' com 'Não vinculado' e 'Adicionar e-mail' (Perfil); nenhum PATCH. POS: com e-mail, habilitados", async () => {
  const sem = { ...notifPadrao(), email: null, email_notifications_available: false, tip_email_enabled: false, insight_email_enabled: false, engagement_email_enabled: false };
  const p = await abrirSeguranca({ notif: sem });
  await p.sw("Dicas de uso").waitFor();
  await p.sw("Dicas de uso").click({ force: true, timeout: 1500 }).catch(() => {});
  const [estado, vazio, link] = [await marcados(p), await p.notif.locator(".notif-dado").first().innerText(),
    await p.notif.getByRole("link", { name: "Adicionar e-mail" }).getAttribute("href")];
  const sr = await p.notif.locator(".notif-dado").first().locator(".sr-only").innerText();
  const nPatches = p.s.nPatches.length;
  await fecha(p);
  assert.deepEqual(estado.slice(0, 2), [["Dicas de uso", false, true], ["Insights financeiros", false, true]]);
  assert.deepEqual([vazio, sr, link, nPatches], ["—\nNão vinculado\nAdicionar e-mail", "Não vinculado", "#/conta/perfil", 0]);
  const q = await abrirSeguranca();
  await q.sw("Dicas de uso").waitFor();
  const [com, links] = [(await marcados(q)).slice(0, 2).map((e) => e[2]), await q.notif.getByRole("link", { name: "Adicionar e-mail" }).count()];
  await fecha(q);
  assert.deepEqual([com, links], [[false, false], 0]);
});

test("sem WhatsApp: só 'Atualizações do Piggy' trava, '—' e 'Conectar WhatsApp' (Perfil); os resumos seguem. POS: com WhatsApp, habilitado e UM PATCH ao clicar", async () => {
  const sem = { ...notifPadrao(), whatsapp_destination: null, whatsapp_updates_available: false, whatsapp_updates_enabled: false };
  const p = await abrirSeguranca({ notif: sem });
  await p.sw("Dicas de uso").waitFor();
  const [estado, link, nPatches] = [await marcados(p), await p.notif.getByRole("link", { name: "Conectar WhatsApp" }).getAttribute("href"), p.s.nPatches.length];
  await fecha(p);
  assert.deepEqual(estado.map((e) => e[2]), [false, false, true, false, false, false]);
  assert.deepEqual([link, nPatches], ["#/conta/perfil", 0]);
  const q = await abrirSeguranca();
  await q.sw("Atualizações do Piggy").click();
  await q.page.waitForFunction(() => document.querySelector(".conta-notif > p[role=status]").textContent === "Preferências salvas");
  await fecha(q, 1);
  assert.deepEqual(q.s.nPatches.map((x) => x.corpo), [{ whatsapp_updates_enabled: false }]);
});

test("semanal fora do plano: nunca gravado = travado com 'Plus e Pro'; gravado ligado = ligado, 'Pausado no seu plano', e desligar manda só {weekly_report_enabled:false}", async () => {
  const fora = { ...notifPadrao(), weekly_report_available: false, weekly_report_enabled: false, weekly_report_stored_enabled: false };
  const p = await abrirSeguranca({ notif: fora });
  await p.sw("Resumo semanal").waitFor();
  const [estado, selo] = [(await marcados(p))[4], await p.notif.locator("label .selo").allInnerTexts()];
  await fecha(p);
  assert.deepEqual([estado, selo], [["Resumo semanal", false, true], ["Plus e Pro"]]);
  const gravado = { ...notifPadrao(), weekly_report_available: false, weekly_report_enabled: false, weekly_report_stored_enabled: true };
  const q = await abrirSeguranca({ notif: gravado });
  await q.sw("Resumo semanal").waitFor();
  const [ligado, selo2] = [(await marcados(q))[4], await q.notif.locator("label .selo").allInnerTexts()];
  await q.sw("Resumo semanal").click();
  await q.page.waitForFunction(() => document.querySelector(".conta-notif > p[role=status]").textContent === "Preferências salvas");
  await q.page.waitForTimeout(300);
  const depois = [(await marcados(q))[4], await q.notif.locator("label .selo").allInnerTexts()];
  await fecha(q);
  assert.deepEqual([ligado, selo2], [["Resumo semanal", true, false], ["Pausado no seu plano"]]);
  assert.deepEqual(q.s.nPatches.map((x) => x.corpo), [{ weekly_report_enabled: false }]);
  assert.deepEqual(depois, [["Resumo semanal", false, true], ["Plus e Pro"]]);
});

test("D1: mudar o horário com o diário DESLIGADO manda o `daily_report_enabled` atual e o diário segue desligado (o servidor liga o diário ao gravar a hora)", async () => {
  const n = { ...notifPadrao(), daily_report_enabled: false };
  const p = await abrirSeguranca({ notif: n });
  await p.sw("Resumo diário").waitFor();
  await p.notif.locator("select").selectOption("11");
  await p.page.waitForFunction(() => document.querySelector(".conta-notif > p[role=status]").textContent === "Preferências salvas");
  await p.page.waitForTimeout(300);
  const [diario, h] = [(await marcados(p))[3], await hora(p)];
  const gravado = p.s.notif.daily_report_enabled;
  await fecha(p);
  assert.deepEqual(p.s.nPatches.map((x) => [x.csrf, x.corpo]), [["tok-123", { daily_report_hour: 11, daily_report_minute: 0, daily_report_enabled: false }]]);
  assert.deepEqual([diario, h, gravado], [["Resumo diário", false, false], ["11:00", false], false]);
  // com o diário ligado, o corpo leva true
  const q = await abrirSeguranca();
  await q.sw("Resumo diário").waitFor();
  await q.notif.locator("select").selectOption("18");
  await q.page.waitForFunction(() => document.querySelector(".conta-notif > p[role=status]").textContent === "Preferências salvas");
  await fecha(q);
  assert.deepEqual(q.s.nPatches.map((x) => x.corpo), [{ daily_report_hour: 18, daily_report_minute: 0, daily_report_enabled: true }]);
});

test("o horário trava sem nenhum resumo ligado", async () => {
  const p = await abrirSeguranca({ notif: { ...notifPadrao(), daily_report_enabled: false, weekly_report_enabled: false, weekly_report_stored_enabled: false, monthly_report_enabled: false } });
  await p.sw("Resumo diário").waitFor();
  const h = await hora(p);
  await fecha(p);
  assert.equal(h[1], true);
});

test("um GET em voo DURANTE o PATCH não repinta o valor de antes (o invalidate o cancela)", async () => {
  const p = await abrirSeguranca();
  await p.sw("Dicas de uso").waitFor();
  const [segura, solta] = portao();
  p.s.nHold = segura; // o GET leva a foto de ANTES do PATCH e só responde depois
  await volta(p);
  await p.page.waitForTimeout(300);
  await p.sw("Dicas de uso").click();
  await p.page.waitForFunction(() => document.querySelector(".conta-notif > p[role=status]").textContent === "Preferências salvas");
  p.s.nHold = null;
  solta();
  await p.page.waitForTimeout(600);
  const marca = await p.sw("Dicas de uso").isChecked();
  await fecha(p);
  assert.equal(marca, false);
});

test("NEG: sair da rota e voltar com o PATCH em voo NÃO libera um 2º PATCH (a trava é do módulo, não do componente)", async () => {
  const p = await abrirSeguranca();
  await p.sw("Dicas de uso").waitFor();
  const [segura, solta] = portao();
  p.s.nPatch = async (r) => { await segura; p.s.notif.tip_email_enabled = false; await r.fulfill({ json: structuredClone(p.s.notif) }); };
  await p.sw("Dicas de uso").click();
  await ate(p.page, () => p.s.nPatches.length === 1, "o 1º PATCH saiu");
  await p.page.evaluate(() => { location.hash = "#/conta/perfil"; });
  await p.page.locator("#page-title", { hasText: "Perfil" }).waitFor();
  await p.page.evaluate(() => { location.hash = "#/conta/configuracoes"; });
  await p.sw("Dicas de uso").waitFor(); // `Preferencias` remontou: sem `previa`, o clique só é barrado pela trava
  await p.sw("Dicas de uso").click();
  await p.page.waitForTimeout(400);
  assert.equal(p.s.nPatches.length, 1, "o clique depois de voltar não gera um 2º PATCH com o 1º em voo");
  solta();
  await ate(p.page, () => p.s.notif.tip_email_enabled === false, "o 1º PATCH terminou");
  await p.page.waitForTimeout(400);
  await p.sw("Dicas de uso").click(); // POS: solto o 1º, um clique novo gera o 2º
  await ate(p.page, () => p.s.nPatches.length === 2, "o 2º PATCH saiu");
  await fecha(p);
});

test("hora gravada fora da lista (15:00, 09:30) aparece no select, selecionada, sem mudar nada sozinha", async () => {
  for (const [h, m, texto] of [[15, 0, "15:00"], [9, 30, "09:30"]]) {
    const p = await abrirSeguranca({ notif: { ...notifPadrao(), daily_report_hour: h, daily_report_minute: m } });
    await p.sw("Dicas de uso").waitFor();
    const [sel, opcoes] = [await hora(p), await p.notif.locator("select option").allTextContents()];
    await fecha(p);
    assert.deepEqual(sel, [texto, false], texto);
    assert.equal(opcoes.length, 12, texto);
    assert.equal(p.s.nPatches.length, 0);
  }
});

test("§0.7: as horas do select são as do settings.html (#notif-daily-hour)", async () => {
  const html = readFileSync(join(RAIZ, "frontend", "settings.html"), "utf8");
  const bloco = html.slice(html.indexOf('id="notif-daily-hour"'));
  const doHtml = [...bloco.slice(0, bloco.indexOf("</select>")).matchAll(/<option value="(\d+)">(\d\d:00)<\/option>/g)].map((m) => [m[1], m[2]]);
  const p = await abrirSeguranca();
  await p.sw("Dicas de uso").waitFor();
  const daTela = await p.notif.locator("select option").evaluateAll((os) => os.map((o) => [o.value, o.textContent]));
  await fecha(p);
  assert.equal(doHtml.length, 11);
  assert.deepEqual(daTela, doHtml);
});

test("sem uid no dashboard-profile: o painel não aparece e nenhuma requisição de notificações sai", async () => {
  const p = await abrirSeguranca({ semUid: true });
  await p.page.waitForTimeout(800);
  const [n, achou] = [await p.notif.count(), p.s.reqs.filter((q) => q.url.includes("/notifications")).length];
  await fecha(p);
  assert.deepEqual([n, achou, p.s.nGets], [0, 0, 0]);
});

for (const [width, height] of [[1280, 800], [390, 844]]) {
  test(`layout em ${width}×${height}: sem rolagem lateral, interruptores e select de 44px, tudo dentro do painel`, async () => {
    const notif = { ...notifPadrao(), email: "nome.muito.comprido.de.teste.para.quebrar.linha@subdominio-comprido.exemplo.com.br", weekly_report_available: false, weekly_report_enabled: false, weekly_report_stored_enabled: true };
    const p = await abrirSeguranca({ width, height, notif });
    await p.sw("Dicas de uso").waitFor();
    const m = await p.page.evaluate(() => {
      const painel = document.querySelector(".conta-notif").getBoundingClientRect();
      const alvos = [...document.querySelectorAll(".conta-notif input[role=switch], .conta-notif select, .conta-notif .btn")].map((e) => e.getBoundingClientRect());
      const sw = document.querySelector(".conta-notif input[role=switch]").getBoundingClientRect();
      return { lateral: document.documentElement.scrollWidth - innerWidth, pequenos: alvos.filter((a) => a.width < 43.5 || a.height < 43.5).length, alvos: alvos.length, sw: [sw.width, sw.height],
        fora: [...document.querySelectorAll(".conta-notif *")].filter((e) => { const r = e.getBoundingClientRect(); return r.width && (r.right > painel.right + 0.5 || r.left < painel.left - 0.5); }).length,
        painel: [Math.round(painel.left), Math.round(painel.right)] };
    });
    await p.notif.scrollIntoViewIfNeeded();
    await p.page.screenshot({ path: join(process.env.TMPDIR ?? "/tmp", `notificacoes-${width}.png`), fullPage: true });
    await fecha(p);
    assert.deepEqual({ lateral: m.lateral, pequenos: m.pequenos, fora: m.fora, sw: m.sw }, { lateral: 0, pequenos: 0, fora: 0, sw: [44, 44] }, JSON.stringify(m));
  });
}
