/**
 * /painel#/casa (PR 6c), isolamento no cliente: a chave de cache é por conta (["conta","familia-casa",uid]), a troca de conta com a tela aberta não mostra a casa
 * nem o diálogo da anterior, nada vai para storage/hash, e o foco da aba e o aviso do SSE RELEEM a casa (ler a casa não grava log; reduz conflito de edição).
 * Em cada teste, o comentário de cima diz qual mutação o deixa vermelho.
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { navegador } from "./_guia.mjs";
import { F, ate, comCasa, sse } from "./_familia.mjs";

const browser = navegador();
void browser;

const perfil = (uid) => (ctx) => ctx.route((u) => u.pathname === "/auth/dashboard-profile", (r) => r.fulfill({ json: { display_name: "Ana", email: "a@exemplo.com", user_id: uid.v, whatsapp_linked: false } }));
const foco = (page) => page.evaluate(() => window.dispatchEvent(new Event("visibilitychange")));

// mutação: tirar o `key={chave}` de PaginaCasa (CasaPagina.tsx): o diálogo da conta 7 reabre sobre a casa da 8.
// A chave por uid tem uma 2ª defesa na LEITURA (main.tsx reseta as ["conta", assunto] sem uid): só a chave sem uid E sem esse reset (as duas mutações juntas) mostra o dado da 7; sozinha cada uma fica verde.
// O reset não cobre um `setQueryData` tardio de escrita em voo: esse é o caso do teste de baixo (a chave capturada com o uid antigo).
test("troca de conta com a casa e o diálogo abertos: nada da conta 7 aparece enquanto a casa da 8 está presa; o rascunho some", async () => {
  const uid = { v: 7 };
  await comCasa({ antes: perfil(uid) }, async (t) => {
    await t.page.getByText("Viagem").first().waitFor();
    await t.bt("Nova meta").click();
    await t.dlg().getByLabel("Nome").fill("Segredo da 7");
    let soltar; t.s.holdCasa = new Promise((r) => (soltar = r)); // o GET da conta 8 demora
    t.s.casa = { ...structuredClone(F.casa), metas: [{ ...F.casa.metas[0], nome: "Da conta 8" }], orcamentos: [] };
    uid.v = 8;
    await foco(t.page);
    await t.page.waitForFunction(() => !document.body.innerText.includes("Viagem"));
    await t.page.waitForTimeout(300);
    const x = await t.page.locator("body").innerText();
    assert.ok(!x.includes("Viagem") && !x.includes("R$ 1.234,56") && !x.includes("Mercado"), "dado da conta 7 com a 8 carregando");
    assert.equal(await t.dlg().count(), 0, "o diálogo da conta 7 sobreviveu");
    soltar();
    await t.page.getByText("Da conta 8").waitFor();
    assert.ok(!(await t.page.locator("body").innerText()).includes("Segredo da 7"));
    assert.equal(await t.dlg().count(), 0, "o diálogo da conta 7 reabriu sobre a casa da conta 8");
  });
});

// mutação: tirar o `uid` de `casaQuery` (casa.ts): o diálogo captura a chave da conta 7 e o `setQueryData` tardio da escrita em voo cai na chave que a conta 8 lê; "Segredo da 7" aparece na tela da 8
test("a conta 7 salva uma meta com o POST preso, a conta troca para a 8 (GET preso): a tela da 8 não mostra nada da 7, nem presa nem depois", async () => {
  const uid = { v: 7 };
  let solta; const seguro = new Promise((r) => (solta = r));
  await comCasa({ antes: (_c, s) => { perfil(uid)(_c); s.casaCriar = () => seguro; } }, async (t) => {
    await t.page.getByText("Viagem").first().waitFor();
    await t.bt("Nova meta").click();
    await t.dlg().getByLabel("Nome").fill("Segredo da 7");
    await t.dlg().getByLabel("Quanto juntar (R$)").fill("10");
    await t.dlg().getByRole("button", { name: "Salvar" }).click();
    await ate(() => t.escritas().length === 1); // o POST da 7 está preso no servidor
    let abre; t.s.holdCasa = new Promise((r) => (abre = r)); // o GET da conta 8 demora
    t.s.casa = { ...structuredClone(F.casa), metas: [{ ...F.casa.metas[0], nome: "Da conta 8" }], orcamentos: [] };
    uid.v = 8;
    await foco(t.page);
    await t.page.waitForFunction(() => !document.body.innerText.includes("Viagem"));
    solta(); // o POST da 7 conclui com o GET da 8 ainda preso
    await t.page.waitForTimeout(800);
    assert.ok(!(await t.page.locator("body").innerText()).includes("Segredo da 7"), "dado da conta 7 na tela da 8 com o GET preso");
    abre();
    await t.page.getByText("Da conta 8").waitFor();
    await t.page.waitForTimeout(300);
    const x = await t.page.locator("body").innerText();
    assert.ok(!x.includes("Segredo da 7") && !x.includes("Viagem"), "dado da conta 7 na tela da 8 depois do GET");
  });
});

// mutação: gravar a casa em localStorage/sessionStorage/hash (qualquer persistência) — aqui nada pode conter os nomes
test("nada da casa vai para storage nem para o hash", async () => {
  await comCasa({}, async (t) => {
    await t.bt("Nova meta").click();
    await t.dlg().getByLabel("Nome").fill("Carro");
    await t.dlg().getByLabel("Quanto juntar (R$)").fill("10");
    await t.dlg().getByRole("button", { name: "Salvar" }).click();
    await t.page.locator(".casa-lista li", { hasText: "Carro" }).waitFor();
    const r = await t.page.evaluate(() => [JSON.stringify({ ...localStorage }), JSON.stringify({ ...sessionStorage }), location.hash, document.cookie]);
    for (const x of r) assert.ok(!/Viagem|Carro|Mercado|Ana|m1/.test(x), x);
    assert.equal(r[2], "#/casa");
  });
});

// mutação: `refetchOnWindowFocus: false` em casaQuery (casa.ts): o foco não relê e a edição de outro membro só aparece com um reload
test("foco da aba relê a casa (não grava log): a edição de outro membro aparece; é decisão do plano", async () => {
  await comCasa({}, async (t) => {
    t.s.casa.metas[0].nome = "Mudada por outro";
    const n = t.leituras().length;
    await foco(t.page);
    await ate(() => t.leituras().length > n);
    await t.page.getByText("Mudada por outro").waitFor();
  });
});

// mutação: `meta: { foto: true }` em casaQuery (casa.ts, o que o SSE não relê): o aviso não traz a edição de outro membro
// A decisão: o SSE (lib/eventos.ts) invalida tudo que não é foto; a casa NÃO é foto, porque ler a casa não grava linha de histórico e o aviso ao vivo reduz o 409.
test("o aviso do SSE relê a casa (e a reconexão também): a edição de outro membro chega sem reload", async () => {
  const ev = sse();
  await comCasa({ antes: ev.antes }, async (t) => {
    const n = t.leituras().length;
    t.s.casa.orcamentos[0].categoria = "Mercado do outro";
    ev.solta();
    await ate(() => t.leituras().length > n, 10000);
    await t.page.getByText("Mercado do outro").waitFor();
    assert.ok(ev.hits >= 1);
  });
});
