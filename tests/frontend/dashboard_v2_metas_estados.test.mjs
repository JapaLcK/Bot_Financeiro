/**
 * /painel#/metas: os estados de borda da tela, achados do Tester (PR B da Etapa 4). Cada grupo tem o caso
 * que quebra sem o conserto e o caminho legítimo que continua valendo: duplo clique que não fecha o
 * diálogo, fonte, apagar, 409 da reserva, foco, plural, plano sem investimentos, `meses` com espaço,
 * a prioridade das frases e o que o 4xx nunca mostra. Leitura e escrita comuns: os outros dois arquivos de metas.
 */
import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import { chromium } from "playwright";
import { RESPOSTAS, abrirPainel, exigeArtefatoEmDia } from "./_painel.mjs";

let browser;
before(async () => { exigeArtefatoEmDia(); browser = await chromium.launch(); });
after(() => browser?.close());
const ERROS = RESPOSTAS.erros;
const clone = (o) => JSON.parse(JSON.stringify(o));
const varios = () => clone(RESPOSTAS.metas.varios);
const JANELA_DO_FUNDO = 450; // acima da constante do useDialogo (400 ms)
const demora = (ms) => new Promise((r) => setTimeout(r, ms));
const respostaDe = (e) => (r) => r.fulfill({ status: e.status, json: e.body });

// `dados`: o GET /metas no lugar da fixture (`set` troca depois); `resp(r, body)` responde os POST; `antes(ctx)` roda antes de abrir a tela.
async function abrir({ dados, resp, fallback = false, antes, width, ...opts } = {}) {
  const p = await abrirPainel(browser, { espera: null, width, ...opts });
  if (fallback) await p.ctx.addInitScript(() => { delete HTMLDialogElement.prototype.showModal; delete HTMLDialogElement.prototype.close; });
  if (antes) await antes(p.ctx);
  const posts = [], gets = { n: 0 };
  let atual = dados;
  await p.ctx.route(/\/api\/v2\/metas(\/editar|\/apagar)?$/, (r) => {
    const req = r.request();
    if (req.method() === "GET") { gets.n++; return atual ? (typeof atual === "function" ? atual(r) : r.fulfill({ json: atual })) : r.fallback(); }
    const body = req.postDataJSON();
    posts.push({ path: new URL(req.url()).pathname.replace("/api/v2", ""), body });
    return resp ? resp(r, body) : r.fulfill({ json: { id: body.id ?? 99 } });
  });
  await p.ir("/metas");
  await p.page.locator("#w-metas-painel").waitFor();
  return { ...p, posts, gets, set: (d) => { atual = d; }, form: p.page.locator(".meta-form"), salvar: p.page.locator(".meta-form").getByRole("button", { name: /^(Salvar|Salvando…)$/ }) };
}
const cartao = (page, nome) => page.locator("article.meta").filter({ has: page.getByRole("heading", { name: nome, exact: true }) });
const editar = (page, nome) => page.getByRole("button", { name: `Editar ${nome}` }).click();
const fechado = (page) => page.locator(".meta-form[open]").waitFor({ state: "detached" });
const abertos = (page) => page.locator(".meta-form[open]").count();
const foco = (page) => page.evaluate(() => { const a = document.activeElement; return `${a?.getAttribute("name") ?? ""}|${(a?.textContent ?? "").trim()}`; });
const recarregaPorFoco = (page) => page.evaluate(async () => {
  const set = (v) => Object.defineProperty(document, "visibilityState", { configurable: true, get: () => v });
  set("hidden"); document.dispatchEvent(new Event("visibilitychange", { bubbles: true }));
  await new Promise((r) => setTimeout(r, 50));
  set("visible"); document.dispatchEvent(new Event("visibilitychange", { bubbles: true }));
});
const ate = async (cond) => { for (let i = 0; i < 100 && !(await cond()); i++) await demora(50); assert.ok(await cond(), "a condição não veio"); };

