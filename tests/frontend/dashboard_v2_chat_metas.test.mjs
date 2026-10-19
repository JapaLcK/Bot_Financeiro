// O assunto `metas` do chat do /painel (parts/MetasResposta.tsx): a frase + as pílulas do app com o
// dado de `GET /api/v2/metas`. O desenho da pílula (blocos, ✓, ⚠, aria-label): dashboard_v2_chat_metas_pilula.test.mjs.
//   · os 3 chips de metas (a próxima meta, a reserva, a meta de economia) e o texto livre
//     mostram a MESMA resposta real; a LEAD de exemplo (número inventado) não a cobre;
//   · vazio, erro, uma requisição por pergunta, role=status só com a frase, SSE;
//   · no protótipo o assunto segue o de exemplo (GOALS), sem requisição.
import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import { RESPOSTAS } from "./_painel.mjs";
import { iniciar, encerrar, ERRO, SENHA, SEM_METAS, CHIP_META, CHIP_RESERVA, CHIP_ECONOMIA, abrir, perguntar, resposta, ultima, chip, pilulas, varios, servirMetas } from "./_chat_metas.mjs";

before(iniciar);
after(encerrar);

for (const width of [1440, 390]) {
  test(`${width}: o chip responde com as metas reais: frase e 5 pílulas só leitura, reserva primeiro, sem frase na tela, uma requisição`, async () => {
    const { ctx, page, gets, erros } = await abrir({ width });
    await chip(page, CHIP_META);
    const texto = await resposta(page);
    await pilulas(page).first().waitFor();
    await page.waitForTimeout(300); // o role=status monta a mesma frase 60 ms depois
    const nomes = await ultima(page).locator(".pilula-nome").allInnerTexts();
    const rotulos = await pilulas(page).evaluateAll((ps) => ps.slice(0, 2).map((p) => p.getAttribute("aria-label")));
    const blocos = await pilulas(page).first().locator(".blocos > span").evaluateAll((bs) => [bs.length, bs.filter((b) => b.classList.contains("cheio")).length]);
    const barras = await ultima(page).locator(".meter").count();
    const visivel = await ultima(page).locator(".metas-chat").innerText();
    const cartoes = await ultima(page).locator("article.meta").count();
    const controles = await ultima(page).locator(".metas-chat button, .metas-chat a").evaluateAll((cs) => cs.map((c) => [c.textContent, Math.round(c.getBoundingClientRect().height)]));
    const link = await ultima(page).locator("a.msg-open").getAttribute("href");
    const dado = await ultima(page).evaluate((li) => [li.dataset.dado, li.querySelector(".msg-by")?.textContent.trim() ?? null]);
    const largura = await pilulas(page).first().evaluate((p) => Math.round(p.getBoundingClientRect().width));
    const estoura = await ultima(page).evaluate((li) => [li.scrollWidth > li.clientWidth, [...li.querySelectorAll(".pilula")].some((p) => p.scrollWidth > p.clientWidth)]);
    const docLargo = await page.evaluate(() => document.scrollingElement.scrollWidth - document.scrollingElement.clientWidth);
    await ctx.close();
    assert.equal(texto, "Você tem 11 metas.");
    assert.deepEqual(nomes, ["Reserva de emergência", "Viagem pro Japão", "Notebook novo", "Entrada do apê", "Curso de inglês"]);
    assert.deepEqual(rotulos, [
      "Reserva de emergência, 25%, 1 de 6 meses, R$ 4.500,00 de R$ 18.000,00, Nesse ritmo, chega em abril de 2029",
      "Viagem pro Japão, 60%, R$ 12.000,00 de R$ 20.000,00, prazo junho de 2027, Nesse ritmo, chega em dezembro de 2027, depois do prazo",
    ]);
    assert.deepEqual(blocos, [6, 1]); // floor(4500 × 6 / 18000) = 1 bloco cheio de 6
    assert.equal(barras, 4); // as 4 metas comuns; a reserva usa blocos
    assert.match(visivel, /^Suas metas\s*Reserva de emergência\s*25%\s*Viagem pro Japão\s*60%/);
    assert.doesNotMatch(visivel, /R\$|Nesse ritmo|Precisa|Falta|Editar|Mudar prazo/); // nenhuma frase nem dinheiro na tela
    assert.equal(cartoes, 0); // nada do cartão da página Metas
    assert.deepEqual(controles, [["+6", controles[0][1]], ["Ver todas as 11 metas no painel", controles[1][1]]]);
    assert.ok(controles.every(([, h]) => h >= 44), JSON.stringify(controles)); // alvo de toque do "+N" e do link
    assert.equal(link, "#/metas");
    assert.deepEqual(dado, ["real", null]);
    assert.ok(largura <= 480);
    assert.deepEqual(estoura, [false, false]);
    assert.equal(docLargo, 0);
    assert.deepEqual(gets, ["GET"]);
    assert.deepEqual(erros, []);
  });
}

