/**
 * PL-06 B6, PR 1: a linha "Avisos do Piggy por e-mail" nas duas telas web, `/settings` e `/painel`, em desktop e mobile.
 *
 * O servidor de mentira é o `aplicaNotif` (`_conta_seguranca.mjs`), que imita a rota; a rota real é medida em
 * `tests/test_insights_adesao.py`. Aqui se mede só o que a tela faz com a resposta: nasce desligada, some com `notice`
 * nulo, trava para ligar e nunca para desligar, e o texto é o do servidor.
 *
 * Estados: fora da env, liberada sem adesão, aderida, depois de cancelar, sem plano (com e sem adesão), sem e-mail.
 * POSITIVO do grupo: ligar e reler persiste, nas duas telas. Sem ele, um interruptor sempre travado passaria.
 */
import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { chromium } from "playwright";
import { startServer } from "./_server.mjs";
import { navegador } from "./_guia.mjs";
import { RAIZ } from "./_painel.mjs";
import { AVISO_D5, abrirSeguranca, aplicaNotif, notifPadrao } from "./_conta_seguranca.mjs";

// O selo de carência vem do servidor (`insight_alert_email_paused_notice`): o teste usa outro texto para provar que a tela não tem cópia.
const PAUSADO = "Texto do servidor: pausado até o pagamento.";
const TEXTO = "Texto do servidor: opcional, e-mail às 10h, e o link Cancelar inscrição corta tudo.";
const TELAS = [[1280, 800], [390, 844]];
const ROTULO = "Avisos do Piggy por e-mail";
const liberada = (extra = {}) => ({ ...notifPadrao(), insight_email_enabled: true, insight_alert_email_enabled: false, insight_alert_email_available: true, insight_alert_email_notice: TEXTO, ...extra });
const ESTADOS = {
  "liberada sem adesão": [liberada(), { ligado: false, travado: false }],
  "aderida": [liberada({ insight_alert_email_enabled: true, insight_email_off_notice: AVISO_D5 }), { ligado: true, travado: false }],
  "depois de cancelar": [liberada({ tip_email_enabled: false, insight_email_enabled: false, engagement_email_enabled: false }), { ligado: false, travado: true, selo: "Ligue Insights financeiros" }],
  "sem plano": [liberada({ insight_alert_email_available: false }), { ligado: false, travado: true, selo: "Plus e Pro" }],
  "sem plano, aderida": [liberada({ insight_alert_email_available: false, insight_alert_email_enabled: true, insight_alert_email_paused_notice: PAUSADO }), { ligado: true, travado: false, estado: PAUSADO }],
  "sem e-mail": [liberada({ email: null, email_notifications_available: false, tip_email_enabled: false, insight_email_enabled: false }), { ligado: false, travado: true }],
};

// ── /painel ──────────────────────────────────────────────────────────────────
void navegador();
const abrePainel = async (notif, tela = TELAS[0]) => {
  const p = await abrirSeguranca({ width: tela[0], height: tela[1], notif });
  await p.sw("Dicas de uso").waitFor();
  return p;
};
const medidas = (page, seletorLinha, seletorAlvo) => page.evaluate(([l, a]) => {
  const linha = document.querySelector(l), alvo = document.querySelector(a).getBoundingClientRect(), r = linha.getBoundingClientRect();
  return { lateral: document.documentElement.scrollWidth - innerWidth, alvo: [Math.round(alvo.width), Math.round(alvo.height)],
    fora: [...linha.querySelectorAll("*")].filter((e) => { const b = e.getBoundingClientRect(); return b.width && (b.right > r.right + 0.5 || b.left < r.left - 0.5); }).length };
}, [seletorLinha, seletorAlvo]);