// O grupo do duplo clique: o 2º clique cai no fundo do diálogo recém-aberto (::backdrop nativo, scrim no fallback).
// O 2º clique só sai com o diálogo já aberto, no ponto do botão (fora dele, no fundo): sem o conserto fecha, por mais lenta que esteja a máquina.
for (const fallback of [false, true]) test(`${fallback ? "Safari 14 (fallback)" : "nativo"}: duplo clique em Nova meta, Montar reserva e Editar deixa o diálogo aberto; o clique no fundo depois da janela fecha`, async () => {
  for (const [metas, botao] of [["varios", { name: "Nova meta" }], ["vazio", { name: "Montar reserva" }], ["varios", { name: "Editar Notebook novo" }]]) {
    const { ctx, page } = await abrir({ metas, fallback }); // 1440: o botão fica fora do diálogo, o 2º clique cai no fundo
    await page.getByRole("button", botao).scrollIntoViewIfNeeded();
    const box = await page.getByRole("button", botao).boundingBox();
    const [x, y] = [box.x + 10, box.y + 10];
    await page.mouse.click(x, y);
    await page.locator(".meta-form[open]").waitFor();
    const t0 = await page.evaluate(() => performance.now());
    const dentro = await page.locator(".meta-form").boundingBox();
    assert.ok(x < dentro.x || x > dentro.x + dentro.width || y < dentro.y || y > dentro.y + dentro.height, "o ponto do botão tem de cair no fundo, não no diálogo");
    await page.mouse.click(x, y);
    assert.equal(await abertos(page), 1, botao.name); // o fechar é síncrono: sem sleep para "esperar que não feche"
    await page.waitForFunction(([t, janela]) => performance.now() - t > janela, [t0, JANELA_DO_FUNDO]);
    await page.mouse.click(x, y); // o caso legítimo: clique no fundo, passada a janela
    await fechado(page);
    await ctx.close();
  }
});

// O grupo da fonte: comparar por valor (tipo + banco/conta SÓ do tipo atual), não o objeto cru.
test("trocar a fonte e voltar à original = 'Nenhum campo foi alterado.' sem POST; trocar de verdade manda só a fonte", async () => {
  const { ctx, page, form, salvar, posts } = await abrir();
  await editar(page, "Notebook novo");
  await form.getByLabel("Investido em um banco").check();
  await form.getByLabel("Banco", { exact: true }).selectOption("Nubank");
  await form.getByLabel("Tudo que tenho investido").check();
  await salvar.click();
  await form.getByText("Nenhum campo foi alterado.").waitFor();
  await form.getByRole("button", { name: "Cancelar" }).click(); await fechado(page);
  await editar(page, "Viagem pro Japão"); // fonte banco Nubank: sai e volta com o banco residual
  await form.getByLabel("Tudo que tenho investido").check();
  await form.getByLabel("Investido em um banco").check();
  await salvar.click();
  await form.getByText("Nenhum campo foi alterado.").waitFor();
  assert.deepEqual(posts, []);
  await form.getByLabel("Banco", { exact: true }).selectOption("XP");
  await salvar.click(); await fechado(page);
  assert.deepEqual(posts.map((p) => p.body), [{ id: 2, fonte: { tipo: "banco", banco: "XP" } }]);
  await ctx.close();
});

// O grupo do apagar.
test("apagar: a falha de rede diz 'apagada', não 'salva'; salvar com a rede caída segue dizendo 'salva'", async () => {
  const { ctx, page, form, salvar } = await abrir({ resp: (r) => r.abort() });
  await editar(page, "Notebook novo");
  await form.getByRole("button", { name: "Apagar", exact: true }).click();
  await form.getByRole("button", { name: "Confirmar apagar" }).click();
  await form.getByText("A resposta se perdeu. A meta pode ter sido apagada: confira a lista antes de tentar de novo.").waitFor();
  await form.getByRole("button", { name: "Voltar" }).click();
  await form.getByLabel("Nome").fill("Outro");
  await salvar.click();
  await form.getByText("A resposta se perdeu. A meta pode ter sido salva: confira a lista antes de tentar de novo.").waitFor();
  await ctx.close();
});

test("404 ao apagar: a meta já foi, o diálogo fecha e a lista recarrega; 404 ao editar segue com a mensagem e o rascunho", async () => {
  const { ctx, page, form, salvar, gets } = await abrir({ resp: respostaDe(ERROS["404_meta_nao_encontrada"]) });
  await editar(page, "Notebook novo");
  await form.getByLabel("Nome").fill("Outro nome");
  await salvar.click();
  await form.getByText("Essa meta não existe mais.").waitFor();
  assert.equal(await form.getByLabel("Nome").inputValue(), "Outro nome");
  await form.getByRole("button", { name: "Apagar", exact: true }).click();
  const antes = gets.n;
  await form.getByRole("button", { name: "Confirmar apagar" }).click();
  await fechado(page);
  await ate(() => gets.n > antes);
  await ctx.close();
});

