/**
 * /painel#/conta/perfil, as bordas (o fluxo feliz mora em dashboard_v2_conta_perfil.test.mjs):
 *   · Salvar sem mudar não manda PATCH (nem regrava o telefone estrangeiro mostrado em máscara BR);
 *   · modal sem fechar com o PATCH em voo (Esc, Cancelar, Fechar, clique fora), nativo e sem <dialog>; o Esc repetido (o Chromium
 *     deixa de vetar o `cancel` no 3º) não fecha o <dialog> com o React montado; o PATCH que nunca responde estoura em 30 s;
 *   · `created_at` que não é texto, `user_id` ausente, volta do WhatsApp (foco da aba), /auth/me só no e-mail, e-mail inválido até o servidor;
 *   · Conectar só abre link https/whatsapp, e dois cliques no mesmo tick geram um pedido só.
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { navegador } from "./_guia.mjs";
import { JSON_ERRO, SESSAO, abrirPerfil, ate, portao } from "./_conta_perfil.mjs";

const browser = navegador();
void browser;

test("Salvar sem mudar fecha em silêncio, sem PATCH nem aviso; mudando, o PATCH sai (nome e telefone)", async () => {
  const sec = (extra) => ({ ...{ ok: true, user_id: 7, email: "ana.teste@exemplo.com", display_name: "Ana Teste", phone: "+5511987654321", created_at: null }, ...extra });
  const visto = [];
  // [campo, o que está no servidor, o que se digita (undefined = nada), esperado no PATCH (null = nenhum)]
  for (const [campo, dado, digita, corpo] of [
    ["nome", sec(), undefined, null],
    ["nome", sec({ display_name: " Ana Teste " }), "  Ana Teste", null],
    ["nome", sec({ display_name: null }), undefined, null],
    ["telefone", sec(), undefined, null],
    ["telefone", sec({ phone: "14155550123" }), undefined, null], // estrangeiro de 11 dígitos: a máscara BR mostra (14) 15555-0123
    ["nome", sec(), "Ana Maria", { display_name: "Ana Maria" }],
    ["telefone", sec({ phone: "14155550123" }), "(11) 98765-4321", { phone: "(11) 98765-4321" }],
  ]) {
    const p = await abrirPerfil({ sec: dado });
    await p.lapis(campo).click();
    if (digita !== undefined) await p.campo.fill(digita);
    await p.salvar.click(); await p.fechado();
    visto.push([await p.aviso.innerText() === "", await p.foco()]);
    await p.ctx.close();
    assert.deepEqual(p.s.patches.map((x) => x.corpo), corpo ? [corpo] : [], `${campo} ${digita}`);
  }
  assert.deepEqual(visto, [[true, "Alterar nome"], [true, "Alterar nome"], [true, "Alterar nome"], [true, "Alterar telefone"], [true, "Alterar telefone"],
    [false, "Alterar nome"], [false, "Alterar telefone"]]);
});

// O PATCH fica parado no portão; `desfecho` responde quando ele abrir.
for (const semDialog of [false, true]) {
  for (const [desfecho, resposta] of [["falha", JSON_ERRO(400, "Nome não aceito.")], ["sucesso", null]]) {
    test(`${semDialog ? "sem <dialog>" : "nativo"}: Esc, clique fora, Cancelar e Fechar NÃO fecham com o PATCH em voo; o resultado (${desfecho}) nunca fica engolido`, async () => {
      const p = await abrirPerfil({ semDialog });
      const [segura, solta] = portao();
      p.s.patch = async (r, corpo) => {
        await segura;
        if (resposta) return resposta(r);
        p.s.sec.display_name = corpo.display_name;
        return r.fulfill({ json: p.s.sec });
      };
      await p.lapis("nome").click();
      await p.campo.fill("Nome Novo");
      await p.page.waitForTimeout(500); // passada a janela em que o clique no fundo ainda não vale
      await p.salvar.click();
      await p.dialogo.getByRole("button", { name: "Salvando…" }).waitFor();
      await p.page.keyboard.press("Escape");
      await p.page.mouse.click(3, 3); // o fundo (::backdrop ou o scrim)
      const emVoo = [await p.dialogo.count(), await p.dialogo.getByRole("button", { name: "Cancelar" }).isDisabled(), await p.dialogo.getByRole("button", { name: "Fechar" }).isDisabled()];
      solta();
      if (resposta) {
        await p.alerta.waitFor();
        const fim = [await p.alerta.innerText(), await p.dialogo.count(), await p.campo.inputValue()];
        await p.page.keyboard.press("Escape"); await p.fechado(); // terminado o voo, o Esc volta a fechar
        assert.deepEqual(fim, ["Nome não aceito.", 1, "Nome Novo"]);
      } else {
        await p.fechado();
        assert.equal(await p.aviso.innerText(), "Nome atualizado");
        assert.equal((await p.valores())[0], "Nome Novo");
      }
      await p.ctx.close();
      assert.deepEqual(emVoo, [1, true, true]);
      assert.equal(p.s.patches.length, 1);
    });
  }
}

test("created_at que não é texto (123, true, {}): o painel não cai e a linha 'Desde' some", async () => {
  const lido = [];
  for (const created_at of [123, true, {}]) {
    const p = await abrirPerfil({ sec: { ok: true, user_id: 7, email: "a@b.co", display_name: "Ana", phone: null, created_at } });
    lido.push([(await p.valores()).length, p.erros.length]);
    await p.ctx.close();
  }
  assert.deepEqual(lido, [[4, 0], [4, 0], [4, 0]]);
});

test("sem user_id (ausente ou null) no dashboard-profile: nada vai a /settings/undefined|null e a tela mostra o erro, não 'Carregando…'", async () => {
  for (const opcao of [{ semUid: true }, { uid: null }]) {
    const p = await abrirPerfil(opcao);
    await p.page.waitForTimeout(500);
    const r = [await p.page.locator(".conta-perfil [role=alert]").innerText(), p.s.reqs.map((q) => new URL(q.url).pathname).filter((x) => /\/settings\/(undefined|null)\b|\/settings\/?$/.test(x))];
    await p.ctx.close();
    assert.deepEqual(r, ["Não deu para carregar seu perfil.\n\nTentar de novo", []], JSON.stringify(opcao));
  }
});

test("volta ao app (foco da aba): o WhatsApp vira 'Conectado' e o nome novo aparece, sem recarregar", async () => {
  const p = await abrirPerfil();
  p.s.linked = true;
  p.s.sec.display_name = "Nome vindo de fora";
  await p.page.evaluate(() => document.dispatchEvent(new Event("visibilitychange", { bubbles: true })));
  await p.page.locator(".conta-perfil dl > div").nth(3).getByText("Conectado").waitFor({ state: "attached" });
  await p.page.waitForFunction(() => document.querySelector(".conta-perfil dd")?.textContent === "Nome vindo de fora");
  await p.ctx.close();
});

test("sessão acaba com o perfil na tela (foco da aba devolve 401): 'Sua sessão terminou.' e Recarregar, nada do cache fica visível", async () => {
  const p = await abrirPerfil();
  p.s.getErro = (r) => r.fulfill({ status: 401, json: { detail: "Not authenticated" }, headers: SESSAO });
  await p.page.evaluate(() => document.dispatchEvent(new Event("visibilitychange", { bubbles: true })));
  await p.page.locator(".conta-perfil [role=alert]").waitFor();
  const r = [await p.page.locator(".conta-perfil [role=alert]").innerText(), await p.page.locator(".conta-perfil").innerText()];
  await p.ctx.close();
  assert.equal(r[0], "Sua sessão terminou.\n\nRecarregar");
  assert.ok(!r[1].includes("Ana Teste") && !r[1].includes("exemplo.com"), r[1]);
});

test("/auth/me só é pedido pelo modal do E-MAIL: abrir e salvar nome e telefone não o chamam", async () => {
  const p = await abrirPerfil();
  await p.lapis("nome").click(); await p.campo.fill("Outro Nome"); await p.salvar.click(); await p.fechado();
  await p.lapis("telefone").click(); await p.campo.fill("(21) 91234-5678"); await p.salvar.click(); await p.fechado();
  await p.page.waitForTimeout(300);
  const nome = p.s.meHits;
  await p.lapis("e-mail").click();
  await ate(p.page, () => p.s.meHits >= 1, "o modal do e-mail pede o /auth/me");
  await p.ctx.close();
  assert.equal(nome, 0);
});

test("e-mail digitado inválido não é barrado pelo navegador: chega ao servidor e o 400 sai verbatim", async () => {
  const p = await abrirPerfil();
  p.s.patch = JSON_ERRO(400, "E-mail inválido.");
  await p.lapis("e-mail").click();
  await p.campo.fill("isso-nao-e-email");
  await p.senha.fill("abc");
  const noValidate = await p.page.locator("dialog[open] form").evaluate((f) => f.noValidate);
  await p.salvar.click();
  await p.alerta.filter({ hasText: "E-mail inválido." }).waitFor();
  await p.ctx.close();
  assert.equal(noValidate, true);
  assert.deepEqual(p.s.patches.map((x) => x.corpo), [{ email: "isso-nao-e-email", current_password: "abc" }]);
});

// A troca de e-mail feita em outra aba não pode ser desfeita por um modal aberto com o campo intocado (invariante do B0a).
test("e-mail trocado por trás com o modal aberto e o campo intocado: sem campo de senha e Salvar fecha sem PATCH", async () => {
  const p = await abrirPerfil();
  await p.lapis("e-mail").click();
  const n = p.s.gets.length;
  p.s.sec.email = "trocado-em-outra-aba@exemplo.com";
  p.s.sse();
  await ate(p.page, () => p.s.gets.length > n, "o SSE refaz o GET de segurança");
  await p.page.waitForFunction(() => document.querySelector(".conta-perfil dl")?.textContent.includes("trocado-em-outra-aba@exemplo.com"));
  const r = [await p.campo.inputValue(), await p.senha.count()];
  await p.salvar.click();
  await p.fechado();
  await p.ctx.close();
  assert.deepEqual(r, ["ana.teste@exemplo.com", 0]);
  assert.deepEqual(p.s.patches, []);
});

for (const [nome, celular] of [["no computador", false], ["no celular", true]]) {
  test(`Conectar ${nome}: link javascript:/data: nunca é aberto nem atribuído; a janela vazia fecha e a mensagem aparece`, async () => {
    for (const ruim of ["javascript:window.__xss=1", "data:text/html,<script>window.__xss=1</script>", "JavaScript:window.__xss=1"]) {
      const p = await abrirPerfil({ celular, width: celular ? 390 : 1440, height: celular ? 844 : 900, link: (r) => r.fulfill({ json: { whatsapp_link: ruim } }) });
      const janelas = [];
      p.ctx.on("page", (j) => janelas.push(j));
      await p.page.getByRole("button", { name: "Conectar" }).click();
      await p.page.locator(".conta-perfil [role=alert]").filter({ hasText: "Não foi possível gerar seu link do WhatsApp agora." }).waitFor();
      await ate(p.page, () => janelas.every((j) => j.isClosed()), "a janela aberta no clique foi fechada");
      const xss = await p.page.evaluate(() => window.__xss);
      await p.ctx.close();
      assert.equal(xss, undefined, ruim);
      assert.equal(janelas.length, celular ? 0 : 1, ruim);
    }
  });
}

// O Chromium só deixa o `cancel` ser vetado 2 vezes seguidas sem gesto: do 3º Esc em diante ele fecha o <dialog> à força.
for (const [desfecho, resposta] of [["falha", JSON_ERRO(400, "Nome não aceito.")], ["sucesso", null]]) {
  test(`nativo: Esc repetido (6x) com o PATCH em voo não deixa o <dialog> fechado com o formulário montado (${desfecho})`, async () => {
    const p = await abrirPerfil();
    const [segura, solta] = portao();
    p.s.patch = async (r, corpo) => {
      await segura;
      if (resposta) return resposta(r);
      p.s.sec.display_name = corpo.display_name;
      return r.fulfill({ json: p.s.sec });
    };
    await p.lapis("nome").click();
    await p.campo.fill("Nome Novo");
    await p.page.waitForTimeout(500);
    await p.salvar.click();
    await p.dialogo.getByRole("button", { name: "Salvando…" }).waitFor();
    for (let i = 0; i < 6; i++) await p.page.keyboard.press("Escape"); // entre um Esc e o `close` o <dialog> pisca fechado: só o fim conta
    await p.page.waitForTimeout(150);
    const aberto = await p.dialogo.count();
    solta();
    let depois;
    if (resposta) {
      await p.alerta.waitFor(); // visível: dentro do <dialog> aberto
      depois = [await p.alerta.innerText(), await p.dialogo.count()];
      await p.dialogo.getByRole("button", { name: "Cancelar" }).click(); await p.fechado();
      await p.lapis("nome").click(); // o lápis reabre o modal normalmente
      depois.push(await p.dialogo.count(), await p.campo.inputValue());
    } else {
      await p.fechado();
      depois = [await p.foco(), await p.aviso.innerText()];
    }
    await p.ctx.close();
    assert.equal(aberto, 1);
    assert.deepEqual(depois, resposta ? ["Nome não aceito.", 1, 1, "Ana Teste"] : ["Alterar nome", "Nome atualizado"]);
  });
}

test("nativo: o <dialog> fechado por fora, sem escrita em voo, desmonta o formulário (o lápis reabre)", async () => {
  const p = await abrirPerfil();
  await p.lapis("nome").click();
  await p.dialogo.waitFor();
  await p.page.evaluate(() => document.querySelector("dialog[open]").close());
  await p.page.locator("dialog.lanc-form").waitFor({ state: "detached" });
  await p.lapis("nome").click();
  const reabriu = await p.dialogo.count();
  await p.ctx.close();
  assert.equal(reabriu, 1);
});

test("PATCH que nunca responde: estoura em 30 s, mantém o rascunho e libera o modal", async () => {
  const p = await abrirPerfil();
  const [segura, solta] = portao();
  p.s.patch = async () => { await segura; };
  // Sem esperar 30 s: o teste encurta o setTimeout de 30000 ms (e só ele) para 400 e registra o que o app pediu.
  await p.page.evaluate(() => { const st = window.setTimeout; window.__ms = []; window.setTimeout = (f, ms, ...a) => { if (ms === 30000) { window.__ms.push(ms); ms = 400; } return st(f, ms, ...a); }; });
  await p.lapis("nome").click();
  await p.campo.fill("Nome Novo");
  await p.salvar.click();
  await p.alerta.waitFor();
  const r = [await p.alerta.innerText(), await p.campo.inputValue(), await p.campo.isEnabled(), await p.page.evaluate(() => window.__ms)];
  await p.dialogo.getByRole("button", { name: "Cancelar" }).click(); await p.fechado(); // destravado: o Cancelar volta a fechar
  solta();
  await p.ctx.close();
  assert.deepEqual(r, ["Não foi possível salvar. Seu rascunho foi mantido.", "Nome Novo", true, [30000]]);
});

test("Conectar: dois cliques no MESMO tick geram um POST e uma janela só", async () => {
  const p = await abrirPerfil();
  const janelas = [];
  p.ctx.on("page", (j) => janelas.push(j));
  await p.page.getByRole("button", { name: "Conectar" }).evaluate((b) => { b.click(); b.click(); });
  await p.page.waitForTimeout(800); // o /auth/link-code demora 300 ms no servidor de mentira
  await p.ctx.close();
  assert.deepEqual([p.s.posts.length, janelas.length], [1, 1]);
});

// O regex do link (`/^(https|whatsapp):\/\//i`): cada caso mata uma mutação (sem `^`, sem `i`, `https?`). Bloqueado = o alerta.
test("Conectar só abre link https/whatsapp: o regex mede o início, a caixa e o esquema", async () => {
  const visto = [];
  for (const link of ["javascript:window.__xss=1;'https://x'", "HTTPS://wa.me/5511999999999", "http://wa.me/5511999999999", "whatsapp://send?text=oi"]) {
    const p = await abrirPerfil({ celular: true, width: 390, height: 844, link: (r) => r.fulfill({ json: { whatsapp_link: link } }) });
    await p.page.getByRole("button", { name: "Conectar" }).click();
    await p.page.waitForTimeout(900); // o /auth/link-code demora 300 ms no servidor de mentira
    visto.push([link, await p.page.locator(".conta-perfil [role=alert]").count() === 1, await p.page.evaluate(() => window.__xss)]);
    await p.ctx.close();
  }
  assert.deepEqual(visto, [["javascript:window.__xss=1;'https://x'", true, undefined], ["HTTPS://wa.me/5511999999999", false, undefined],
    ["http://wa.me/5511999999999", true, undefined], ["whatsapp://send?text=oi", false, undefined]]);
});