for (const tela of TELAS) {
  test(`/painel ${tela[0]}: fora da env a linha não existe (o resto segue)`, async () => {
    const p = await abrePainel({ ...liberada(), insight_alert_email_notice: null }, tela);
    const [n, texto] = [await p.sw(ROTULO).count(), await p.notif.innerText()];
    await p.ctx.close();
    assert.equal(n, 0);
    assert.ok(!texto.includes("Avisos do Piggy") && !texto.includes(TEXTO));
  });

  for (const [nome, [notif, esperado]] of Object.entries(ESTADOS)) {
    test(`/painel ${tela[0]}: ${nome}`, async () => {
      const p = await abrePainel(notif, tela);
      const sw = p.sw(ROTULO);
      await sw.waitFor();
      const li = p.notif.locator("li", { has: sw });
      const [ligado, travado, notas, selo, descrito, selosIguais] = [await sw.isChecked(), await sw.isDisabled(), await li.locator(".notif-nota").allInnerTexts(),
        await li.locator(".selo").allTextContents(),
        await sw.evaluate((i) => i.getAttribute("aria-describedby").split(" ").map((x) => document.getElementById(x)?.textContent)),
        await p.notif.locator(".selo").allTextContents().then((t) => t.length - new Set(t).size)];
      await sw.evaluate((i) => i.setAttribute("data-aviso", ""));
      await li.evaluate((l) => l.setAttribute("data-linha-aviso", ""));
      const m = await medidas(p.page, "[data-linha-aviso]", "[data-aviso]");
      await p.ctx.close();
      const notasEsperadas = esperado.estado ? [TEXTO, esperado.estado] : [TEXTO];
      assert.deepEqual({ ligado, travado, notas, descrito, selo, selosIguais },
        { ligado: esperado.ligado, travado: esperado.travado, notas: notasEsperadas, descrito: notasEsperadas, selo: esperado.selo ? [esperado.selo] : [], selosIguais: 0 });
      assert.deepEqual(m, { lateral: 0, alvo: [44, 44], fora: 0 }, JSON.stringify(m));
    });
  }
}

test("/painel: ligar manda só o campo, persiste na releitura; desligar volta a nula. Travada não manda nada", async () => {
  const p = await abrePainel(liberada());
  await p.sw(ROTULO).click();
  await p.page.waitForFunction(() => document.querySelector(".conta-notif > p[role=status]").textContent === "Preferências salvas");
  await p.page.reload();
  await p.sw(ROTULO).waitFor();
  const depois = await p.sw(ROTULO).isChecked();
  await p.sw(ROTULO).click();
  await p.page.waitForFunction(() => document.querySelector(".conta-notif > p[role=status]").textContent === "Preferências salvas");
  const corpos = p.s.nPatches.map((x) => x.corpo);
  const final = p.s.notif.insight_alert_email_enabled;
  await p.ctx.close();
  assert.equal(depois, true);
  assert.deepEqual(corpos, [{ insight_alert_email_enabled: true }, { insight_alert_email_enabled: false }]);
  assert.equal(final, false);

  const q = await abrePainel(ESTADOS["depois de cancelar"][0]);
  await q.sw(ROTULO).click({ force: true, timeout: 1500 }).catch(() => {});
  const n = q.s.nPatches.length;
  await q.ctx.close();
  assert.equal(n, 0);
});

test("/painel: D5, a nota avisa antes; desligar Insights tira a adesão, trava a linha e diz isso visível", async () => {
  const p = await abrePainel(liberada({ insight_alert_email_enabled: true, insight_email_off_notice: AVISO_D5 }));
  // `exact`: travada, a linha do aviso ganha o selo "Ligue Insights financeiros" e casaria por substring.
  const insights = p.page.getByRole("switch", { name: "Insights financeiros", exact: true });
  const antes = await insights.evaluate((i) => document.getElementById(i.getAttribute("aria-describedby"))?.textContent);
  await insights.click();
  // O aviso de "salvas" pode pintar antes do `useQuery`: a espera é pela linha, não pelo aviso.
  await p.page.waitForFunction(() => document.querySelector("input[id$='-insight_alert_email_enabled']").disabled);
  const [ligado, travado] = [await p.sw(ROTULO).isChecked(), await p.sw(ROTULO).isDisabled()];
  const msg = p.notif.locator(".notif-desligou");
  const [texto, visivel, notaDepois] = [await msg.textContent(), await msg.isVisible(), await p.notif.locator("li", { has: insights }).locator(".notif-nota").count()];
  const caixa = await msg.boundingBox();
  await p.ctx.close();
  assert.equal(antes, AVISO_D5);
  assert.deepEqual([ligado, travado], [false, true]);
  assert.deepEqual([texto, visivel, notaDepois], ["Avisos do Piggy por e-mail desligados.", true, 0]);
  assert.ok(caixa.width > 1 && caixa.height > 1, "a mensagem não pode ser só sr-only");
});