// O grupo do 409: depois dele a recarga traz a reserva e esconde o select de Tipo; o usuário tem de poder virar Meta.
for (const [como, abrirForm] of [
  ["Nova meta escolhendo Reserva", async (page, form) => { await page.getByRole("button", { name: "Nova meta" }).click(); await form.getByLabel("Tipo").selectOption("reserva"); }],
  ["Montar reserva", async (page) => { await page.getByRole("button", { name: "Montar reserva" }).click(); }],
]) test(`409 reserva_ja_existe (${como}): o select de Tipo aparece com a reserva desabilitada; virar Meta mantém o nome e salva`, async () => {
  let ja = false;
  const { ctx, page, form, salvar, set, posts } = await abrir({ metas: "vazio", resp: (r) => ja ? r.fulfill({ json: { id: 99 } }) : r.fulfill({ status: 409, json: ERROS["409_reserva_ja_existe"].body }) });
  await abrirForm(page, form);
  await form.getByLabel("Nome").fill("Minha reserva");
  set(varios()); // a outra aba criou a reserva: a recarga traz
  await salvar.click();
  await form.getByText("Você já tem uma reserva de emergência.").waitFor();
  await ate(async () => (await form.getByLabel("Tipo").count()) === 1);
  assert.equal(await form.getByLabel("Tipo").locator('option[value="reserva"]').evaluate((o) => o.disabled), true);
  await form.getByLabel("Tipo").selectOption("meta");
  await form.getByLabel("Quanto quer juntar").fill("1000");
  ja = true;
  await salvar.click(); await fechado(page);
  assert.deepEqual(posts.map((p) => p.body).at(-1), { nome: "Minha reserva", tipo: "meta", fonte: { tipo: "investido" }, alvo: "1000" });
  // o caminho legítimo: com a reserva já existindo ao abrir, Nova meta não oferece o Tipo
  await page.getByRole("button", { name: "Nova meta" }).click();
  assert.equal(await form.getByLabel("Tipo").count(), 0);
  await ctx.close();
});

// O grupo do foco: o botão clicado sai do DOM e o foco não pode cair no <body>.
test("foco: Apagar leva ao Confirmar apagar, Voltar ao Apagar, Tirar prazo ao campo da data", async () => {
  const { ctx, page, form } = await abrir();
  await editar(page, "Viagem pro Japão");
  await form.getByRole("button", { name: "Tirar prazo" }).click();
  assert.equal(await foco(page), "data_alvo|");
  await form.getByRole("button", { name: "Apagar", exact: true }).click();
  assert.equal(await foco(page), "|Confirmar apagar");
  await form.getByRole("button", { name: "Voltar" }).click();
  assert.equal(await foco(page), "|Apagar");
  await ctx.close();
});

// O grupo do plural.
test("plural: 1 mês / 1 dia no singular; 6 meses e 45 dias seguem no plural; parcial sem dias não escreve 'medido em  dias'", async () => {
  const d = varios();
  d.metas[0].meses = 1;
  const j = d.metas.find((m) => m.nome === "Viagem pro Japão");
  j.ritmo.dias_de_historico = 1;
  const n = d.metas.find((m) => m.nome === "Notebook novo");
  n.ritmo.parcial = true; n.ritmo.dias_de_historico = null;
  const { ctx, page } = await abrir({ dados: d });
  await page.locator("article.meta").first().waitFor();
  const t = await page.locator(".metas-corpo").innerText();
  assert.match(t, /Reserva de emergência · 1 mês das suas contas fixas \(1 × R\$ 3\.000,00\)/);
  assert.match(t, /medido em 1 dia\b/);
  assert.doesNotMatch(t, /1 meses|1 dias|medido em\s+dias|undefined|null/);
  const ok = await abrir();
  await ok.page.locator("article.meta").first().waitFor();
  const o = await ok.page.locator(".metas-corpo").innerText();
  assert.match(o, /6 meses das suas contas fixas/);
  assert.match(o, /medido em 45 dias/);
  await ok.ctx.close(); await ctx.close();
});

// O grupo do plano sem investimentos: sem os rádios de investimento, o padrão é a conta.
test("plano sem investimentos: Nova meta já vem em 'Saldo de uma conta', sem conta escolhida não escreve, com ela vai fonte conta; plano com investimentos segue em 'Tudo que tenho investido'", async () => {
  const e = ERROS["403_pro_required"];
  const { ctx, page, form, salvar, posts } = await abrir({ antes: (c) => c.route("**/api/v2/investido", respostaDe(e)) });
  await page.getByRole("button", { name: "Nova meta" }).click();
  await form.getByText("Seu plano atual não inclui investimentos.").waitFor();
  await form.getByLabel("Nome").fill("Carro");
  await form.getByLabel("Quanto quer juntar").fill("100");
  assert.equal(await form.getByLabel("Saldo de uma conta").isChecked(), true);
  await salvar.click();
  await form.getByText("Escolha de onde sai o dinheiro da meta.").waitFor();
  assert.deepEqual(posts, []);
  await form.getByLabel("Conta", { exact: true }).selectOption({ label: "Nubank · Conta" });
  await salvar.click(); await fechado(page);
  assert.deepEqual(posts.map((p) => p.body.fonte), [{ tipo: "conta", conta: 1 }]);
  await ctx.close();
  const com = await abrir();
  await com.page.getByRole("button", { name: "Nova meta" }).click();
  assert.equal(await com.form.getByLabel("Tudo que tenho investido").isChecked(), true);
  await com.ctx.close();
});

