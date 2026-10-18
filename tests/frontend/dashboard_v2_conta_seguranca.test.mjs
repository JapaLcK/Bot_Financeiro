/**
 * /painel#/conta/configuracoes (conta/Seguranca.tsx, conta/api.ts): a Segurança dentro de Configurações; o link "Abrir configurações" segue embaixo.
 *   · só ícone e valor (a palavra é sr-only); leitura em GET /auth/mfa/status e /auth/me, SEM /api/v2; uid do dashboard-profile;
 *   · status com erro: "Não deu para carregar a segurança da conta." + "Tentar de novo", NUNCA "Ativar"; 401 de sessão esconde o cache;
 *   · senha: o botão manda o link (POST /settings/7/password-reset com o CSRF), a mensagem do servidor sai inline, um POST por clique;
 *   · conta só-Google: "Definir senha" e o diálogo "Defina uma senha primeiro", sem POST.
 * Os fluxos do MFA (ativar, desativar, regerar) moram em dashboard_v2_conta_seguranca_mfa.test.mjs; o Cofre, em ..._cofre.test.mjs.
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { join } from "node:path";
import { navegador } from "./_guia.mjs";
import { JSON_ERRO, MSG_RESET, SESSAO, abrirSeguranca, ate, portao } from "./_conta_seguranca.mjs";

const browser = navegador();
void browser;
const ON = { enabled: true, has_pending_secret: false, backup_codes_remaining: 7 };

test("exibição em 1440×900, 390×844 e 320×640: ícone e valor, palavras só para leitor de tela, sem rolagem lateral, alvos de 44px", async () => {
  for (const [width, height] of [[1440, 900], [390, 844], [320, 640]]) {
    for (const mfa of [undefined, ON]) {
      const p = await abrirSeguranca({ width, height, ...(mfa ? { mfa } : {}) });
      await p.bt(mfa ? "Desativar" : "Ativar").waitFor();
      const r = await p.page.evaluate(() => {
        const linhas = [...document.querySelectorAll(".conta-seguranca dl > div")];
        const painel = document.querySelector(".conta-seguranca").getBoundingClientRect();
        const alvos = [...document.querySelectorAll(".conta-seguranca .btn")].map((e) => e.getBoundingClientRect());
        return {
          rotulos: linhas.map((l) => l.querySelector("dt .sr-only").textContent),
          icones: linhas.map((l) => l.querySelector("dt i").className),
          // o texto à vista de cada linha: sem a palavra sr-only e sem os botões (que têm a lista própria abaixo)
          visivel: linhas.map((l) => [...l.querySelectorAll("dd")].map((d) => { const c = d.cloneNode(true); c.querySelectorAll(".sr-only, button").forEach((e) => e.remove()); return c.textContent.trim(); }).filter(Boolean)),
          sr: [...document.querySelectorAll(".conta-seguranca dd .sr-only")].map((e) => e.textContent),
          botoes: [...document.querySelectorAll(".conta-seguranca .btn")].map((b) => b.textContent),
          lateral: document.documentElement.scrollWidth - innerWidth,
          pequenos: alvos.filter((a) => a.width < 43.5 || a.height < 43.5).length,
          fora: alvos.filter((a) => a.left < painel.left - 0.5 || a.right > painel.right + 0.5).length,
          titulo: document.querySelector("h2#seg-t").textContent,
        };
      });
      await p.page.screenshot({ path: join(process.env.TMPDIR ?? "/tmp", `seguranca-${width}-${mfa ? "on" : "off"}.png`) });
      await p.ctx.close();
      const rotulo = `${width} ${mfa ? "on" : "off"}`;
      assert.deepEqual(r.rotulos, ["Senha", "Autenticação em 2 etapas"], rotulo);
      assert.deepEqual(r.icones, ["ph ph-lock-key", mfa ? "ph ph-shield-check" : "ph ph-shield"], rotulo);
      assert.deepEqual(r.visivel, [["••••••••"], mfa ? ["Códigos de backup restantes: 7 de 10"] : ["—"]], rotulo);
      assert.deepEqual(r.sr, [mfa ? "Ativada" : "Desativada"], rotulo);
      assert.deepEqual(r.botoes, mfa ? ["Resetar senha por e-mail", "Desativar", "Gerar novos códigos de backup"] : ["Resetar senha por e-mail", "Ativar"], rotulo);
      assert.deepEqual({ lateral: r.lateral, pequenos: r.pequenos, fora: r.fora, titulo: r.titulo }, { lateral: 0, pequenos: 0, fora: 0, titulo: "Segurança" }, rotulo);
    }
  }
});

test("Configurações: a Segurança vem antes e o link 'Abrir configurações' (/settings) continua embaixo; Cobrança segue ponte", async () => {
  const p = await abrirSeguranca();
  const r = await p.page.evaluate(() => {
    const seg = document.querySelector(".conta-seguranca"), link = document.querySelector(".conta-ponte a");
    return { antes: !!(seg.compareDocumentPosition(link) & Node.DOCUMENT_POSITION_FOLLOWING), href: link.getAttribute("href"), texto: link.textContent, titulo: document.querySelector("#page-title").textContent };
  });
  await p.ctx.close();
  assert.deepEqual(r, { antes: true, href: "/settings", texto: "Abrir configurações", titulo: "Configurações" });
});

test("a leitura vai a /auth/mfa/status e /auth/me SEM /api/v2; nada de estado de MFA em storage", async () => {
  const p = await abrirSeguranca({ mfa: ON });
  await p.bt("Desativar").waitFor();
  const guardado = await p.page.evaluate(() => [localStorage, sessionStorage].flatMap((st) => Object.keys(st).map((k) => k + "=" + st.getItem(k))).join("\n"));
  await p.ctx.close();
  const gets = new Set(p.s.reqs.filter((q) => q.metodo === "GET").map((q) => new URL(q.url).pathname));
  assert.ok(gets.has("/auth/mfa/status") && gets.has("/auth/me") && gets.has("/auth/dashboard-profile"), [...gets].join());
  assert.ok(![...gets].some((g) => g.startsWith("/api/v2/mfa") || g.includes("/api/v2/auth")), [...gets].join());
  assert.ok(!/enabled|backup_codes/.test(guardado), guardado);
});

const FALHAS = [
  ["500", (r) => r.fulfill({ status: 500, body: "x" })],
  ["404", JSON_ERRO(404, "Usuário não encontrado.")],
  ["rede", (r) => r.abort("internetdisconnected")],
  ["JSON inválido", (r) => r.fulfill({ status: 200, body: "<html>" })],
];
for (const [nome, falha] of FALHAS) {
  test(`status com erro (${nome}): a mensagem e 'Tentar de novo'; NUNCA 'Ativar' nem 'Desativar'; recupera ao tentar de novo`, async () => {
    const p = await abrirSeguranca();
    p.s.statusErro = falha;
    await p.page.reload();
    await p.painel.getByRole("alert").waitFor();
    const r = [await p.painel.getByRole("alert").innerText(), await p.bt("Ativar").count(), await p.bt("Desativar").count(), await p.bt("Gerar novos códigos de backup").count(), await p.bt("Resetar senha por e-mail").isEnabled()];
    p.s.statusErro = null;
    p.s.mfa = ON;
    await p.bt("Tentar de novo").click();
    await p.bt("Desativar").waitFor();
    await p.ctx.close();
    assert.deepEqual(r, ["Não deu para carregar a segurança da conta.", 0, 0, 0, true]);
  });
}

test("status que falha na releitura (foco da aba) com dado em cache: tira as ações, não mostra 'desativado'", async () => {
  const p = await abrirSeguranca({ mfa: ON });
  await p.bt("Desativar").waitFor();
  p.s.statusErro = (r) => r.fulfill({ status: 500, body: "x" });
  await p.page.evaluate(() => document.dispatchEvent(new Event("visibilitychange", { bubbles: true })));
  await p.painel.getByRole("alert").waitFor();
  const r = [await p.painel.getByRole("alert").innerText(), await p.bt("Ativar").count(), await p.bt("Desativar").count(), await p.painel.innerText().then((t) => t.includes("restantes"))];
  await p.ctx.close();
  assert.deepEqual(r, ["Não deu para carregar a segurança da conta.", 0, 0, false]);
});

test("sessão acaba com a Segurança na tela (401 COM WWW-Authenticate): 'Sua sessão terminou.' e Recarregar, nada do cache fica visível", async () => {
  const p = await abrirSeguranca({ mfa: ON });
  await p.bt("Desativar").waitFor();
  p.s.statusErro = (r) => r.fulfill({ status: 401, json: { detail: "Not authenticated" }, headers: SESSAO });
  await p.page.evaluate(() => document.dispatchEvent(new Event("visibilitychange", { bubbles: true })));
  await p.painel.getByRole("alert").waitFor();
  const r = [await p.painel.getByRole("alert").innerText(), await p.bt("Recarregar").count(), await p.bt("Desativar").count(), (await p.painel.innerText()).includes("restantes")];
  await p.ctx.close();
  assert.deepEqual(r, ["Sua sessão terminou.", 1, 0, false]);
});

test("status carregando: 'Carregando…' e nenhuma ação até a resposta chegar", async () => {
  const [segura, solta] = portao();
  const p = await abrirSeguranca();
  p.s.statusSegura = segura;
  await p.page.reload();
  await p.painel.getByText("Carregando…").waitFor();
  const antes = [await p.bt("Ativar").count(), await p.bt("Desativar").count()];
  solta();
  await p.bt("Ativar").waitFor();
  await p.ctx.close();
  assert.deepEqual(antes, [0, 0]);
});

test("senha: o botão manda o link (POST com o CSRF em /settings/7/password-reset), 'Enviando…' em voo, a mensagem do servidor sai inline e um clique duplo = 1 POST", async () => {
  const p = await abrirSeguranca();
  const [segura, solta] = portao();
  p.s.reset = async (r) => { await segura; return r.fulfill({ json: { ok: true, message: MSG_RESET } }); };
  await p.bt("Resetar senha por e-mail").evaluate((b) => { b.click(); b.click(); }); // no MESMO tick: o `disabled` ainda não chegou ao DOM
  await p.painel.getByRole("button", { name: "Enviando…" }).waitFor();
  const emVoo = await p.painel.getByRole("button", { name: "Enviando…" }).isDisabled();
  solta();
  await p.painel.getByText(MSG_RESET).waitFor();
  const r = [await p.painel.getByRole("status").filter({ hasText: MSG_RESET }).count(), await p.bt("Resetar senha por e-mail").isEnabled()];
  await p.ctx.close();
  assert.equal(p.s.chamadas.reset.length, 1);
  assert.equal(p.s.chamadas.reset[0].csrf, "tok-123");
  assert.equal(p.s.chamadas.reset[0].metodo, "POST");
  assert.ok(emVoo);
  assert.deepEqual(r, [1, true]);
  assert.deepEqual(p.s.reqs.filter((q) => q.metodo === "POST").map((q) => new URL(q.url).pathname), ["/settings/7/password-reset"]);
});

const ERROS_RESET = [
  ["400 verbatim", JSON_ERRO(400, "Adicione um e-mail antes de resetar a senha."), "Adicione um e-mail antes de resetar a senha."],
  ["404 verbatim", JSON_ERRO(404, "Conta de e-mail não encontrada."), "Conta de e-mail não encontrada."],
  ["429 verbatim", JSON_ERRO(429, "Muitas tentativas. Aguarde alguns minutos e tente novamente."), "Muitas tentativas. Aguarde alguns minutos e tente novamente."],
  ["500 vira a reserva", JSON_ERRO(500, "Não foi possível enviar o e-mail de reset."), "Erro ao enviar reset"],
  ["rede", (r) => r.abort("internetdisconnected"), "Erro ao enviar reset"],
  ["403 (CSRF)", JSON_ERRO(403, "CSRF"), "Recarregue a página antes de salvar de novo."],
  ["401 de sessão", JSON_ERRO(401, "Not authenticated", SESSAO), "Sua sessão terminou."],
];
test("link da senha com erro: texto do servidor nos 4xx, reserva no resto; o botão volta a funcionar", async () => {
  const p = await abrirSeguranca();
  const vistos = [];
  for (const [nome, falha] of ERROS_RESET) {
    p.s.reset = falha;
    await p.bt("Resetar senha por e-mail").click();
    await p.painel.getByRole("alert").waitFor();
    vistos.push([nome, await p.painel.getByRole("alert").first().innerText(), await p.bt("Resetar senha por e-mail").isEnabled()]);
    p.s.reset = null;
  }
  await p.bt("Resetar senha por e-mail").click();
  await p.painel.getByText(MSG_RESET).waitFor();
  const depois = await p.painel.getByRole("alert").count();
  await p.ctx.close();
  assert.deepEqual(vistos, ERROS_RESET.map(([n, , t]) => [n, t, true]));
  assert.equal(depois, 0);
});

test("conta só-Google: 'Definir senha' no lugar do reset, valor '—' (Não definido); o mesmo POST manda o link", async () => {
  const p = await abrirSeguranca({ temSenha: false });
  await p.bt("Definir senha").waitFor();
  const r = await p.page.evaluate(() => { const dd = document.querySelector(".conta-seguranca dl > div dd"); return [dd.innerText.trim(), dd.querySelector(".sr-only").textContent]; });
  await p.bt("Definir senha").click();
  await p.painel.getByRole("status").filter({ hasText: MSG_RESET }).waitFor();
  await p.ctx.close();
  assert.deepEqual(r, ["—\nNão definido", "Não definido"]);
  assert.equal(p.s.chamadas.reset.length, 1);
});

for (const [rotulo, abrirPor] of [["Ativar", async () => {}], ["Desativar", (p) => { p.s.mfa = ON; }]]) {
  test(`só-Google clicando em '${rotulo}': diálogo 'Defina uma senha primeiro' SEM POST; 'Agora não' fecha; 'Definir senha' fecha e foca o botão da senha`, async () => {
    const p = await abrirSeguranca({ temSenha: false });
    await abrirPor(p);
    if (rotulo === "Desativar") await p.page.reload();
    await p.bt(rotulo).waitFor();
    await ate(p.page, () => p.s.reqs.some((q) => q.url.endsWith("/auth/me")), "o /auth/me chegou");
    await p.page.waitForTimeout(150);
    await p.bt(rotulo).click();
    await p.dialogo.waitFor();
    const r = [await p.dialogo.locator("h2").innerText(), (await p.dialogo.locator("p").allInnerTexts()).join("|"), await p.dialogo.locator("input").count()];
    await p.dialogo.getByRole("button", { name: "Agora não" }).click(); await p.fechado();
    await p.bt(rotulo).click();
    await p.dialogo.getByRole("button", { name: "Definir senha" }).click(); await p.fechado();
    const foco = await p.foco();
    await p.ctx.close();
    assert.deepEqual(r, ["Defina uma senha primeiro", "Sua conta ainda não tem senha, então nenhuma senha funciona aqui. Defina uma senha por e-mail e volte para concluir.|Já definiu a senha em outra aba? Atualize a página.", 0]);
    assert.equal(foco, "Definir senha");
    assert.deepEqual(p.s.reqs.filter((q) => q.metodo === "POST"), []); // nenhum POST (nem o do link: o diálogo só leva ao botão)
  });
}

test("/auth/me fora do ar (temSenha desconhecido): o formulário abre mesmo assim e o 409 do servidor sai verbatim (lado do servidor decide)", async () => {
  const p = await abrirSeguranca({ meRuim: true });
  await p.bt("Ativar").click();
  await p.senha.fill("qualquer");
  p.s.setup = JSON_ERRO(409, SEM_SENHA_SERVIDOR);
  await p.enviar.click();
  await p.alerta.waitFor();
  const r = await p.alerta.innerText();
  await p.ctx.close();
  assert.equal(r, SEM_SENHA_SERVIDOR);
  assert.equal(p.s.chamadas.setup.length, 1);
});
const SEM_SENHA_SERVIDOR = "Sua conta ainda não tem senha. Defina uma senha por e-mail.";