test("/painel: D5 sem adesão não mostra nota, e desligar Insights não diz nada de avisos", async () => {
  const p = await abrePainel(liberada());
  const n = await p.notif.locator("li", { has: p.sw("Insights financeiros") }).locator(".notif-nota").count();
  await p.sw("Insights financeiros").click();
  await p.page.waitForFunction(() => document.querySelector(".conta-notif > p[role=status].sr-only").textContent === "Preferências salvas");
  const msgs = await p.notif.locator(".notif-desligou").count();
  await p.ctx.close();
  assert.deepEqual([n, msgs], [0, 0]);
});

test("/painel: sem plano não há caminho de desligar ali (o /settings é a saída, §11 do plano)", async () => {
  const p = await abrirSeguranca({ notif: liberada({ insight_alert_email_enabled: true }) });
  p.s.nGetErro = (r) => r.fulfill({ status: 402, json: { detail: { error: "subscription_required", insight_alert_email_enabled: true } } });
  await p.page.reload();
  await p.notif.getByText("Indisponível sem plano ativo").waitFor();
  const [botoes, switches] = [await p.page.getByRole("button", { name: "Desligar agora" }).count(), await p.notif.locator("input").count()];
  await p.ctx.close();
  assert.deepEqual([botoes, switches], [0, 0]);
});

test("/painel: falha do PATCH volta ao servidor e mostra o motivo", async () => {
  const p = await abrePainel(liberada());
  p.s.nPatch = (r) => r.fulfill({ status: 409, json: { detail: "Ligue Insights financeiros primeiro." } });
  await p.sw(ROTULO).click();
  await p.notif.locator("[role=alert]").waitFor();
  const [ligado, msg] = [await p.sw(ROTULO).isChecked(), await p.notif.locator("[role=alert]").innerText()];
  await p.ctx.close();
  assert.equal(ligado, false);
  assert.match(msg, /Ligue Insights financeiros primeiro/);
});

// ── /settings ────────────────────────────────────────────────────────────────
let server, browser, ORIGIN;
before(async () => { ({ proc: server, origin: ORIGIN } = await startServer()); browser = await chromium.launch(); });
after(async () => { await browser?.close(); server?.kill(); });

const FRONTEND = join(RAIZ, "frontend");
const json = (body, status = 200) => ({ status, contentType: "application/json", body: JSON.stringify(body) });

async function abreSettings(notif, tela = TELAS[0], { falha = null } = {}) {
  const page = await browser.newPage({ viewport: { width: tela[0], height: tela[1] } });
  const estado = { notif: structuredClone(notif), patches: [], falha };
  await page.route("**/*", (route) => {
    const url = new URL(route.request().url());
    if (url.origin !== ORIGIN) return route.abort();
    if (/\.[a-z0-9]+$/i.test(url.pathname)) return route.continue();
    return route.fulfill(json({}));
  });
  await page.route("**/static/auth-refresh.js", (r) => r.fulfill({ status: 200, contentType: "application/javascript", body: readFileSync(join(FRONTEND, "static", "auth-refresh.js"), "utf8") }));
  await page.route("**/auth/validate", (r) => r.fulfill(json({ user_id: 1 })));
  await page.route("**/auth/me", (r) => r.fulfill(json({ of_ui_enabled: false })));
  await page.route("**/auth/mfa/status", (r) => r.fulfill(json({ enabled: false })));
  await page.route("**/settings/1/notifications", (r) => {
    if (r.request().method() !== "PATCH") return r.fulfill(json(estado.notif));
    const corpo = r.request().postDataJSON();
    estado.patches.push(corpo);
    if (estado.falha) return r.fulfill(json({ detail: estado.falha }, 409));
    const res = aplicaNotif(estado.notif, corpo);
    return r.fulfill(json(res.json, res.status ?? 200));
  });
  await page.goto(`${ORIGIN}/settings.html?view=notifications`);
  await page.waitForFunction(() => document.getElementById("notif-status-value").textContent.includes("resumo"));
  await page.waitForFunction(() => !document.getElementById("notif-daily-report").disabled);
  return { page, estado };
}
// Fim do PATCH: é o `finally` de `updateNotificationSettings` que devolve os controles.
const salvou = (page) => page.waitForFunction(() => !document.getElementById("notif-daily-report").disabled);