test("os chips da reserva e da meta de economia mostram as metas reais, nunca o número de exemplo", async () => {
  for (const [perfil, ask] of [["investir", CHIP_RESERVA], ["economizar", CHIP_ECONOMIA]]) {
    const { ctx, page, gets } = await abrir({ perfil });
    await chip(page, ask);
    const texto = await resposta(page);
    await pilulas(page).first().waitFor();
    const dado = await ultima(page).getAttribute("data-dado");
    const selo = await ultima(page).locator(".msg-by").count();
    const nome = await pilulas(page).first().locator(".pilula-nome").innerText();
    await ctx.close();
    assert.equal(texto, "Você tem 11 metas.", ask);
    assert.doesNotMatch(texto, /Sua reserva cobre|caixinhas já recebem/);
    assert.equal(dado, "real");
    assert.equal(selo, 0); // a resposta real não leva a linha do selo
    assert.equal(nome, "Reserva de emergência"); // a reserva primeiro
    assert.deepEqual(gets, ["GET"], ask);
  }
});

test("sem metas: o texto exato, só a vaga 'Monte no painel' (6 blocos vazios) e o link para criar, nenhum R$", async () => {
  const { ctx, page } = await abrir({ metas: "vazio" });
  await chip(page, CHIP_META);
  const texto = await resposta(page);
  await ultima(page).locator("a.msg-open").waitFor();
  const link = await ultima(page).locator("a.msg-open").evaluate((a) => [a.textContent, a.getAttribute("href")]);
  const vaga = await pilulas(page).evaluateAll((ps) => ps.map((p) => [p.getAttribute("aria-label"), p.textContent, p.querySelectorAll(".blocos > span").length, p.querySelectorAll(".cheio").length]));
  const tudo = await ultima(page).innerText();
  await ctx.close();
  assert.equal(texto, SEM_METAS);
  assert.deepEqual(link, ["Criar no painel", "#/metas"]);
  assert.deepEqual(vaga, [["Reserva de emergência, 6 meses, ainda não criada. Monte no painel.", "Monte no painel", 6, 0]]);
  assert.doesNotMatch(tudo, /R\$/);
});

test("1 meta: o singular, a vaga da reserva ocupa um lugar, sem '+N' e com o link 'Abrir no painel'", async () => {
  const { ctx, page } = await abrir();
  await servirMetas(ctx, [varios.metas[1]]);
  await chip(page, CHIP_META);
  const texto = await resposta(page);
  await pilulas(page).nth(1).waitFor();
  const nomes = await pilulas(page).evaluateAll((ps) => ps.map((p) => p.textContent.replace(/\d+%$/, "")));
  const link = await ultima(page).locator("a.msg-open").innerText();
  const mais = await ultima(page).locator(".pilula-mais").count();
  await ctx.close();
  assert.equal(texto, "Você tem 1 meta.");
  assert.deepEqual(nomes, ["Monte no painel", "Viagem pro Japão"]);
  assert.equal(link, "Abrir no painel");
  assert.equal(mais, 0);
});

