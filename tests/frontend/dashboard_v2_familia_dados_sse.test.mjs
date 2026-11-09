/**
 * /painel#/conta/familia, o titular com o painel de um membro aberto: o aviso/reconexão do SSE (/api/v2/eventos) e os estados do VerMembro.
 * Cada GET de dados grava uma linha no "Quem viu" do membro: o aviso do SSE relê a lista da família, nunca os dados do membro.
 * Em cada teste, o comentário de cima diz qual mutação o deixa vermelho.
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { navegador } from "./_guia.mjs";
import { ERRO, MARIA, abrirFamilia, abrirVer, sse, titular } from "./_familia.mjs";

const browser = navegador();
void browser;

const TUDO = { ...MARIA, permissoes: ["agregados", "lancamentos"] };
const com = async (opts, fn) => {
  const t = await abrirVer(opts);
  try { await fn(t); assert.deepEqual(t.erros, [], "pageerror"); } finally { await t.ctx.close(); }
};
const familias = (s) => s.reqs.filter((q) => q.metodo === "GET" && q.caminho === "").length;
const dadosN = (s) => s.reqs.filter((q) => q.metodo === "GET" && q.caminho.startsWith("/membros/")).length;
const pausa = (page, ms = 1800) => page.waitForTimeout(ms);

// mutação: tirar `meta: { foto: true }` de `leitura` (dados.ts); positivo: a lista da família É relida pelo aviso
test("aviso e reconexão do SSE com o painel aberto: nenhum GET novo de dados do membro; o GET /familia é relido", async () => {
  const ev = sse();
  await com({ membro: TUDO, antes: ev.antes }, async ({ page, s }) => {
    await page.locator(".fam-lanc").first().or(page.getByText("Nenhum lançamento")).first().waitFor();
    await pausa(page, 400);
    const d0 = dadosN(s), f0 = familias(s);
    assert.ok(d0 >= 2, "agregados e lançamentos já lidos");
    ev.solta();
    await pausa(page);
    assert.ok(ev.hits >= 2, `a reconexão aconteceu (${ev.hits} conexões)`);
    assert.equal(dadosN(s), d0, "o SSE releu dados do membro (cada GET = uma linha no Quem viu)");
    assert.ok(familias(s) > f0, "a lista da família deve ser relida pelo aviso");
    assert.ok((await page.locator(".conta-familia").innerText()).includes("R$ 1.234,56"), "o painel segue com o dado");
  });
});

// mutação: tirar `|| !membro.permissoes.includes("agregados")` de `parou` (VerMembro.tsx); positivo: o teste de cima (lista inalterada, painel segue)
test("a lista relida sem 'agregados': 'parou de compartilhar', sem valor e sem GET de dados", async () => {
  const ev = sse();
  await com({ membro: TUDO, antes: ev.antes }, async ({ page, s, secao, bt }) => {
    await page.locator(".fam-lanc").first().or(page.getByText("Nenhum lançamento")).first().waitFor();
    await pausa(page, 400);
    const d0 = dadosN(s);
    s.visao = titular({ membros: [{ ...MARIA, permissoes: [] }] });
    ev.solta();
    await secao.getByText("Maria parou de compartilhar.").waitFor();
    await pausa(page, 800);
    const txt = await secao.innerText();
    for (const t of ["1.234,56", "789,10", "Mercado", "Lançamentos"]) assert.ok(!txt.includes(t), `sobrou ${t}`);
    assert.equal(dadosN(s), d0);
    await bt("Voltar").click();
    await secao.getByText("Não compartilha").waitFor();
  });
});

// mutação: tirar o `semLanc` ou o `viu` (VerMembro.tsx): a seção some em silêncio em vez de virar texto
test("a lista relida sem 'lancamentos': a seção vira o texto e o resumo fica", async () => {
  const ev = sse();
  await com({ membro: TUDO, antes: ev.antes }, async ({ page, s, secao }) => {
    await page.locator(".fam-lanc").first().or(page.getByText("Nenhum lançamento")).first().waitFor();
    const d0 = dadosN(s);
    s.visao = titular({ membros: [{ ...MARIA, permissoes: ["agregados"] }] });
    ev.solta();
    await secao.getByText("Maria parou de compartilhar os lançamentos.").waitFor();
    assert.ok((await secao.innerText()).includes("R$ 1.234,56"));
    assert.equal(dadosN(s), d0);
  });
  // positivo: quem abriu sem 'lancamentos' não vê texto nenhum
  await com({}, async ({ secao, page }) => {
    await pausa(page, 400);
    assert.ok(!(await secao.innerText()).includes("parou de compartilhar"));
  });
});

// O SSE manual: cada conexão fica presa até `solta()` e então entrega UM aviso (o navegador reconecta em 300 ms e a nova fica presa).
function sseManual() {
  const e = { soltas: [] };
  e.antes = (ctx) => ctx.route("**/api/v2/eventos", async (r) => {
    await new Promise((ok) => e.soltas.push(ok));
    r.fulfill({ status: 200, headers: { "content-type": "text/event-stream" }, body: `retry: 300\ndata: {"recurso":"tudo"}\n\n` }).catch(() => {});
  });
  // espera a conexão presa existir, solta e dá tempo da família ser relida
  e.aviso = async (page) => { while (!e.soltas.length) await page.waitForTimeout(50); e.soltas.shift()(); await page.waitForTimeout(900); };
  return e;
}
const PERMS = (...p) => titular({ membros: [{ ...MARIA, permissoes: p }] });
const uma = (page) => page.getByText("R$ 1.234,56").first();