for (const tela of TELAS) {
  test(`/settings ${tela[0]}: fora da env a linha fica oculta`, async () => {
    const { page } = await abreSettings({ ...liberada(), insight_alert_email_notice: null }, tela);
    try {
      assert.equal(await page.locator("#notif-insight-aviso-row").isVisible(), false);
      assert.equal(await page.locator("#notif-monthly-report").count(), 1);
      assert.match(await page.locator("#notif-list").innerText(), /Insights financeiros/);
    } finally { await page.close(); }
  });

  for (const [nome, [notif, esperado]] of Object.entries(ESTADOS)) {
    test(`/settings ${tela[0]}: ${nome}`, async () => {
      const { page } = await abreSettings(notif, tela);
      try {
        const sw = page.locator("#notif-insight-aviso");
        assert.equal(await page.locator("#notif-insight-aviso-row").isVisible(), true);
        assert.equal(await page.locator("#notif-insight-aviso-text").textContent(), TEXTO);
        assert.deepEqual([await sw.isChecked(), await sw.isDisabled()], [esperado.ligado, esperado.travado]);
        const m = await medidas(page, "#notif-insight-aviso-row", "#notif-insight-aviso-row .switch");
        assert.equal(m.lateral, 0, JSON.stringify(m));
        assert.equal(m.fora, 0, JSON.stringify(m));
      } finally { await page.close(); }
    });
  }
}

test("/settings: orientação de cada trava, e a descrição nova de Insights financeiros", async () => {
  const lido = async (nome) => {
    const { page } = await abreSettings(ESTADOS[nome][0]);
    const estado = page.locator("#notif-insight-aviso-state");
    const texto = await estado.isVisible() ? await estado.textContent() : "";
    await page.close();
    return texto;
  };
  assert.equal(await lido("liberada sem adesão"), "");
  assert.equal(await lido("sem e-mail"), "", "o aviso do topo da lista já diz para vincular");
  assert.equal(await lido("depois de cancelar"), "Ligue Insights financeiros para usar este aviso.");
  assert.equal(await lido("sem plano"), "Disponível nos planos Plus e Pro.");
  assert.equal(await lido("sem plano, aderida"), PAUSADO);
  const { page } = await abreSettings(liberada());
  const lista = await page.locator("#notif-list").innerText();
  await page.close();
  assert.ok(lista.includes("Curiosidades sobre finanças, uma vez por mês."));
  assert.ok(!lista.includes("Receba análises e alertas"));
});

test("/settings: ligar persiste na releitura; desligar manda false; os outros controles voltam", async () => {
  const { page, estado } = await abreSettings(liberada());
  try {
    await page.locator("#notif-insight-aviso").check();
    await salvou(page);
    await page.reload();
    await page.waitForFunction(() => document.getElementById("notif-insight-aviso").checked);
    await salvou(page);
    await page.locator("#notif-insight-aviso").uncheck();
    await salvou(page);
    assert.deepEqual(estado.patches, [{ insight_alert_email_enabled: true }, { insight_alert_email_enabled: false }]);
    assert.equal(await page.locator("#notif-insight-aviso").isChecked(), false);
    assert.equal(await page.locator("#notif-insight-aviso").isDisabled(), false);
  } finally { await page.close(); }
});

test("/settings: D5, nota antes; desligar Insights trava a linha e diz no toast; a trava resiste à volta dos controles", async () => {
  const { page } = await abreSettings(liberada({ insight_alert_email_enabled: true, insight_email_off_notice: AVISO_D5 }));
  try {
    const nota = page.locator("#notif-insight-off-note");
    assert.deepEqual([await nota.isVisible(), await nota.textContent(),
      await page.locator("#notif-insight-email").getAttribute("aria-describedby")], [true, AVISO_D5, "notif-insight-off-note"]);
    await page.locator("#notif-insight-email").uncheck();
    await salvou(page);
    assert.deepEqual([await page.locator("#notif-insight-aviso").isChecked(), await page.locator("#notif-insight-aviso").isDisabled()], [false, true]);
    assert.match(await page.locator("#toast").textContent(), /Avisos do Piggy por e-mail desligados\./);
    assert.equal(await nota.isVisible(), false);
  } finally { await page.close(); }
});

test("/settings: o interruptor do aviso descreve-se pelo texto e pelo estado", async () => {
  const { page } = await abreSettings(liberada());
  try {
    assert.equal(await page.locator("#notif-insight-aviso").getAttribute("aria-describedby"), "notif-insight-aviso-text notif-insight-aviso-state");
    assert.equal(await page.locator("#notif-insight-off-note").isVisible(), false, "sem adesão, sem nota");
  } finally { await page.close(); }
});

