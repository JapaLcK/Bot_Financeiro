/**
 * MFA da Segurança, as bordas (conta/MfaForm.tsx, conta/api.ts): erros do servidor, duplo envio, travas em voo, prazo e o enable que se perde.
 *   · "Senha incorreta." (401 SEM WWW-Authenticate) nunca vira "Sua sessão terminou."; 401 COM WWW-Authenticate é sessão (passa pelo /auth/refresh);
 *   · 400/404/409/429 saem verbatim, 403 e 5xx/rede com o texto aprovado (regerar: 'os códigos antigos podem ter sido invalidados'), e a escrita que pode ter ido relê o status;
 *   · duplo clique/Enter = 1 POST; Esc, fundo, Cancelar e Fechar travados em voo; senha e código limpos depois de toda tentativa;
 *   · setup, disable e reset estouram em 30 s; enable e regenerate NÃO têm prazo (o servidor comita e devolve os códigos uma vez);
 *   · enable que se perde: o status diz se o MFA ligou (T2 + gerar novos) ou não (T5); nada de segredo em URL, storage, HTML ou console.
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { navegador } from "./_guia.mjs";
import { CODIGOS, JSON_ERRO, NOVOS, SEGREDO, SENHA, SESSAO, TOTP, abrirSeguranca, ate, ateOCofre, ateOQr, portao, rastros, vazou } from "./_conta_seguranca.mjs";

const browser = navegador();
void browser;
const ON = { enabled: true, has_pending_secret: false, backup_codes_remaining: 10 };
const T2 = "O MFA foi ativado, mas os códigos não chegaram. Gere novos códigos.";
const T5 = "Não foi possível ativar agora. Tente de novo.";
const PERDIDO = "Os códigos antigos podem ter sido invalidados. Gere novos códigos."; // regerar sem resposta: o servidor pode ter comitado

const FLUXOS = [
  ["setup", "Ativar", async (p) => { await p.senha.fill(SENHA); }, "Erro ao iniciar setup"],
  ["disable", "Desativar", async (p) => { await p.senha.fill(SENHA); await p.codigo.fill(TOTP); }, "Erro ao desativar"],
  ["regerar", "Gerar novos códigos de backup", async (p) => { await p.senha.fill(SENHA); await p.codigo.fill(TOTP); }, "Erro ao gerar códigos"],
];
for (const [rota, rotulo, preenche, reserva] of FLUXOS) {
  test(`${rota}: 401 'Senha incorreta.' (SEM WWW-Authenticate) nunca vira 'Sua sessão terminou.'; senha limpa e em foco; sem /auth/refresh; 401 COM WWW = sessão`, async () => {
    const p = await abrirSeguranca({ mfa: ON });
    await p.page.waitForTimeout(100);
    if (rota === "setup") { p.s.mfa = { enabled: false, has_pending_secret: false, backup_codes_remaining: 0 }; await p.page.reload(); }
    await p.bt(rotulo).click();
    await preenche(p);
    p.s[rota] = JSON_ERRO(401, "Senha incorreta.");
    await p.enviar.click();
    await p.alerta.waitFor();
    await p.page.waitForTimeout(150);
    const r = await p.page.evaluate(() => ({ senha: document.querySelector("dialog[open] input[name=senha]").value, foco: document.activeElement.name, sessao: document.body.innerText.includes("Sua sessão terminou"), recarregar: !![...document.querySelectorAll("dialog[open] button")].find((b) => b.textContent === "Recarregar") }));
    const texto = await p.alerta.innerText();
    const refreshes = p.s.refreshes;
    await preenche(p);
    p.s[rota] = JSON_ERRO(401, "Not authenticated", SESSAO);
    await p.enviar.click();
    await ate(p.page, () => p.s.refreshes > refreshes, "o 401 de sessão passou pelo /auth/refresh");
    await p.dialogo.getByRole("button", { name: "Recarregar" }).waitFor();
    const sessao = await p.alerta.innerText();
    const campos = await p.page.evaluate(() => [...document.querySelectorAll("dialog[open] input")].map((i) => i.value));
    await p.ctx.close();
    assert.equal(texto, "Senha incorreta.");
    assert.deepEqual(r, { senha: "", foco: "senha", sessao: false, recarregar: false });
    assert.equal(refreshes, 0);
    assert.equal(sessao, "Sua sessão terminou.");
    assert.ok(campos.every((v) => v === ""), campos.join());
    assert.deepEqual(vazou(p.s, SENHA, ["/auth/mfa/" + (rota === "regerar" ? "regenerate-backup-codes" : rota === "disable" ? "disable" : "setup")]), []);
  });

  test(`${rota}: erros do servidor — 400/404/409/429 verbatim, 403 e 5xx/rede com o texto aprovado ('${reserva}'); a escrita pode ter ido, então relê o status`, async () => {
    const p = await abrirSeguranca({ mfa: ON });
    if (rota === "setup") { p.s.mfa = { enabled: false, has_pending_secret: false, backup_codes_remaining: 0 }; await p.page.reload(); }
    await p.bt(rotulo).click();
    const casos = [
      ["400", JSON_ERRO(400, "Código inválido."), "Código inválido."], ["404", JSON_ERRO(404, "Usuário não encontrado."), "Usuário não encontrado."],
      ["409", JSON_ERRO(409, "MFA já está ativado."), "MFA já está ativado."], ["429", JSON_ERRO(429, "Muitas tentativas. Aguarde alguns minutos e tente novamente."), "Muitas tentativas. Aguarde alguns minutos e tente novamente."],
      ["403", JSON_ERRO(403, "CSRF"), "Recarregue a página antes de salvar de novo."],
      ["500", (r) => r.fulfill({ status: 500, body: "<html>" }), rota === "regerar" ? PERDIDO : reserva], ["rede", (r) => r.abort("internetdisconnected"), rota === "regerar" ? PERDIDO : reserva],
    ];
    const vistos = [];
    for (const [nome, falha, esperado] of casos) {
      await preenche(p);
      p.s[rota] = falha;
      const hits = p.s.statusHits;
      await p.enviar.click();
      await p.alerta.waitFor();
      vistos.push([nome, await p.alerta.innerText(), p.s.statusHits > hits || nome]);
      if (nome === "500" || nome === "rede" || nome === "409") await ate(p.page, () => p.s.statusHits > hits, `${nome} relê o status`);
      await p.enviar.waitFor();
      assert.equal(await p.enviar.isEnabled(), true);
      assert.equal(esperado, vistos.at(-1)[1], nome);
    }
    await p.ctx.close();
    assert.equal(vistos.length, casos.length);
  });
}

for (const [nome, antes, preenche, clica] of [
  ["setup (senha)", (p) => { p.s.mfa = { enabled: false, has_pending_secret: false, backup_codes_remaining: 0 }; }, async (p) => { await p.senha.fill(SENHA); }, "Ativar"],
  ["disable", () => {}, async (p) => { await p.senha.fill(SENHA); await p.codigo.fill(TOTP); }, "Desativar"],
]) {
  test(`${nome}: duplo clique e Enter+Enter no mesmo tick = 1 POST; em voo o botão diz 'Enviando…' e os campos ficam desativados`, async () => {
    const p = await abrirSeguranca({ mfa: ON });
    antes(p);
    const [segura, solta] = portao();
    p.s.setup = async (r) => { await segura; return r.fulfill({ json: { secret: SEGREDO, uri: "x", qr_code: "data:image/svg+xml;base64,PHN2Zy8+" } }); };
    p.s.disable = async (r) => { await segura; return r.fulfill({ json: { ok: true } }); };
    await p.page.reload();
    await p.bt(clica).click();
    await preenche(p);
    await p.enviar.dblclick();
    await p.dialogo.getByRole("button", { name: "Enviando…" }).waitFor();
    await p.page.evaluate(() => { const f = document.querySelector("dialog[open] form"); f.requestSubmit(); f.requestSubmit(); });
    const emVoo = [await p.senha.isDisabled(), await p.dialogo.getByRole("button", { name: "Cancelar" }).isDisabled(), await p.dialogo.getByRole("button", { name: "Fechar" }).isDisabled()];
    solta();
    await p.page.waitForTimeout(300);
    await p.ctx.close();
    assert.deepEqual(emVoo, [true, true, true]);
    assert.equal(p.s.chamadas.setup.length + p.s.chamadas.disable.length, 1);
  });
}

test("enable: duplo clique no mesmo tick = 1 POST (Ativar MFA)", async () => {
  const p = await abrirSeguranca();
  await ateOQr(p);
  const [segura, solta] = portao();
  p.s.enable = async (r) => { await segura; return r.fulfill({ json: { ok: true, backup_codes: CODIGOS } }); };
  await p.codigo.fill(TOTP);
  await p.enviar.dblclick();
  await p.page.evaluate(() => { const f = document.querySelector("dialog[open] form"); f.requestSubmit(); f.requestSubmit(); });
  await p.page.waitForTimeout(200);
  solta();
  await p.cofre.waitFor();
  await p.ctx.close();
  assert.equal(p.s.chamadas.enable.length, 1);
});

for (const [nome, rota, rotulo, preenche] of [
  ["setup", "setup", "Ativar", async (p) => { await p.senha.fill(SENHA); }],
  ["disable", "disable", "Desativar", async (p) => { await p.senha.fill(SENHA); await p.codigo.fill(TOTP); }],
]) {
  test(`${nome} em voo: Esc (6×), clique no fundo, Cancelar e Fechar não fecham o modal; solto o POST, o resultado chega`, async () => {
    const p = await abrirSeguranca({ mfa: ON });
    if (rota === "setup") { p.s.mfa = { enabled: false, has_pending_secret: false, backup_codes_remaining: 0 }; await p.page.reload(); }
    const [segura, solta] = portao();
    p.s[rota] = async (r) => { await segura; return rota === "setup" ? r.fulfill({ json: { secret: SEGREDO, uri: "x", qr_code: "data:image/svg+xml;base64,PHN2Zy8+" } }) : r.fulfill({ json: { ok: true } }); };
    await p.bt(rotulo).click();
    await preenche(p);
    await p.enviar.click();
    await p.dialogo.getByRole("button", { name: "Enviando…" }).waitFor();
    await p.page.waitForTimeout(500); // passada a janela em que o clique no fundo ainda não vale
    for (let i = 0; i < 6; i++) await p.page.keyboard.press("Escape"); // entre um Esc e o `close` o <dialog> pisca fechado: só o fim conta
    await p.page.mouse.click(5, 5); // o fundo
    await p.page.waitForTimeout(200);
    const aberto = await p.dialogo.count();
    solta();
    if (rota === "setup") await p.dialogo.locator(".mfa-qr img").waitFor(); else await p.fechado();
    await p.ctx.close();
    assert.equal(aberto, 1);
  });
}

test("prazo de 30 s: setup e disable estouram e liberam o modal; enable e regenerate NÃO têm prazo e ainda entregam os códigos depois", async () => {
  const p = await abrirSeguranca({ mfa: ON });
  const [segura, solta] = portao();
  await p.encurtaPrazo(400);
  // disable que nunca responde
  p.s.disable = async () => { await segura; };
  await p.bt("Desativar").click();
  await p.senha.fill(SENHA); await p.codigo.fill(TOTP);
  await p.enviar.click();
  await p.alerta.waitFor();
  const disable = [await p.alerta.innerText(), await p.enviar.isEnabled()];
  await p.dialogo.getByRole("button", { name: "Cancelar" }).click(); await p.fechado(); // destravado
  p.s.disable = null;
  const ms = await p.page.evaluate(() => [...window.__ms]);
  // regenerate segurado bem além do prazo encurtado: sem abort, os códigos chegam
  p.s.regerar = async (r) => { await new Promise((ok) => setTimeout(ok, 1500)); return r.fulfill({ json: { backup_codes: NOVOS } }); };
  await p.bt("Gerar novos códigos de backup").click();
  await p.senha.fill(SENHA); await p.codigo.fill(TOTP);
  await p.enviar.click();
  await p.cofre.waitFor({ timeout: 8000 });
  const regerar = await p.codigos();
  const msDepois = await p.page.evaluate(() => [...window.__ms]);
  await p.cofre.getByRole("button", { name: "Concluído" }).click();
  // setup que nunca responde, e depois enable segurado
  p.s.mfa = { enabled: false, has_pending_secret: false, backup_codes_remaining: 0 };
  await p.page.reload();
  await p.encurtaPrazo(400);
  p.s.setup = async () => { await segura; };
  await p.bt("Ativar").click();
  await p.senha.fill(SENHA);
  await p.enviar.click();
  await p.alerta.waitFor();
  const setup = [await p.alerta.innerText(), await p.senha.inputValue()];
  p.s.setup = null;
  await p.senha.fill(SENHA); await p.enviar.click(); await p.dialogo.locator(".mfa-qr img").waitFor();
  p.s.enable = async (r) => { await new Promise((ok) => setTimeout(ok, 1500)); s_ok(p); return r.fulfill({ json: { ok: true, backup_codes: CODIGOS } }); };
  await p.codigo.fill(TOTP);
  await p.enviar.click();
  await p.cofre.waitFor({ timeout: 8000 });
  const enable = await p.codigos();
  const msFim = await p.page.evaluate(() => [...window.__ms]);
  solta();
  await p.ctx.close();
  assert.deepEqual(disable, ["Erro ao desativar", true]);
  assert.deepEqual(ms, [30000]);
  assert.deepEqual(regerar, NOVOS);
  assert.deepEqual(msDepois, [30000]); // o regenerate não pediu prazo nenhum
  assert.deepEqual(setup, ["Erro ao iniciar setup", ""]);
  assert.deepEqual(enable, CODIGOS);
  assert.deepEqual(msFim, [30000, 30000]); // os dois setups (o que estourou e o refeito); o enable não pediu prazo
});
const s_ok = (p) => { p.s.mfa = { enabled: true, has_pending_secret: false, backup_codes_remaining: 10 }; };

test("enable que se perde com o MFA LIGADO no servidor: 'os códigos não chegaram' + 'Gerar novos códigos de backup' (senha e TOTP), e o fluxo termina no Cofre", async () => {
  const p = await abrirSeguranca();
  await ateOQr(p);
  p.s.enable = (r) => { s_ok(p); return r.abort("internetdisconnected"); };
  await p.codigo.fill(TOTP);
  await p.enviar.click();
  await p.alerta.filter({ hasText: T2 }).waitFor();
  const r = [await p.alerta.innerText(), await p.dialogo.getByRole("button", { name: "Gerar novos códigos de backup" }).count(), await p.dialogo.locator("input").count(), await p.dialogo.locator(".mfa-qr").count(), await p.dialogo.locator(".mfa-segredo").count(), await p.cofre.count()];
  await p.dialogo.getByRole("button", { name: "Gerar novos códigos de backup" }).click();
  await p.dialogo.locator("h2", { hasText: "Gerar novos códigos de backup" }).waitFor();
  const regerar = [await p.dialogo.locator("input[name=senha]").count(), await p.dialogo.locator("input[name=codigo]").count(), await p.dialogo.locator(".mfa-qr").count(), await p.dialogo.locator("[role=alert]").count()];
  await p.senha.fill(SENHA); await p.codigo.fill(TOTP);
  await p.enviar.click();
  await p.cofre.waitFor();
  const cofre = await p.codigos();
  await p.cofre.getByRole("button", { name: "Concluído" }).click();
  await p.bt("Desativar").waitFor();
  await p.ctx.close();
  assert.deepEqual(r, [T2, 1, 0, 0, 0, 0]);
  assert.deepEqual(regerar, [1, 1, 0, 0]);
  assert.deepEqual(cofre, NOVOS);
});

for (const [nome, falha, ligou] of [
  ["rede, MFA NÃO ligou", () => (r) => r.abort("internetdisconnected"), false],
  ["500, MFA NÃO ligou", () => (r) => r.fulfill({ status: 500, body: "x" }), false],
  ["500, MFA ligou", (p) => (r) => { s_ok(p); return r.fulfill({ status: 500, body: "x" }); }, true],
  ["200 sem códigos (lista vazia): o servidor comitou e os códigos não vieram", (p) => (r) => { s_ok(p); return r.fulfill({ json: { ok: true, backup_codes: [] } }); }, true],
  ["200 com códigos que não são texto", (p) => (r) => { s_ok(p); return r.fulfill({ json: { ok: true, backup_codes: [1, 2, null] } }); }, true],
  ["200 com 8 textos e 1 número: nunca mostra MENOS códigos que os comitados", (p) => (r) => { s_ok(p); return r.fulfill({ json: { ok: true, backup_codes: [...CODIGOS.slice(0, 8), 5] } }); }, true],
]) {
  test(`enable perdido (${nome}): ${ligou ? "T2 + gerar novos" : "T5 'Tente de novo' e o QR continua, dá para tentar outra vez"}; nenhum código aparece`, async () => {
    const p = await abrirSeguranca();
    await ateOQr(p);
    p.s.enable = falha(p);
    await p.codigo.fill(TOTP);
    await p.enviar.click();
    await p.alerta.waitFor();
    const r = [await p.alerta.innerText(), await p.dialogo.getByRole("button", { name: "Gerar novos códigos de backup" }).count(), await p.dialogo.locator(".mfa-qr img").count(), await p.cofre.count(), await p.page.locator(".mfa-codigos").count()];
    if (!ligou) {
      p.s.enable = null;
      await p.codigo.fill(TOTP); await p.enviar.click();
      await p.cofre.waitFor();
    }
    await p.ctx.close();
    assert.deepEqual(r, ligou ? [T2, 1, 0, 0, 0] : [T5, 0, 1, 0, 0]);
  });
}

test("nada vaza: senha, TOTP, segredo e códigos só vão no corpo da sua rota; nenhum rastro em storage, URL ou HTML depois de fechar tudo", async () => {
  const p = await abrirSeguranca();
  const logs = [];
  p.page.on("console", (m) => logs.push(m.text()));
  await ateOCofre(p);
  await p.cofre.getByRole("button", { name: "Concluído" }).click();
  await p.fechado();
  await p.bt("Desativar").click();
  await p.senha.fill(SENHA); await p.codigo.fill(CODIGOS[2]);
  await p.enviar.click(); await p.fechado();
  const resto = await rastros(p.page);
  await p.ctx.close();
  const em = (rota) => ["/auth/mfa/" + rota];
  assert.deepEqual(vazou(p.s, SENHA, ["/auth/mfa/setup", "/auth/mfa/disable"]), []);
  assert.deepEqual(vazou(p.s, SEGREDO, []), []);
  assert.deepEqual(vazou(p.s, CODIGOS[0], []), []);
  assert.deepEqual(vazou(p.s, CODIGOS[2], em("disable")), []);
  assert.deepEqual(vazou(p.s, TOTP, em("enable").concat(em("disable"))), []);
  assert.ok(p.s.reqs.some((q) => q.corpo.includes(SENHA)) && p.s.reqs.some((q) => q.corpo.includes(TOTP)), "a medição enxerga os corpos");
  for (const segredo of [SENHA, TOTP, SEGREDO, ...CODIGOS]) assert.ok(!resto.includes(segredo), `rastro de ${segredo}`);
  assert.ok(!logs.join("\n").match(new RegExp([SENHA, SEGREDO, ...CODIGOS].join("|"))), logs.join("\n"));
});

for (const [nome, corpo] of [
  ["QR que não é data URI de imagem", { secret: SEGREDO, uri: "x", qr_code: "javascript:alert(1)" }],
  ["QR em https (fora do contrato)", { secret: SEGREDO, uri: "x", qr_code: "https://exemplo.com/qr.svg" }],
  ["sem segredo", { uri: "x", qr_code: "data:image/svg+xml;base64,PHN2Zy8+" }],
]) {
  test(`setup com resposta fora do contrato (${nome}): 'Erro ao iniciar setup', nenhum <img> com a URL recebida e o formulário continua no passo da senha`, async () => {
    const p = await abrirSeguranca();
    p.s.setup = (r) => r.fulfill({ json: corpo });
    await p.bt("Ativar").click();
    await p.senha.fill(SENHA);
    await p.enviar.click();
    await p.alerta.waitFor();
    const r = [await p.alerta.innerText(), await p.dialogo.locator("img").count(), await p.senha.count()];
    await p.ctx.close();
    assert.deepEqual(r, ["Erro ao iniciar setup", 0, 1]);
  });
}
