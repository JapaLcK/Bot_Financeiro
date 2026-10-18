/**
 * /painel#/conta/perfil (conta/Perfil.tsx, conta/ContatoForm.tsx, conta/api.ts): a página de verdade, no lugar da ponte.
 *   · só ícone e valor (a palavra de cada linha é sr-only); "Desde {mês} de {ano}" no fuso do app; telefone formatado;
 *   · a leitura é GET /settings/{uid}/security (SEM /api/v2, uid do dashboard-profile); a escrita, PATCH .../security/contact com o CSRF;
 *   · edição em modal; e-mail trocado pede "Senha atual" (só conta com senha; igual ao atual não pede); nome vazio remove o nome;
 *   · "Senha incorreta." (401 SEM WWW-Authenticate) nunca vira "Sua sessão terminou."; os textos do servidor saem como chegam;
 *   · em voo (duplo envio = 1 PATCH), rascunho vivo contra o SSE, GET velho não repinta, senha nunca em storage/URL e sempre limpa;
 *   · Conectar WhatsApp (janela aberta no clique no computador, navegação no celular).
 * O /painel só tem o tema escuro: não há par claro/escuro a medir.
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { RAIZ } from "./_painel.mjs";
import { abrir, navegador } from "./_guia.mjs";
import { JSON_ERRO, SEC, SENHA_VAZIA, SESSAO, WA_OK, abrirPerfil, ate, portao, servidor, vazou } from "./_conta_perfil.mjs";

const browser = navegador();
void browser;

test("exibição em 1440×900, 390×844 e 320×640: só ícone e valor, palavras só para leitor de tela, sem rolagem lateral, alvos de 44px", async () => {
  for (const [width, height] of [[1440, 900], [390, 844], [320, 640]]) {
    const p = await abrirPerfil({ width, height });
    const r = await p.page.evaluate(() => {
      const linhas = [...document.querySelectorAll(".conta-perfil dl > div")];
      const painel = document.querySelector(".conta-perfil").getBoundingClientRect();
      const alvos = [...document.querySelectorAll(".conta-perfil .icon-btn, .conta-perfil .btn")].map((e) => e.getBoundingClientRect());
      return {
        rotulos: linhas.map((l) => l.querySelector("dt .sr-only").textContent),
        icones: linhas.map((l) => l.querySelector("dt i").className),
        visivel: linhas.map((l) => [...l.querySelectorAll("dd")].map((d) => d.innerText.replace(/\s+/g, " ").trim()).join("|")),
        lapis: [...document.querySelectorAll(".conta-perfil .icon-btn")].map((b) => b.getAttribute("aria-label")),
        lateral: document.documentElement.scrollWidth - innerWidth,
        pequenos: alvos.filter((a) => a.width < 43.5 || a.height < 43.5).length,
        fora: alvos.filter((a) => a.left < painel.left || a.right > painel.right + 0.5).length,
        titulo: document.querySelector("#page-title").textContent, aba: document.title,
      };
    });
    await p.page.screenshot({ path: join(process.env.TMPDIR ?? "/tmp", `perfil-${width}.png`) });
    await p.ctx.close();
    assert.deepEqual(r.rotulos, ["Nome", "E-mail", "Telefone", "WhatsApp", "Membro desde"], `${width}`);
    assert.deepEqual(r.icones, ["ph ph-user", "ph ph-envelope", "ph ph-phone", "ph ph-whatsapp-logo", "ph ph-calendar-blank"]);
    assert.deepEqual(r.visivel, ["Ana Teste", "ana.teste@exemplo.com", "(11) 98765-4321", "Conectar", "Desde outubro de 2025"].map((v, i) => i < 3 ? v + "|" : v));
    assert.deepEqual(r.lapis, ["Alterar nome", "Alterar e-mail", "Alterar telefone"]);
    assert.deepEqual({ lateral: r.lateral, pequenos: r.pequenos, fora: r.fora, titulo: r.titulo, aba: r.aba }, { lateral: 0, pequenos: 0, fora: 0, titulo: "Perfil", aba: "PigBank · Perfil" }, `${width}`);
  }
});

test("a leitura vai a /settings/7/security SEM /api/v2 (uid do dashboard-profile); nada de dado pessoal em storage", async () => {
  const p = await abrirPerfil();
  const guardado = await p.page.evaluate(() => [localStorage, sessionStorage].flatMap((st) => Object.keys(st).map((k) => k + "=" + st.getItem(k))).join("\n"));
  await p.ctx.close();
  assert.deepEqual([...new Set(p.s.gets)], ["/settings/7/security"]);
  assert.ok(!/Ana Teste|ana\.teste@exemplo\.com|987654321/.test(guardado), guardado);
});

// §0.7: a cópia de formatPhoneBR (conta/api.ts) tem de dar o que o settings.html dá.
const FONE = [["+5511987654321", "(11) 98765-4321"], ["11987654321", "(11) 98765-4321"], ["1133334444", "(11) 3333-4444"], ["+551133334444", "(11) 3333-4444"], ["12345", "12345"], ["abc", "abc"], ["+5511", "+5511"]];
test("telefone: tabela de 10, 11 e 13 dígitos e lixo, igual à formatPhoneBR do settings.html", async () => {
  const html = readFileSync(join(RAIZ, "frontend", "settings.html"), "utf8");
  const formatPhoneBR = new Function(`${html.match(/function formatPhoneBR\(value\) \{[\s\S]*?\n\}/)[0]}; return formatPhoneBR;`)();
  const p = await abrirPerfil();
  const vistos = [];
  for (const [entrada, esperado] of FONE) {
    assert.equal(formatPhoneBR(entrada), esperado, `settings.html: ${entrada}`);
    p.s.sec.phone = entrada;
    await p.page.reload();
    await p.page.locator(".conta-perfil dl").waitFor();
    vistos.push((await p.valores())[2]);
  }
  p.s.sec.phone = null;
  await p.page.reload();
  await p.page.locator(".conta-perfil dl").waitFor();
  const vazio = await p.page.locator(".conta-perfil dl > div").nth(2).innerText();
  await p.ctx.close();
  assert.deepEqual(vistos, FONE.map((f) => f[1]));
  assert.match(vazio, /—/);
});

test("Desde: mês e ano no fuso do app (01:00Z do dia 1 ainda é o mês anterior em São Paulo); sem data a linha some", async () => {
  const p = await abrirPerfil();
  const lido = [];
  for (const created_at of ["2025-10-01T01:00:00+00:00", "2025-10-01T03:00:00.999999+00:00", "2026-01-01T02:59:59+00:00", null]) {
    p.s.sec.created_at = created_at;
    await p.page.reload();
    await p.page.locator(".conta-perfil dl").waitFor();
    lido.push((await p.valores()).slice(4).join(""));
  }
  await p.ctx.close();
  assert.deepEqual(lido, ["Desde setembro de 2025", "Desde outubro de 2025", "Desde dezembro de 2025", ""]);
});

test("valores vazios: '—' na tela e 'Não definido' para o leitor de tela", async () => {
  const p = await abrirPerfil({ sec: { ...SEC(), display_name: null, phone: null } });
  const r = await p.page.evaluate(() => [...document.querySelectorAll(".conta-perfil dl > div")].slice(0, 3).map((l) => [l.querySelector("dd:nth-of-type(1) [aria-hidden]")?.textContent ?? null, l.querySelector("dd:nth-of-type(1) .sr-only")?.textContent ?? null]));
  await p.ctx.close();
  assert.deepEqual(r, [["—", "Não definido"], [null, null], ["—", "Não definido"]]);
});

test("display_name, e-mail e telefone que não são texto (123, {}, 5) não derrubam o painel: linhas vazias e o modal abre", async () => {
  const p = await abrirPerfil({ sec: { ...SEC(), display_name: 123, email: {}, phone: 5 } });
  const antes = await p.valores();
  await p.lapis("e-mail").click();
  const r = [await p.campo.inputValue(), await p.page.locator("dialog[open]").count()];
  await p.ctx.close();
  assert.deepEqual(antes.slice(0, 3), ["— Não definido", "— Não definido", "— Não definido"]);
  assert.deepEqual(r, ["", 1]);
  assert.deepEqual(p.erros, []);
});

for (const width of [320, 390]) {
  test(`${width}: nome e e-mail longos, sem ponto de quebra, não criam rolagem lateral (nem com o modal aberto)`, async () => {
    const p = await abrirPerfil({ width, height: 700, sec: { ...SEC(), display_name: "W".repeat(50), email: "joao.da.silva.pereira.de.souza.neto@empresa-corporativa-grande-demais-do-brasil.com.br" } });
    const lateral = [await p.page.evaluate(() => document.documentElement.scrollWidth - innerWidth)];
    await p.lapis("e-mail").click();
    lateral.push(await p.page.evaluate(() => document.documentElement.scrollWidth - innerWidth), await p.page.evaluate(() => { const d = document.querySelector("dialog[open]").getBoundingClientRect(); return Math.round(Math.max(0, d.right - innerWidth, -d.left)); }));
    await p.ctx.close();
    assert.deepEqual(lateral, [0, 0, 0]);
  });
}

test("nome: PATCH com o CSRF e só {display_name}, a linha pinta a RESPOSTA, anuncia 'Nome atualizado' e o foco volta ao lápis; vazio remove o nome", async () => {
  const p = await abrirPerfil();
  p.s.patch = (r, corpo) => { p.s.sec.display_name = corpo.display_name ? corpo.display_name + " (do servidor)" : null; return r.fulfill({ json: p.s.sec }); };
  await p.lapis("nome").click();
  assert.equal(await p.campo.inputValue(), "Ana Teste");
  assert.deepEqual(await p.campo.evaluate((e) => [e.type, e.autocomplete, e.getAttribute("maxlength")]), ["text", "name", "50"]);
  await p.campo.fill("Ana Maria");
  await p.salvar.click(); await p.fechado();
  const depois = [(await p.valores())[0], await p.aviso.innerText(), await p.foco()];
  await p.lapis("nome").click();
  await p.campo.fill("   ");
  await p.salvar.click(); await p.fechado();
  const removido = [(await p.valores())[0], await p.aviso.innerText()];
  await p.ctx.close();
  assert.deepEqual(p.s.patches, [
    { metodo: "PATCH", url: "/settings/7/security/contact", csrf: "tok-123", corpo: { display_name: "Ana Maria" } },
    { metodo: "PATCH", url: "/settings/7/security/contact", csrf: "tok-123", corpo: { display_name: "" } },
  ]);
  assert.deepEqual(depois, ["Ana Maria (do servidor)", "Nome atualizado", "Alterar nome"]);
  assert.deepEqual(removido, ["— Não definido", "Nome removido"]);
});

test("telefone: corpo {phone}, atributos do campo, placeholder só com o campo vazio, 'Telefone atualizado'", async () => {
  const p = await abrirPerfil();
  await p.lapis("telefone").click();
  const atributos = await p.campo.evaluate((e) => [e.type, e.autocomplete, e.getAttribute("aria-label"), e.placeholder, e.value]);
  await p.campo.fill("");
  const placeholder = await p.campo.getAttribute("placeholder");
  await p.campo.fill("(21) 91234-5678");
  await p.salvar.click(); await p.fechado();
  const r = [(await p.valores())[2], await p.aviso.innerText()];
  await p.ctx.close();
  assert.deepEqual(atributos, ["tel", "tel", "Telefone", "", "(11) 98765-4321"]);
  assert.equal(placeholder, "(11) 99999-9999");
  assert.deepEqual(p.s.patches.map((x) => x.corpo), [{ phone: "(21) 91234-5678" }]);
  assert.deepEqual(r, ["(21) 91234-5678", "Telefone atualizado"]);
});

test("e-mail com senha: o campo só aparece quando o e-mail muda; o corpo leva current_password; sem senha não sai PATCH", async () => {
  const p = await abrirPerfil();
  await p.lapis("e-mail").click();
  const atributos = await p.campo.evaluate((e) => [e.type, e.autocomplete]);
  const antes = await p.senha.count();
  await p.campo.fill("nova@exemplo.com");
  await p.senha.waitFor();
  const senhaAttrs = await p.senha.evaluate((e) => [e.type, e.autocomplete, e.getAttribute("aria-label"), e.placeholder]);
  await p.salvar.click(); // senha vazia
  await p.alerta.waitFor();
  const barrada = { texto: await p.alerta.innerText(), patches: p.s.patches.length, foco: await p.foco() };
  await p.senha.fill("segredo-123");
  await p.salvar.click(); await p.fechado();
  assert.deepEqual(vazou(p.s, "segredo-123"), []);
  const r = [(await p.valores())[1], await p.aviso.innerText()];
  await p.ctx.close();
  assert.deepEqual([atributos, antes, senhaAttrs], [["email", "email"], 0, ["password", "current-password", "Senha atual", "Senha atual"]]);
  assert.deepEqual(barrada, { texto: SENHA_VAZIA, patches: 0, foco: "senha" });
  assert.deepEqual(p.s.patches.map((x) => x.corpo), [{ email: "nova@exemplo.com", current_password: "segredo-123" }]);
  assert.deepEqual(r, ["nova@exemplo.com", "E-mail atualizado"]);
});

test("e-mail igual ao do servidor (outra caixa, espaços): sem campo de senha e Salvar fecha em silêncio, sem PATCH", async () => {
  const p = await abrirPerfil();
  await p.lapis("e-mail").click();
  await p.campo.fill("  ANA.TESTE@exemplo.com ");
  await p.page.waitForTimeout(150);
  const campoSenha = await p.senha.count();
  await p.salvar.click(); await p.fechado();
  const r = [await p.aviso.innerText(), await p.foco()];
  await p.ctx.close();
  assert.equal(campoSenha, 0);
  assert.deepEqual(p.s.patches, []);
  assert.deepEqual(r, ["", "Alterar e-mail"]);
});

test("conta só-Google (has_password:false): sem campo de senha; o 409 mostra o texto do servidor e o link 'Definir senha' para /settings", async () => {
  const TEXTO = "Sua conta ainda não tem senha. Defina uma senha para continuar.";
  const p = await abrirPerfil({ me: { has_password: false } });
  p.s.patch = JSON_ERRO(409, TEXTO);
  await p.lapis("e-mail").click();
  await p.campo.fill("nova@exemplo.com");
  await p.senha.waitFor({ state: "detached" }); // aparece (lado restritivo) enquanto o /auth/me não chegou, e some
  await p.salvar.click();
  await p.alerta.waitFor();
  const r = { texto: await p.alerta.innerText(), link: await p.dialogo.getByRole("link", { name: "Definir senha" }).getAttribute("href"), campo: await p.senha.count(), corpo: p.s.patches.map((x) => x.corpo) };
  await p.ctx.close();
  assert.deepEqual(r, { texto: TEXTO, link: "/settings", campo: 0, corpo: [{ email: "nova@exemplo.com" }] });
});

test("/auth/me fora do ar (500): o campo de senha aparece mesmo assim (lado restritivo)", async () => {
  const p = await abrirPerfil({ me: 500 });
  await p.lapis("e-mail").click();
  await p.campo.fill("nova@exemplo.com");
  await p.senha.waitFor();
  await ate(p.page, () => p.s.meHits >= 1, "o /auth/me nem foi pedido");
  await p.page.waitForTimeout(400);
  const visivel = await p.senha.count();
  await p.ctx.close();
  assert.equal(visivel, 1);
});

test("401 'Senha incorreta.' (sem WWW-Authenticate): mostra o texto do servidor, nunca 'Sua sessão terminou.', limpa a senha, mantém o rascunho, foca a senha, sem /auth/refresh nem senha em storage ou URL", async () => {
  const p = await abrirPerfil();
  p.s.patch = JSON_ERRO(401, "Senha incorreta.");
  await p.lapis("e-mail").click();
  await p.campo.fill("nova@exemplo.com");
  await p.senha.fill("senha-que-nao-pode-vazar");
  await p.salvar.click();
  await p.alerta.waitFor();
  await p.page.waitForTimeout(200);
  const r = await p.page.evaluate(() => ({
    corpo: document.body.innerText.includes("Sua sessão terminou"),
    senha: document.querySelector("dialog[open] input[name=senha]").value, email: document.querySelector("dialog[open] input[name=valor]").value,
    foco: document.activeElement.name, recarregar: [...document.querySelectorAll("dialog[open] button")].some((b) => b.textContent === "Recarregar"),
    guardado: [localStorage, sessionStorage].flatMap((st) => Object.keys(st).map((k) => st.getItem(k))).join("|") + location.href,
  }));
  const texto = await p.alerta.innerText();
  await p.ctx.close();
  assert.equal(texto, "Senha incorreta.");
  assert.deepEqual({ ...r, guardado: r.guardado.includes("senha-que-nao-pode-vazar") }, { corpo: false, senha: "", email: "nova@exemplo.com", foco: "senha", recarregar: false, guardado: false });
  assert.equal(p.s.refreshes, 0);
  assert.equal(p.s.patches.length, 1);
  // A senha só viaja no corpo do PATCH de contato: nem na URL (nem na query), nem no corpo de outro pedido. O controle
  // positivo prova que a medição enxerga o corpo (o PATCH TEM a senha).
  assert.ok(p.s.reqs.some((q) => q.metodo === "PATCH" && q.corpo.includes("senha-que-nao-pode-vazar")));
  assert.deepEqual(vazou(p.s, "senha-que-nao-pode-vazar"), []);
});

test("401 de SESSÃO (com WWW-Authenticate, refresh falho): 'Sua sessão terminou.' e Recarregar, sem o texto do servidor", async () => {
  const p = await abrirPerfil();
  p.s.patch = JSON_ERRO(401, "Not authenticated", SESSAO);
  await p.lapis("nome").click();
  await p.campo.fill("Outro Nome");
  await p.salvar.click();
  await p.alerta.waitFor();
  const r = [await p.alerta.innerText(), await p.dialogo.getByRole("button", { name: "Recarregar" }).count(), p.s.refreshes];
  await p.ctx.close();
  assert.deepEqual(r, ["Sua sessão terminou.", 1, 1]);
});

const ERROS = [
  ["400", JSON_ERRO(400, "E-mail inválido."), "E-mail inválido."],
  ["409 contato em uso", JSON_ERRO(409, "Este e-mail ou telefone já está em uso."), "Este e-mail ou telefone já está em uso."],
  ["429", JSON_ERRO(429, "Muitas tentativas. Aguarde alguns minutos e tente novamente."), "Muitas tentativas. Aguarde alguns minutos e tente novamente."],
  ["403 (CSRF)", JSON_ERRO(403, "CSRF"), "Recarregue a página antes de salvar de novo."],
  ["422 (lista do Pydantic)", (r) => r.fulfill({ status: 422, json: { detail: [{ loc: ["body"], msg: "x" }] } }), "Não foi possível salvar. Seu rascunho foi mantido."],
  ["500", (r) => r.fulfill({ status: 500, body: "<html>erro</html>" }), "Não foi possível salvar. Seu rascunho foi mantido."],
  ["rede", (r) => r.abort("internetdisconnected"), "Não foi possível salvar. Seu rascunho foi mantido."],
];
test("erros do PATCH em role=alert: 400/409/429 verbatim, 403 e 422/5xx/rede com o texto aprovado; rascunho mantido e o foco volta ao campo", async () => {
  const p = await abrirPerfil();
  const vistos = [];
  for (const [nome, resposta, esperado] of ERROS) {
    p.s.patch = resposta;
    const antes = p.s.gets.length;
    await p.lapis("e-mail").click();
    await p.campo.fill("rascunho@exemplo.com");
    await p.senha.fill("abc");
    await p.salvar.click();
    await p.page.locator("dialog[open] [role=alert]").filter({ hasText: esperado }).waitFor();
    vistos.push([nome, await p.alerta.innerText(), await p.campo.inputValue(), await p.senha.inputValue(), await p.foco(), await p.dialogo.getByRole("link").count()]);
    if (nome.startsWith("500")) { await p.page.waitForTimeout(400); vistos.push(["500 releu", p.s.gets.length > antes]); }
    await p.dialogo.getByRole("button", { name: "Cancelar" }).click(); await p.fechado();
  }
  await p.ctx.close();
  assert.deepEqual(vistos.filter((v) => v.length === 6).map((v) => [v[0], v[1] === ERROS.find((e) => e[0] === v[0])[2], v[2], v[3], v[4], v[5]]),
    ERROS.map(([nome]) => [nome, true, "rascunho@exemplo.com", "", "valor", 0]));
  assert.deepEqual(vistos.filter((v) => v.length === 2), [["500 releu", true]]);
});

test("em voo: duplo envio no mesmo tick e duplo clique = 1 PATCH; campos desativados, botão 'Salvando…'", async () => {
  const p = await abrirPerfil();
  const [segura, solta] = portao();
  p.s.patch = async (r) => { await segura; return r.fulfill({ json: p.s.sec }); };
  await p.lapis("nome").click();
  await p.campo.fill("Nome Novo");
  await p.page.evaluate(() => { const f = document.querySelector("dialog[open] form"); f.requestSubmit(); f.requestSubmit(); });
  await p.dialogo.getByRole("button", { name: "Salvando…" }).waitFor();
  await p.dialogo.getByRole("button", { name: "Salvando…" }).dblclick({ force: true }).catch(() => {});
  const r = [await p.campo.isDisabled(), await p.salvar.isDisabled(), p.s.patches.length];
  solta(); await p.fechado();
  await p.ctx.close();
  assert.deepEqual(r, [true, true, 1]);
  assert.equal(p.s.patches.length, 1);
});

test("rascunho vivo: um aviso do SSE atualiza a linha por trás, nunca o campo; e-mail e senha digitados ficam", async () => {
  const p = await abrirPerfil();
  await p.lapis("nome").click();
  await p.campo.fill("Meu rascunho");
  const antes = p.s.gets.length;
  p.s.sec.display_name = "Nome vindo do servidor";
  p.s.sse();
  await ate(p.page, () => p.s.gets.length > antes, "o GET disparado pelo aviso do SSE");
  await p.page.waitForFunction(() => document.querySelector(".conta-perfil dd")?.textContent === "Nome vindo do servidor");
  const r = [await p.campo.inputValue(), await p.page.locator(".conta-perfil dd").first().innerText()];
  await p.ctx.close();
  assert.deepEqual(r, ["Meu rascunho", "Nome vindo do servidor"]);
});

test("GET lento iniciado antes do PATCH não repinta o valor velho depois do salvar", async () => {
  const p = await abrirPerfil();
  const [segura, solta] = portao();
  p.s.hold = segura;
  p.s.sse(); // a conexão e o aviso disparam GET que ficam parados, com a foto de ANTES
  await ate(p.page, () => p.s.gets.length >= 2, "pelo menos 1 GET lento parado (a conexão e o aviso do SSE disparam 2; um cancela o outro, então só 1 é certo)");
  await p.lapis("nome").click();
  await p.campo.fill("Nome Salvo");
  p.s.hold = null; // o PATCH e as leituras depois dele passam; só os GET velhos seguem parados
  await p.salvar.click(); await p.fechado();
  solta();
  await p.page.waitForTimeout(500);
  const r = (await p.valores())[0];
  await p.ctx.close();
  assert.equal(r, "Nome Salvo");
});

test("WhatsApp: com whatsapp_linked o ícone verde 'Conectado' (sem botão); sem, o botão Conectar", async () => {
  const ligado = await abrirPerfil({ linked: true });
  const a = await ligado.page.evaluate(() => { const l = document.querySelectorAll(".conta-perfil dl > div")[3]; const i = l.querySelector("dd i"); return { sr: l.querySelector("dd .sr-only").textContent, cor: getComputedStyle(i).color, gain: getComputedStyle(document.documentElement).getPropertyValue("--gain").trim(), botoes: l.querySelectorAll("button").length, classe: i.className }; });
  await ligado.ctx.close();
  assert.equal(a.sr, "Conectado"); assert.equal(a.botoes, 0); assert.equal(a.classe, "ph ph-check-circle perfil-ok");
  assert.equal(a.cor, "rgb(61, 220, 151)"); assert.equal(a.gain.toLowerCase(), "#3ddc97");
});

test("Conectar no computador: a janela abre no clique (antes da resposta), recebe o link depois e perde o opener; POST com CSRF", async () => {
  const p = await abrirPerfil();
  const [popup] = await Promise.all([p.page.waitForEvent("popup"), p.page.getByRole("button", { name: "Conectar" }).click()]);
  const inicial = popup.url();
  await popup.waitForURL(`${WA_OK}`);
  const opener = await popup.evaluate(() => window.opener);
  await p.ctx.close();
  assert.equal(inicial, "about:blank");
  assert.equal(opener, null);
  assert.deepEqual(p.s.posts, [{ metodo: "POST", csrf: "tok-123" }]);
});

test("Conectar no celular: navega a própria aba para o link, sem janela nova", async () => {
  const p = await abrirPerfil({ width: 390, height: 844, celular: true });
  let abriu = false;
  p.ctx.on("page", () => { abriu = true; });
  await p.page.getByRole("button", { name: "Conectar" }).click();
  await p.page.waitForURL(`${WA_OK}`);
  await p.ctx.close();
  assert.equal(abriu, false);
});

for (const [nome, link, esperado] of [
  ["link vazio", (r) => r.fulfill({ json: {} }), "WhatsApp ainda não configurado neste ambiente."],
  ["429", JSON_ERRO(429, "Muitas tentativas. Aguarde alguns minutos e tente novamente."), "Muitas tentativas. Aguarde alguns minutos e tente novamente."],
  ["500", (r) => r.fulfill({ status: 500, body: "x" }), "Não foi possível gerar seu link do WhatsApp agora."],
]) {
  test(`Conectar com ${nome}: a mensagem em role=alert e a janela vazia é fechada`, async () => {
    const p = await abrirPerfil({ link });
    const [popup] = await Promise.all([p.page.waitForEvent("popup"), p.page.getByRole("button", { name: "Conectar" }).click()]);
    await p.page.locator(".conta-perfil [role=alert]").filter({ hasText: esperado }).waitFor();
    for (let t = 0; t < 40 && !popup.isClosed(); t++) await p.page.waitForTimeout(50);
    const r = [await p.page.locator(".conta-perfil [role=alert]").innerText(), popup.isClosed(), await p.page.getByRole("button", { name: "Conectar" }).isEnabled()];
    await p.ctx.close();
    assert.deepEqual(r, [esperado, true, true]);
  });
}

test("a11y: o modal tem nome, os campos são rotulados, Cancelar e Esc devolvem o foco ao lápis; 'Fechar' fecha", async () => {
  const p = await abrirPerfil();
  const r = [];
  for (const [chave, titulo] of [["nome", "Nome"], ["e-mail", "E-mail"], ["telefone", "Telefone"]]) {
    await p.lapis(chave).click();
    r.push([await p.page.getByRole("dialog", { name: titulo }).count(), await p.page.getByRole("textbox", { name: titulo }).count(), await p.foco()]);
    await p.dialogo.getByRole("button", { name: "Cancelar" }).click(); await p.fechado();
    r.push(await p.foco());
  }
  await p.lapis("nome").click();
  await p.page.keyboard.press("Escape"); await p.fechado();
  r.push(await p.foco());
  await p.lapis("telefone").click();
  await p.dialogo.getByRole("button", { name: "Fechar" }).click(); await p.fechado();
  r.push(await p.foco(), await p.aviso.innerText(), await p.page.locator(".conta-perfil [role=status]").count());
  await p.ctx.close();
  assert.deepEqual(r, [[1, 1, "valor"], "Alterar nome", [1, 1, "valor"], "Alterar e-mail", [1, 1, "valor"], "Alterar telefone", "Alterar nome", "Alterar telefone", "", 1]);
});

test("Safari 14 (sem <dialog>): o mesmo modal abre, salva e devolve o foco ao lápis", async () => {
  const p = await abrirPerfil({ semDialog: true });
  await p.lapis("nome").click();
  await p.campo.fill("Sem Dialog");
  await p.salvar.click(); await p.fechado();
  const r = [(await p.valores())[0], await p.foco()];
  await p.ctx.close();
  assert.deepEqual(r, ["Sem Dialog", "Alterar nome"]);
});

test("carga com erro: 'Não deu para carregar seu perfil.' e 'Tentar de novo' recupera; 401 de sessão na carga: 'Sua sessão terminou.' + Recarregar", async () => {
  const s = servidor();
  s.getErro = (r) => r.fulfill({ status: 404, json: { detail: "x" } });
  const antes = (ctx) => s.antes(ctx);
  const { ctx, page } = await abrir({ guia: "concluido", rota: "/conta/perfil", antes });
  await page.locator(".conta-perfil [role=alert]").waitFor();
  const erro = await page.locator(".conta-perfil [role=alert]").innerText();
  s.getErro = null;
  await page.getByRole("button", { name: "Tentar de novo" }).click();
  await page.locator(".conta-perfil dl").waitFor();
  const dado = (await page.locator(".conta-perfil dd").first().innerText());
  s.getErro = (r) => r.fulfill({ status: 401, json: { detail: "Not authenticated" }, headers: SESSAO });
  await page.reload();
  await page.locator(".conta-perfil [role=alert]").waitFor();
  const sessao = [await page.locator(".conta-perfil [role=alert]").innerText(), await page.getByRole("button", { name: "Recarregar" }).count()];
  await ctx.close();
  assert.equal(erro, "Não deu para carregar seu perfil.\n\nTentar de novo");
  assert.equal(dado, "Ana Teste");
  assert.deepEqual(sessao, ["Sua sessão terminou.\n\nRecarregar", 1]);
});