async function settings402(tela, detail) {
  const page = await browser.newPage({ viewport: { width: tela[0], height: tela[1] } });
  const patches = [];
  await page.route("**/*", (route) => {
    const url = new URL(route.request().url());
    if (url.origin !== ORIGIN) return route.abort();
    if (/\.[a-z0-9]+$/i.test(url.pathname)) return route.continue();
    return route.fulfill(json({}));
  });
  await page.route("**/static/auth-refresh.js", (r) => r.fulfill({ status: 200, contentType: "application/javascript", body: readFileSync(join(FRONTEND, "static", "auth-refresh.js"), "utf8") }));
  await page.route("**/auth/validate", (r) => r.fulfill(json({ user_id: 1 })));
  await page.route("**/auth/me", (r) => r.fulfill(json({ of_ui_enabled: false })));
  await page.route("**/auth/mfa/status", (r) => r.fulfill(json({ enabled: false })));
  await page.route("**/settings/1/notifications", (r) => {
    if (r.request().method() !== "PATCH") return r.fulfill(json({ detail }, 402));
    patches.push(r.request().postDataJSON());
    return r.fulfill(json({ ok: true, insight_alert_email_enabled: false }));
  });
  await page.goto(`${ORIGIN}/settings.html?view=notifications`);
  await page.waitForFunction(() => document.getElementById("notif-status-value").textContent.includes("Indisponível"));
  return { page, patches };
}

for (const tela of TELAS) {
  test(`/settings ${tela[0]}: D2, cortado com adesão vê "Desligar agora" e nenhum interruptor`, async () => {
    const { page, patches } = await settings402(tela, { error: "subscription_required", insight_alert_email_enabled: true });
    try {
      const bloco = page.locator("#notif-aviso-sem-plano");
      await bloco.waitFor();
      const [switches, lateral, alvo] = [await bloco.locator("input").count(),
        await page.evaluate(() => document.documentElement.scrollWidth - innerWidth),
        await page.locator("#notif-aviso-desligar").boundingBox()];
      const texto = await page.locator("#notif-aviso-sem-plano-texto").textContent();
      await page.locator("#notif-aviso-desligar").click();
      const resultado = page.locator("#notif-aviso-sem-plano-resultado");
      await page.waitForFunction(() => document.getElementById("notif-aviso-sem-plano-resultado").textContent === "Avisos desligados.");
      const [papel, focado, visivel] = [await resultado.getAttribute("role"),
        await page.evaluate(() => document.activeElement?.id), await resultado.isVisible()];
      assert.deepEqual([switches, patches, await page.locator("#notif-aviso-desligar").isVisible()],
        [0, [{ insight_alert_email_enabled: false }], false]);
      assert.equal(texto, "Se o pagamento restabelecer seu plano em até 1 hora após a perda do acesso, os avisos continuam; depois disso, será preciso ligar de novo.");
      assert.deepEqual([papel, focado, visivel], ["status", "notif-aviso-sem-plano-resultado", true]);
      assert.equal(lateral, 0);
      if (tela[0] < 540) assert.ok(alvo.height >= 44, `alvo ${alvo.height}`);
    } finally { await page.close(); }
  });
}

test("/settings: D2, 402 sem adesão fica igual ao de sempre", async () => {
  const { page } = await settings402(TELAS[0], { error: "subscription_required" });
  try {
    await page.waitForTimeout(300);
    assert.equal(await page.locator("#notif-aviso-sem-plano").isVisible(), false);
  } finally { await page.close(); }
});

test("/settings: falha do PATCH volta ao servidor, sem GET bom para repintar", async () => {
  const { page, estado } = await abreSettings(liberada(), TELAS[0], { falha: "Ligue Insights financeiros primeiro." });
  try {
    await page.route("**/settings/1/notifications", (r) => r.fulfill(r.request().method() === "PATCH" ? json({ detail: estado.falha }, 409) : json({}, 503)));
    await page.locator("#notif-insight-aviso").click();
    await page.waitForFunction(() => document.getElementById("toast").className.includes("error"));
    await salvou(page);
    assert.equal(await page.locator("#notif-insight-aviso").isChecked(), false);
  } finally { await page.close(); }
});