// mutação: voltar `parou` a `parouAqui || !membro.permissoes.includes("agregados")` (sem o ref pegajoso, VerMembro.tsx): religar remonta o Resumo e lê sem ação do titular
test("revoga, aviso, religa, aviso: 'parou' fica, nenhum GET de dados; Voltar + Ver lê uma vez e o dado volta", async () => {
  const ev = sseManual();
  await com({ membro: TUDO, antes: ev.antes }, async ({ page, s, secao, bt }) => {
    await page.locator(".fam-lanc").first().or(page.getByText("Nenhum lançamento")).first().waitFor();
    await pausa(page, 400);
    const d0 = dadosN(s);
    s.visao = PERMS();
    await ev.aviso(page);
    await secao.getByText("Maria parou de compartilhar.").waitFor();
    const f0 = familias(s);
    s.visao = PERMS("agregados", "lancamentos");
    await ev.aviso(page);
    assert.ok(familias(s) > f0, "o aviso releu a família (religada)");
    assert.ok((await secao.innerText()).includes("Maria parou de compartilhar."), "o texto continua");
    assert.equal(dadosN(s), d0, "religar com o painel aberto leu dados sem ação do titular");
    await bt("Voltar").click();
    await bt("Ver").click();
    await uma(page).waitFor();
    assert.equal(dadosN(s), d0 + 2, "Ver de novo: 1 GET de agregados + 1 de lançamentos");
  });
});

// mutação: tirar o `semLanc` (VerMembro.tsx): religar remonta o MembroLancamentos e o GET sai
test("revoga só os lançamentos, aviso, religa, aviso: o texto fica, nenhum GET; Voltar + Ver lê o resumo e os lançamentos", async () => {
  const ev = sseManual();
  await com({ membro: TUDO, antes: ev.antes }, async ({ page, s, secao, bt }) => {
    await page.locator(".fam-lanc").first().or(page.getByText("Nenhum lançamento")).first().waitFor();
    await pausa(page, 400);
    const d0 = dadosN(s);
    s.visao = PERMS("agregados");
    await ev.aviso(page);
    await secao.getByText("Maria parou de compartilhar os lançamentos.").waitFor();
    s.visao = PERMS("agregados", "lancamentos");
    await ev.aviso(page);
    assert.ok((await secao.innerText()).includes("Maria parou de compartilhar os lançamentos."), "o texto continua");
    assert.ok((await secao.innerText()).includes("R$ 1.234,56"), "o resumo fica");
    assert.equal(dadosN(s), d0, "religar leu os lançamentos sem ação do titular");
    await bt("Voltar").click();
    await bt("Ver").click();
    await page.locator(".fam-lanc").first().or(page.getByText("Nenhum lançamento")).first().waitFor();
    assert.equal(dadosN(s), d0 + 2);
    assert.ok(!(await secao.innerText()).includes("parou de compartilhar"));
  });
});