test("plano sem investimentos e sem conta em reais: diz que não há fonte e leva ao Conectar banco", async () => {
  const e = ERROS["403_pro_required"];
  const { ctx, page, form } = await abrir({ antes: async (c) => {
    await c.route("**/api/v2/investido", respostaDe(e));
    await c.route("**/api/v2/contas", (r) => r.fulfill({ json: { ...RESPOSTAS.contas.todos_os_estados, contas: [] } }));
  } });
  await page.getByRole("button", { name: "Nova meta" }).click();
  await form.getByText("Seu plano atual não inclui investimentos.").waitFor();
  await form.getByText("Você não tem conta em reais conectada.").waitFor();
  assert.equal(await form.getByRole("link", { name: "Conectar banco" }).getAttribute("href"), "#/conta/bancos");
  await ctx.close();
});

// O grupo do `meses` com espaço: aparar antes de validar.
test("meses ' 6' e '6 ' valem 6; '', '0', '25' e '1.5' seguem recusados", async () => {
  const { ctx, page, form, salvar, posts } = await abrir({ metas: "vazio" });
  for (const [m, vai] of [[" 6", true], ["6 ", true], ["", false], ["0", false], ["25", false], ["1.5", false]]) {
    await page.getByRole("button", { name: "Montar reserva" }).click();
    await form.getByLabel("Meses das suas contas fixas").fill(m);
    const n = posts.length;
    await salvar.click();
    if (vai) { await fechado(page); assert.equal(posts.at(-1).body.meses, 6, m); }
    else { await form.getByText("Use de 1 a 24 meses.").waitFor(); assert.equal(posts.length, n, m); await form.getByRole("button", { name: "Cancelar" }).click(); await fechado(page); }
  }
  await ctx.close();
});

// O grupo do corpo da edição da reserva.
test("converter reserva com nome esquisito manda {id, tipo, alvo} sem nome; editar só o nome da reserva manda {id, nome} sem meses", async () => {
  const d = clone(RESPOSTAS.metas.sem_custo);
  d.metas[0].nome = "Reserva\u0000  de emergência ";
  const c = await abrir({ dados: d });
  await c.page.getByRole("button", { name: "Definir o valor eu mesmo" }).click();
  await c.form.getByLabel("Quanto quer juntar").fill("5000");
  await c.salvar.click(); await fechado(c.page);
  assert.deepEqual(c.posts.map((p) => p.body), [{ id: 1, tipo: "meta", alvo: "5000" }]);
  await c.ctx.close();
  const { ctx, page, form, salvar, posts } = await abrir();
  await editar(page, "Reserva de emergência");
  await form.getByLabel("Nome").fill("Colchão");
  await salvar.click(); await fechado(page);
  assert.deepEqual(posts.map((p) => p.body), [{ id: 1, nome: "Colchão" }]);
  await ctx.close();
});

// O grupo da prioridade das frases: com mais de um motivo, vale a ORDEM do mapa, não a do servidor.
test("ritmo sem número com 2+ motivos: a frase do plano vence a do banco; a pausa vence a moeda e o histórico curto", async () => {
  const d = varios();
  const semRitmo = (id, nome, motivos, dias = null) => ({ ...clone(d.metas[2]), id, nome, ritmo: { valor: null, dias_de_historico: dias, parcial: false, motivos } });
  d.metas = [semRitmo(21, "Plano", ["sem_banco_conectado", "plano_sem_investimentos"]), semRitmo(22, "Pausa", ["outra_moeda", "historico_curto", "conexao_pausada"], 3), semRitmo(23, "Curto", ["outra_moeda", "historico_curto"], 3)];
  const { ctx, page } = await abrir({ dados: d });
  await page.locator("article.meta").first().waitFor();
  const t = async (n) => (await cartao(page, n).innerText());
  assert.match(await t("Plano"), /Seu plano atual não inclui investimentos\.[\s\S]*Ver planos/);
  assert.doesNotMatch(await t("Plano"), /Conecte um banco pra eu acompanhar/);
  assert.match(await t("Pausa"), /A conexão com o banco está pausada/);
  assert.doesNotMatch(await t("Pausa"), /juntando histórico|outra moeda/);
  assert.match(await t("Curto"), /Ainda estou juntando histórico \(3 de 7 dias\)/);
  await ctx.close();
});