test("erros esperados e de rede: o texto de cada um, nenhuma pílula, nenhum R$", async () => {
  const leia = async (falha) => {
    const { ctx, page } = await abrir({ falha });
    await chip(page, CHIP_META);
    const t = await resposta(page);
    await page.waitForTimeout(300);
    const n = await pilulas(page).count();
    const tudo = await ultima(page).innerText();
    await ctx.close();
    assert.equal(n, 0, falha);
    assert.doesNotMatch(tudo, /R\$/, falha);
    return t;
  };
  assert.equal(await leia("403_password_required"), SENHA);
  assert.equal(await leia("401"), "Sua sessão terminou. Recarregue a página.");
  for (const f of ["402", "503", "500", "rede"]) assert.equal(await leia(f), ERRO, f);
});

test("erro de rede: perguntar de novo pede a rota mais uma vez e mostra as pílulas", async () => {
  const { ctx, page, gets } = await abrir({ falha: "rede" });
  await chip(page, CHIP_META);
  await page.locator(".chat > .msg-piggy .msg-text", { hasText: "Não consegui buscar agora" }).waitFor({ timeout: 15000 });
  await page.waitForTimeout(300);
  const falhou = gets.length; // as 3 tentativas do `retry` (rede e 5xx)
  await ctx.route("**/api/v2/metas", (r) => r.fulfill({ json: varios }));
  await perguntar(page, "como estão minhas metas?");
  const depois = await resposta(page);
  await pilulas(page).first().waitFor();
  await page.waitForTimeout(300);
  const total = gets.length;
  const n = await pilulas(page).count();
  await ctx.close();
  assert.equal(falhou, 3);
  assert.equal(total, falhou + 1);
  assert.equal(depois, "Você tem 11 metas.");
  assert.equal(n, 5);
});

// A frase aparece na bolha e, 60 ms depois, no role=status do PiggyChat (o leitor de tela
// lê de lá). Só a bolha consulta, e as pílulas ficam fora da cópia.
test("uma requisição por pergunta (sucesso e 403 rápido); o role=status lê só a frase", async () => {
  for (const opts of [{}, { falha: "403_password_required" }]) {
    const { ctx, page, gets } = await abrir(opts);
    await chip(page, CHIP_META);
    await resposta(page);
    await page.waitForTimeout(300);
    const bolha = await ultima(page).locator(".msg-text").textContent();
    const anuncio = await page.locator('[role="status"]:has(+ .chat)').textContent();
    await ctx.close();
    assert.deepEqual(gets, ["GET"], JSON.stringify(opts));
    assert.equal(anuncio, bolha, JSON.stringify(opts));
    assert.notEqual(bolha, "Calculando…");
    assert.doesNotMatch(anuncio, /Reserva de emergência/);
  }
});

// Fora da /piggy a consulta fica inativa: o aviso do SSE só a marca como invalidada. Voltar
// tem de reler (a lista velha não fica na tela).
test("aviso do SSE com a conversa fechada: voltar relê uma vez e a bolha mostra o dado novo", async () => {
  const { ctx, page, gets } = await abrir({ sse: true });
  await chip(page, CHIP_META);
  const antes = await resposta(page);
  await page.evaluate(() => { location.hash = "#/"; });
  await page.locator(".chat").waitFor({ state: "detached" });
  await ctx.route("**/api/v2/metas", (r) => r.fulfill({ json: RESPOSTAS.metas.vazio }));
  await page.evaluate(() => window.sseDoPainel.dispatchEvent(new MessageEvent("message", { data: '{"recurso":"metas"}' })));
  const inativa = gets.length;
  await page.evaluate(() => { location.hash = "#/piggy"; });
  await page.locator(".chat").waitFor();
  await page.waitForFunction(() => document.querySelector(".chat > .msg-piggy:last-child .msg-text").textContent.includes("ainda não tem metas"), null, { timeout: 5000 }).catch(() => {});
  await page.waitForTimeout(300);
  const voltou = [gets.length, await ultima(page).locator(".msg-text").innerText(), await pilulas(page).count()];
  await ctx.close();
  assert.equal(antes, "Você tem 11 metas.");
  assert.equal(inativa, 1); // inativa: o aviso não relê sozinho
  assert.deepEqual(voltou, [2, SEM_METAS, 1]); // só a vaga "Monte no painel"
});