// mutação: `viu` seguir a lista (`viu.current = membro.permissoes.includes("lancamentos")` a cada render, VerMembro.tsx): liberar 'lancamentos' pela 1ª vez monta a seção e o GET sai sozinho
test("abriu só com o resumo, o aviso traz 'lancamentos': nenhum GET e sem seção (nem texto, mesmo se cair de novo); Voltar + Ver lê os dois e mostra", async () => {
  const ev = sseManual();
  await com({ antes: ev.antes }, async ({ page, s, secao, bt }) => {
    await uma(page).waitFor();
    await pausa(page, 400);
    const d0 = dadosN(s), f0 = familias(s);
    s.visao = PERMS("agregados", "lancamentos");
    await ev.aviso(page);
    assert.ok(familias(s) > f0, "o aviso releu a família");
    assert.equal(dadosN(s), d0, "liberar 'lancamentos' pela 1ª vez leu sozinho (cada GET = uma linha no Quem viu)");
    assert.equal(await secao.getByRole("heading", { name: "Lançamentos" }).count(), 0, "a seção apareceu sem Ver");
    s.visao = PERMS("agregados");
    await ev.aviso(page);
    assert.equal(await secao.getByText("parou de compartilhar").count(), 0, "texto de quem nunca viu a seção");
    s.visao = PERMS("agregados", "lancamentos");
    await ev.aviso(page);
    assert.equal(dadosN(s), d0);
    await bt("Voltar").click();
    await bt("Ver").click();
    await page.locator(".fam-lanc").first().or(page.getByText("Nenhum lançamento")).first().waitFor();
    assert.equal(dadosN(s), d0 + 2, "Ver de novo: 1 GET de agregados + 1 de lançamentos");
    assert.equal(await secao.getByRole("heading", { name: "Lançamentos" }).count(), 1);
  });
});

// mutação: tirar o `parouLista` pegajoso (VerMembro.tsx); positivo: o painel de quem abriu com o resumo mostra o dado antes da revogação
test("abriu só com o resumo, perde 'agregados' e depois ganha os dois: 'parou' fica e nenhum GET", async () => {
  const ev = sseManual();
  await com({ antes: ev.antes }, async ({ page, s, secao }) => {
    await uma(page).waitFor();
    await pausa(page, 400);
    const d0 = dadosN(s);
    s.visao = PERMS();
    await ev.aviso(page);
    await secao.getByText("Maria parou de compartilhar.").waitFor();
    s.visao = PERMS("agregados", "lancamentos");
    await ev.aviso(page);
    assert.ok((await secao.innerText()).includes("Maria parou de compartilhar."));
    assert.equal(await secao.getByRole("heading", { name: "Lançamentos" }).count(), 0);
    assert.equal(dadosN(s), d0);
  });
});

// mutação: tirar o `parouLista` ou trocar a ordem dos ramos de `parou` (VerMembro.tsx): do texto dos lançamentos ao total
test("perde 'lancamentos' e depois 'agregados': vira 'parou de compartilhar.' (total), sem o texto dos lançamentos e sem GET", async () => {
  const ev = sseManual();
  await com({ membro: TUDO, antes: ev.antes }, async ({ page, s, secao }) => {
    await page.locator(".fam-lanc").first().or(page.getByText("Nenhum lançamento")).first().waitFor();
    await pausa(page, 400);
    const d0 = dadosN(s);
    s.visao = PERMS("agregados");
    await ev.aviso(page);
    await secao.getByText("Maria parou de compartilhar os lançamentos.").waitFor();
    s.visao = PERMS();
    await ev.aviso(page);
    await secao.getByText("Maria parou de compartilhar.").waitFor();
    assert.ok(!(await secao.innerText()).includes("os lançamentos"));
    assert.equal(dadosN(s), d0);
  });
});

// mutação: tirar `|| membro.status !== "ativo"` (VerMembro.tsx): o painel do suspenso segue com o dado
test("o membro passa a suspenso com o painel aberto: 'parou de compartilhar', sem dado e sem GET de dados; reativado, segue assim", async () => {
  const ev = sseManual();
  await com({ membro: TUDO, antes: ev.antes }, async ({ page, s, secao }) => {
    await page.locator(".fam-lanc").first().or(page.getByText("Nenhum lançamento")).first().waitFor();
    await pausa(page, 400);
    const d0 = dadosN(s);
    s.visao = titular({ membros: [{ ...TUDO, status: "suspenso" }] });
    await ev.aviso(page);
    await secao.getByText("Maria parou de compartilhar.").waitFor();
    assert.ok(!(await secao.innerText()).includes("1.234,56"));
    s.visao = titular({ membros: [TUDO] });
    await ev.aviso(page);
    assert.ok((await secao.innerText()).includes("Maria parou de compartilhar."));
    assert.equal(dadosN(s), d0);
  });
});

// Abre o painel da Maria quando o resumo NÃO chega (erro): `gancho(s)` arma o servidor antes do clique em "Ver".
const comErro = async (gancho, fn) => {
  const t = await abrirFamilia({ visao: titular({ membros: [MARIA] }), antes: (_c, s) => gancho(s) });
  try { await t.bt("Ver").click(); await fn(t); assert.deepEqual(t.erros, [], "pageerror"); } finally { await t.ctx.close(); }
};