// O grupo do 4xx: nunca mostra dado guardado (por desenho derruba até o formulário aberto); rede/5xx mostra com aviso.
test("recarga com 4xx troca a lista pela página de bloqueio, sem dado velho; com 5xx mantém a lista e avisa", async () => {
  const e = ERROS["403_pro_required"];
  const { ctx, page, set } = await abrir();
  await page.locator("article.meta").first().waitFor();
  set(respostaDe(ERROS["500"]));
  await recarregaPorFoco(page);
  await page.getByText("Não deu para atualizar. Mostrando o que carregou antes.").waitFor({ timeout: 15000 });
  assert.equal(await page.locator("article.meta").count(), RESPOSTAS.metas.varios.metas.length);
  set(varios());
  await page.getByRole("button", { name: "Tentar de novo" }).click();
  await ate(async () => (await page.getByText("Não deu para atualizar.").count()) === 0);
  await page.getByRole("button", { name: "Nova meta" }).click(); // o 4xx de uma recarga derruba até o formulário aberto (por desenho: o rascunho não sobrevive)
  assert.equal(await abertos(page), 1);
  set(respostaDe(e));
  await recarregaPorFoco(page);
  await page.getByText("Não deu para abrir suas metas.").waitFor();
  assert.equal(await page.locator("article.meta").count(), 0);
  assert.equal(await page.locator(".meta-form").count(), 0);
  assert.equal(await page.getByRole("button", { name: "Nova meta" }).count(), 0);
  await ctx.close();
});

// O grupo do aviso do custo: com valor, o motivo "não li as cobranças" vira aviso de valor velho; sem motivo, nada.
test("custo com valor e 'recorrencias_banco_nao_lidas': o aviso 'O valor pode estar velho.' sai no convite, no formulário e na reserva; sem o motivo, não sai", async () => {
  const aviso = "Não consegui ler as cobranças do banco agora. O valor pode estar velho.";
  const d = varios();
  d.custo_mensal.motivos = ["recorrencias_banco_nao_lidas"];
  const sem = { metas: [], custo_mensal: clone(d.custo_mensal) };
  const p = await abrir({ dados: sem });
  await p.page.locator(".meta-convite").waitFor();
  assert.match(await p.page.locator(".meta-convite").innerText(), /R\$ 3\.000,00\/mês\)\.? ?[\s\S]*O valor pode estar velho/);
  await p.page.getByRole("button", { name: "Montar reserva" }).click();
  await p.form.getByText(aviso).waitFor();
  await p.ctx.close();
  const c = await abrir({ dados: d });
  await c.page.locator("article.meta").first().waitFor();
  assert.match(await cartao(c.page, "Reserva de emergência").innerText(), /O valor pode estar velho/);
  await c.ctx.close();
  const ok = await abrir();
  await ok.page.locator("article.meta").first().waitFor();
  assert.doesNotMatch(await ok.page.locator(".metas-corpo").innerText(), /pode estar velho/);
  await ok.ctx.close();
});

// O grupo do selo "a conferir": zero guardado + motivo do SALDO pede conferir; motivo do alvo ou saldo > 0, não.
test("selo 'a conferir': R$ 0,00 com motivo do saldo sim; com motivo do alvo ou com saldo, não", async () => {
  const d = varios();
  const base = (id, nome, atual, motivos) => ({ ...clone(d.metas[2]), id, nome, atual, motivos });
  d.metas = [base(31, "Saldo zero", "0.00", ["conexao_pausada"]), base(32, "Alvo zero", "0.00", ["plano_sem_custo_mensal"]), base(33, "Custo zero", "0.00", ["custo_desconhecido"]), base(34, "Com saldo", "10.00", ["conexao_pausada"])];
  const { ctx, page } = await abrir({ dados: d });
  await page.locator("article.meta").first().waitFor();
  const sel = async (n) => cartao(page, n).locator(".meta-valores .selo", { hasText: "a conferir" }).count();
  assert.deepEqual([await sel("Saldo zero"), await sel("Alvo zero"), await sel("Custo zero"), await sel("Com saldo")], [1, 0, 0, 0]);
  await ctx.close();
});