test("texto livre: 'metas' (qualquer caixa) vai para o real; 'metade do mês' e 'gastos' seguem no exemplo, sem requisição", async () => {
  const { ctx, page, gets } = await abrir();
  await perguntar(page, "metade do mês já foi?");
  await perguntar(page, "quanto gastei com meu cartão?");
  const exemplos = await page.locator(".chat > li.msg-piggy").evaluateAll((lis) => lis.map((li) => li.dataset.dado));
  const antes = gets.length;
  await perguntar(page, "como estão minhas metas?");
  const a = await resposta(page);
  await perguntar(page, "METAS");
  const b = await resposta(page);
  await perguntar(page, "e a minha meta?");
  const c = await resposta(page);
  const dados = await page.locator(".chat > li.msg-piggy").evaluateAll((lis) => lis.map((li) => li.dataset.dado));
  await ctx.close();
  assert.deepEqual(exemplos, ["exemplo", "exemplo"]);
  assert.equal(antes, 0);
  assert.deepEqual([a, b, c], ["Você tem 11 metas.", "Você tem 11 metas.", "Você tem 11 metas."]);
  assert.deepEqual(dados, ["exemplo", "exemplo", "real", "real", "real"]);
});

// A escrita de meta é no painel (ou na IA, Etapa 7): o chat web só LÊ. Com verbo de escrita a frase segue para os
// atalhos, como antes do C3; as frases de leitura seguem para as metas reais.
test("texto livre: frase com verbo de escrita sobre meta segue nos atalhos (sem requisição); as de leitura vão para as metas", async () => {
  const escrita = ["quero criar uma meta nova", "apaga a meta da viagem", "muda minha meta", "cria uma meta de 5000", "edita a meta", "excluir meta",
    "aumenta a meta", "Cadastrar meta", "quero uma meta", "me ajuda a definir uma meta?", "remove a meta do notebook", "faz uma meta pra mim", "cria uma meta nova"];
  const leitura = ["minhas metas", "quanto falta pra minha meta?", "meta de gastos de comida", "Como estão minhas METAS?",
    "como tá a meta do notebook novo?", "e a meta da casa nova?", "minhas metas novas", "quanto falta pra meta do carro novo"]; // "novo" é do nome da meta, não escrita
  const { ctx, page, gets } = await abrir();
  for (const t of escrita) await perguntar(page, t);
  const dadosEscrita = await page.locator(".chat > li.msg-piggy").evaluateAll((lis) => lis.map((li) => li.dataset.dado));
  const textos = await page.locator(".chat > li.msg-piggy .msg-text").allInnerTexts();
  const antes = gets.length;
  for (const t of leitura) { await perguntar(page, t); assert.equal(await resposta(page), "Você tem 11 metas.", t); }
  const dados = await page.locator(".chat > li.msg-piggy").evaluateAll((lis) => lis.map((li) => li.dataset.dado));
  await ctx.close();
  assert.deepEqual(dadosEscrita, escrita.map(() => "exemplo"));
  assert.ok(textos.every((t) => t.startsWith("Aqui no protótipo eu respondo pelos atalhos")), textos.join(" | "));
  assert.equal(antes, 0);
  assert.deepEqual(dados.slice(escrita.length), leitura.map(() => "real"));
});

test("protótipo: o assunto metas segue o de exemplo (GOALS, 1 bloco, sem pílula, a reserva inventada), sem requisição", async () => {
  const { ctx, page, gets } = await abrir({ demo: true });
  await chip(page, CHIP_META);
  const texto = await resposta(page);
  const blocos = await ultima(page).locator(".msg-block").count();
  const cartao = await pilulas(page).count();
  await ctx.close();
  const r = await abrir({ demo: true, perfil: "investir" });
  await chip(r.page, CHIP_RESERVA);
  const reserva = await resposta(r.page);
  await r.ctx.close();
  assert.match(texto, /^A meta mais perto é/);
  assert.equal(blocos, 1);
  assert.equal(cartao, 0);
  assert.match(reserva, /^Sua reserva cobre/); // a LEAD de exemplo segue valendo fora do real
  assert.deepEqual(gets.concat(r.gets), []);
});