// mutação: tirar o `reler()` do ramo `saiu` do Resumo (VerMembro.tsx): o painel ficaria aberto com o texto
test("404 no resumo: a família é relida e o painel fecha (a lista sem a Maria)", async () => {
  await comErro((s) => { s.agregados = (r) => { s.visao = titular({ membros: [] }); return ERRO(404, "not_found")(r); }; }, async ({ page, s, secao, bt }) => {
    await secao.getByText("1 de").or(secao.getByText("lugares")).first().waitFor();
    await pausa(page, 600);
    assert.equal(familias(s), 2, "a família foi relida uma vez");
    assert.equal(await bt("Voltar").count(), 0, "o painel fechou");
    assert.ok(!(await secao.innerText()).includes("Maria"));
  });
});

// mutação: tirar o `aoSair={reler}` (VerMembro.tsx) ou o ramo `saiu` do efeito de MembroLancamentos.tsx: o painel ficaria aberto com o texto, a família sem releitura
test("404 nos lançamentos: a família é relida uma vez e o painel fecha, sem GET de dados novo", async () => {
  const t = await abrirFamilia({ visao: titular({ membros: [TUDO] }), antes: (_c, s) => { s.lancamentos = (r) => { s.visao = titular({ membros: [] }); return ERRO(404, "not_found")(r); }; } });
  try {
    const { page, s, secao, bt } = t;
    const f0 = familias(s);
    await bt("Ver").click();
    await secao.getByText("1 de").or(secao.getByText("lugares")).first().waitFor();
    await pausa(page, 600);
    assert.equal(familias(s), f0 + 1, "a família foi relida uma vez");
    assert.equal(dadosN(s), 2, "agregados + lançamentos, nada além");
    assert.equal(await bt("Voltar").count(), 0, "o painel fechou");
    assert.ok(!(await secao.innerText()).includes("Maria"), "o 'Ver' some");
    assert.deepEqual(t.erros, [], "pageerror");
  } finally { await t.ctx.close(); }
});

// mutação: tirar o `useEffect` que limpa `aberto` (Titular.tsx): o mesmo ref que volta na lista reabre o painel e lê sem "Ver"
test("o membro sai da lista com o painel aberto e volta com o mesmo ref: painel fechado, nenhum GET de dados", async () => {
  const ev = sseManual();
  await com({ membro: TUDO, antes: ev.antes }, async ({ page, s, bt }) => {
    await page.locator(".fam-lanc").first().or(page.getByText("Nenhum lançamento")).first().waitFor();
    await pausa(page, 400);
    const d0 = dadosN(s);
    s.visao = titular({ membros: [] });
    await ev.aviso(page);
    assert.equal(await bt("Voltar").count(), 0, "o painel fechou");
    s.visao = titular({ membros: [TUDO] });
    await ev.aviso(page);
    await bt("Ver").waitFor();
    assert.equal(await bt("Voltar").count(), 0, "o painel reabriu sozinho");
    assert.equal(dadosN(s), d0, "leu sem Ver");
  });
});

// mutação: tirar o ramo 402 de `motivoDe` (dados.ts) ou o `plano` de `falhaFamilia` (api.ts): cairia em "Não foi possível carregar."
test("402 no resumo: card do Pro com o CTA para /precos", async () => {
  await comErro((s) => { s.agregados = ERRO(402, "family_coverage_required"); }, async ({ secao }) => {
    await secao.getByText("A família é do plano Pro").waitFor();
    const a = secao.getByRole("link", { name: "Ver o Pro" });
    assert.equal(await a.getAttribute("href"), "/precos");
  });
});

// mutação: tirar o `retry` de `leitura` (3 GETs) ou o `refetch` do botão; positivo: o 'Tentar de novo' com o servidor de volta traz o dado
test("5xx no resumo: 'Não foi possível carregar.' depois de 3 GETs; 'Tentar de novo' relê e mostra o dado", async () => {
  let ok = false;
  await comErro((s) => { s.agregados = (r) => (ok ? undefined : r.fulfill({ status: 500, body: "x" })); }, async ({ page, s, secao, bt }) => {
    await secao.getByText("Não foi possível carregar.").waitFor();
    assert.equal(dadosN(s), 3, "retry: 1 + 2");
    ok = true;
    await bt("Tentar de novo").click();
    await page.getByText("R$ 1.234,56").first().waitFor();
    assert.equal(dadosN(s), 4);
  });
});

// mutação: tirar ‎‏؜ do regex de `primeiroNome` (api.ts): o título do painel levaria a marca invisível
test("marcas de direção (U+200E, U+200F, U+061C) no nome não chegam ao título do painel", async () => {
  await com({ membro: { ...MARIA, nome: "‎‏؜Maria‏ Costa" } }, async ({ page }) => {
    assert.equal(await page.locator(".fam-ver-nome").evaluate((h) => h.textContent), "Maria");
  });
});
