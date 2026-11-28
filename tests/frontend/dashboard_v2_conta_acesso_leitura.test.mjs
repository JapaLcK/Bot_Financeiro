/**
 * /painel#/conta/configuracoes (conta/AcessoLeitura.tsx, conta/api.ts): o card "Acesso de visualização" da Segurança, atrás do freio `acesso_leitura` do /me.
 *   · sem o freio (ou ausente): sem credencial o card não existe; com credencial ativa só o usuário e "Desativar" (o dono sempre lê, GET e DELETE são liberados) (o leitor, que não chega a esta rota no painel, é coberto no jest);
 *     com o freio: desativado = "Criar acesso"; ativo = usuário, "Trocar senha", "Desativar";
 *   · criar: o código só sai no clique em "Enviar código", o PUT leva usuário, senha e código; a senha nunca volta à tela; trocar e desativar (DELETE com corpo {codigo});
 *   · cada recusa do servidor tem texto próprio; sem rolagem lateral em 1440×900 e 390×844.
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { join } from "node:path";
import { abrir, navegador } from "./_guia.mjs";
import { RESPOSTAS } from "./_painel.mjs";
import { servidor } from "./_conta_seguranca.mjs";

const browser = navegador();
void browser;
const SENHA = "senha-de-leitura-1";

async function abrirLeitura({ freio = true, estado = { ativo: false, usuario: null }, width = 1440, height = 900 } = {}) {
  const s = servidor(), L = { estado, gets: 0, codigos: 0, puts: [], dels: [], put: null, del: null, codigo: null };
  const json = (r, status, corpo) => r.fulfill({ status, json: corpo });
  const p = await abrir({ guia: "concluido", width, height, rota: "/conta/configuracoes", antes: async (ctx) => {
    await s.antes(ctx);
    await ctx.route((u) => u.pathname === "/api/v2/me", (r) => json(r, 200, { ...RESPOSTAS.me.pro, ...(freio === "ausente" ? {} : { acesso_leitura: freio }) }));
    await ctx.route((u) => u.pathname === "/api/v2/conta/leitura/codigo", (r) => { L.codigos++; return L.codigo ? L.codigo(r) : json(r, 200, { ok: true }); });
    await ctx.route((u) => u.pathname === "/api/v2/conta/leitura", (r) => {
      const q = r.request(), corpo = q.postDataJSON() ?? {};
      if (q.method() === "GET") { L.gets++; return json(r, 200, L.estado); }
      const rec = { ...corpo, csrf: q.headers()["x-csrf-token"], tipo: q.headers()["content-type"] };
      if (q.method() === "PUT") { L.puts.push(rec); if (L.put) return L.put(r, corpo); L.estado = { ativo: true, usuario: corpo.usuario }; return json(r, 200, L.estado); }
      L.dels.push(rec);
      if (L.del) return L.del(r, corpo);
      L.estado = { ativo: false, usuario: null };
      return json(r, 200, L.estado);
    });
  } });
  p.page.setDefaultTimeout(15000);
  await p.page.locator(".conta-seguranca").first().waitFor({ timeout: 12000 });
  const card = p.page.locator("section[aria-labelledby=leit-t]");
  const dlg = p.page.locator("dialog[open]");
  const bt = (n) => p.page.getByRole("button", { name: n, exact: true });
  return { ...p, s, L, card, dlg, bt, alerta: dlg.locator("[role=alert]"), lateral: () => p.page.evaluate(() => document.documentElement.scrollWidth - innerWidth) };
}
async function preenche(p, { usuario, senha = SENHA, confirma = senha }) {
  if (usuario !== undefined) await p.dlg.locator("input[name=usuario]").fill(usuario);
  await p.dlg.locator("input[name=senha]").fill(senha);
  await p.dlg.locator("input[name=confirma]").fill(confirma);
}
const pedeCodigo = async (p) => { await p.bt("Enviar código para meu e-mail").click(); await p.dlg.getByText("Código enviado. Confira seu e-mail.").waitFor(); };

test("fora do freio: sem credencial o card não existe; ativa, só usuário e Desativar (que funciona); com o freio, desativado mostra só Criar acesso", async () => {
  for (const freio of [false, "ausente"]) {
    const p = await abrirLeitura({ freio });
    await p.page.waitForTimeout(600);
    assert.equal(await p.card.count(), 0, `freio ${freio}`);
    assert.equal(p.L.gets, 1, `freio ${freio}: o dono lê a credencial`);
    await p.ctx.close();
  }
  const f = await abrirLeitura({ freio: false, estado: { ativo: true, usuario: "sindico.ana" } });
  await f.card.getByText("sindico.ana").waitFor();
  assert.equal(await f.bt("Trocar senha").count() + await f.bt("Criar acesso").count(), 0);
  await f.bt("Desativar").click();
  assert.equal(await f.dlg.locator("input[name=usuario]").count(), 0);
  await f.bt("Enviar código para meu e-mail").click();
  await f.dlg.locator("input[name=codigo]").fill("111222");
  await f.dlg.getByRole("button", { name: "Desativar", exact: true }).click();
  await f.dlg.waitFor({ state: "detached" });
  assert.deepEqual(f.L.dels.map((d) => d.codigo), ["111222"]);
  assert.equal(await f.card.locator("[role=status].sr-only").textContent(), "Acesso de visualização desativado.");
  assert.equal(await f.bt("Criar acesso").count(), 0);
  await f.ctx.close();
  const p = await abrirLeitura();
  await p.card.waitFor();
  await p.bt("Criar acesso").waitFor();
  assert.equal(await p.bt("Trocar senha").count() + await p.bt("Desativar").count(), 0);
  assert.match(await p.card.textContent(), /Quem entra com este usuário vê a conta inteira e não consegue lançar, editar nem pagar\./);
  await p.ctx.close();
});

for (const [width, height] of [[1440, 900], [390, 844]]) {
  test(`criar, trocar e desativar em ${width}×${height}: código só no clique, senha nunca na tela, DELETE com corpo, sem rolagem lateral`, async () => {
    const p = await abrirLeitura({ width, height });
    await p.bt("Criar acesso").click();
    await p.dlg.waitFor();
    await preenche(p, { usuario: "sindico.ana" });
    assert.equal(p.L.codigos, 0);
    await pedeCodigo(p);
    assert.equal(p.L.codigos, 1);
    await p.dlg.locator("input[name=codigo]").fill("123456");
    assert.equal(await p.lateral(), 0, "modal");
    await p.page.screenshot({ path: join(process.env.TMPDIR ?? "/tmp", `leitura-modal-${width}.png`) });
    await p.bt("Salvar").click();
    await p.dlg.waitFor({ state: "detached" });
    assert.deepEqual(p.L.puts.map(({ usuario, senha, codigo, tipo }) => ({ usuario, senha, codigo, tipo })), [{ usuario: "sindico.ana", senha: SENHA, codigo: "123456", tipo: "application/json" }]);
    await p.card.getByText("sindico.ana").waitFor();
    assert.ok(!(await p.page.content()).includes(SENHA), "a senha não aparece na página");
    assert.equal(await p.card.locator("[role=status].sr-only").textContent(), "Acesso de visualização salvo.");
    assert.equal(await p.lateral(), 0, "card ativo");
    await p.page.screenshot({ path: join(process.env.TMPDIR ?? "/tmp", `leitura-ativo-${width}.png`) });

    await p.bt("Trocar senha").click();
    assert.equal(await p.dlg.locator("input[name=usuario]").inputValue(), "sindico.ana");
    await preenche(p, { senha: "outra-senha-2" });
    await pedeCodigo(p);
    await p.dlg.locator("input[name=codigo]").fill("654321");
    await p.bt("Salvar").click();
    await p.dlg.waitFor({ state: "detached" });
    assert.deepEqual(p.L.puts.at(-1), { ...p.L.puts.at(-1), usuario: "sindico.ana", senha: "outra-senha-2", codigo: "654321" });
    assert.equal(p.L.puts.length, 2);

    await p.bt("Desativar").click();
    await p.bt("Enviar código para meu e-mail").click();
    await p.dlg.locator("input[name=codigo]").fill("111222");
    await p.dlg.getByRole("button", { name: "Desativar", exact: true }).click();
    await p.dlg.waitFor({ state: "detached" });
    assert.deepEqual(p.L.dels.map((d) => ({ codigo: d.codigo, tipo: d.tipo })), [{ codigo: "111222", tipo: "application/json" }]);
    await p.bt("Criar acesso").waitFor();
    assert.equal(p.L.codigos, 3);
    await p.ctx.close();
  });
}

test("cada recusa do servidor tem texto próprio; o código só é pedido pelo botão; senhas diferentes não saem", async () => {
  const p = await abrirLeitura();
  await p.bt("Criar acesso").click();
  await preenche(p, { usuario: "sindico.ana", confirma: "diferente-123" });
  await p.dlg.locator("input[name=codigo]").fill("123456");
  await p.bt("Salvar").click();
  await p.alerta.getByText("As senhas não são iguais.").waitFor();
  assert.equal(p.L.puts.length, 0);
  await p.dlg.locator("input[name=confirma]").fill(SENHA);
  const casos = [
    [409, { error: { code: "usuario_em_uso", message: "Conflict" } }, /Esse usuário já está em uso/],
    [422, { error: { code: "leitura_invalida", message: "Unprocessable" } }, /Usuário de 4 a 40 caracteres.*senha de 8 a 72/],
    [400, { error: { code: "codigo_invalido", message: "Bad" } }, /Código incorreto/],
    [400, { error: { code: "codigo_expirado", message: "Bad" } }, /O código expirou/],
    [429, { error: { code: "rate_limited", message: "Too" } }, /Muitas tentativas/],
    [403, { error: { code: "acesso_leitura_indisponivel", message: "Forbidden" } }, /ainda não está disponível/],
    [500, { error: { code: "internal", message: "x" } }, /Não foi possível salvar/],
  ];
  for (const [status, corpo, texto] of casos) {
    p.L.put = (r) => r.fulfill({ status, json: corpo });
    await p.dlg.locator("input[name=codigo]").fill("123456");
    await p.bt("Salvar").click();
    await p.alerta.filter({ hasText: texto }).waitFor();
    assert.equal(await p.dlg.locator("input[name=codigo]").inputValue(), [422, 403, 429, 500].includes(status) ? "123456" : "", `código após ${status}`);
    assert.equal(await p.dlg.locator("input[name=senha]").inputValue(), SENHA, "a senha digitada fica para corrigir o resto");
    assert.equal(await p.card.count(), 1);
  }
  assert.equal(p.L.puts.length, casos.length);
  assert.equal(p.L.codigos, 0, "nenhum código foi pedido sem o clique");
  p.L.codigo = (r) => r.fulfill({ status: 429, json: { error: { code: "rate_limited", message: "Too" } } });
  await p.bt("Enviar código para meu e-mail").click();
  await p.alerta.filter({ hasText: /Muitas tentativas/ }).waitFor();
  await p.ctx.close();
});

test("desativar: o código errado mantém o acesso ativo e mostra o texto", async () => {
  const p = await abrirLeitura({ estado: { ativo: true, usuario: "sindico.ana" } });
  await p.bt("Desativar").click();
  p.L.del = (r) => r.fulfill({ status: 400, json: { error: { code: "codigo_invalido", message: "Bad" } } });
  await p.dlg.locator("input[name=codigo]").fill("123456");
  await p.dlg.getByRole("button", { name: "Desativar", exact: true }).click();
  await p.alerta.filter({ hasText: /Código incorreto/ }).waitFor();
  await p.dlg.getByRole("button", { name: "Cancelar" }).click();
  await p.card.getByText("sindico.ana").waitFor();
  await p.ctx.close();
});
