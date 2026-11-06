/**
 * O saque da área do afiliado (afiliado/Saque.tsx): caminho de dinheiro. Em cada teste, o comentário de cima diz qual mutação o deixa vermelho.
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { navegador } from "./_guia.mjs";
import { ABAIXO, ATIVA, COM_SAQUE, ate, comAfiliado, confirmar, pedirSaque, quadros } from "./_afiliado.mjs";

const browser = navegador();
const CHAVE = "ana@pix.com";
const campo = (page) => page.locator("#afil-pix");
const sacar = (page) => page.getByRole("button", { name: /^Sacar/ });

// mutação: mandar `{ pix_key, amount }` em solicitarSaque, ou tirar a confirmação: o corpo muda ou o POST sai sem ela
test("confirmação com valor e chave; o corpo é exatamente {pix_key}, com CSRF; sucesso limpa a chave e relê", async () => {
  await comAfiliado(browser(), {}, async (t) => {
    await t.page.locator("#afil-pix").waitFor();
    const dlg = await pedirSaque(t.page, `  ${CHAVE} `);
    const texto = await dlg.innerText();
    assert.match(texto, /R\$ 120,00/);
    assert.ok(texto.includes(CHAVE));
    assert.equal(t.s.posts.length, 0, "nada sai antes de confirmar");
    const antes = t.s.gets;
    await confirmar(t.page).click();
    await t.page.getByText("Saque solicitado.").waitFor();
    assert.equal(t.s.posts.length, 1);
    assert.equal(t.s.posts[0].corpo, JSON.stringify({ pix_key: CHAVE }));
    assert.equal(t.s.posts[0].csrf, "tok-123");
    assert.equal(new URL(t.s.posts[0].url).pathname, "/api/affiliate/payout");
    await ate(() => t.s.gets > antes);
    await t.page.getByText("Você já tem um saque em análise.").waitFor();
    assert.equal(await campo(t.page).inputValue(), "");
    assert.equal((await quadros(t.page))["Em saque"], "R$ 120,00");
  });
});

// mutação: tirar o `limpa.length < 5` de `pedir`: a confirmação abre com a chave vazia
test("sem chave: pede a chave, sem abrir a confirmação nem mandar nada", async () => {
  await comAfiliado(browser(), {}, async (t) => {
    await sacar(t.page).click();
    await t.page.getByText("Informe sua chave Pix.").waitFor();
    assert.equal(await t.page.getByRole("dialog").count(), 0);
    assert.equal(t.s.posts.length, 0);
  });
});

// mutação: tirar o `Cancelar` do fluxo ou chamar `solicitarSaque` no `pedir`: cancelar manda o POST
test("cancelar a confirmação não manda nada e mantém a chave", async () => {
  await comAfiliado(browser(), {}, async (t) => {
    await pedirSaque(t.page);
    await t.page.getByRole("dialog").getByRole("button", { name: "Cancelar" }).click();
    assert.equal(await t.page.getByRole("dialog").count(), 0);
    assert.equal(t.s.posts.length, 0);
    assert.equal(await campo(t.page).inputValue(), CHAVE);
  });
});

// mutação: tirar a trava `voando` do Confirma.tsx: o 2º clique no mesmo tick (antes de o `disabled` chegar ao DOM) manda outro POST
test("dois cliques no mesmo tick no Confirmar dão 1 POST; o Sacar em duplo clique abre 1 diálogo", async () => {
  await comAfiliado(browser(), {}, async (t) => {
    let solta;
    const preso = new Promise((r) => { solta = r; });
    t.s.post = async () => { await preso; };
    await campo(t.page).fill(CHAVE);
    await sacar(t.page).dblclick();
    assert.equal(await t.page.getByRole("dialog").count(), 1);
    await confirmar(t.page).evaluate((b) => { b.click(); b.click(); });
    await ate(() => t.s.posts.length > 0);
    await t.page.waitForTimeout(150);
    assert.equal(t.s.posts.length, 1);
    solta();
    await t.page.getByText("Saque solicitado.").waitFor();
    assert.equal(t.s.posts.length, 1);
  });
});

// mutação: mostrar o texto genérico no lugar de `e.detalhe`: o 400 do servidor some; tirar o `invalidateQueries` do catch: a falha não relê
test("400 mostra o detail do servidor, relê, e a chave fica para tentar de novo", async () => {
  await comAfiliado(browser(), {}, async (t) => {
    t.s.post = (r) => r.fulfill({ status: 400, json: { detail: "Saldo disponível abaixo do mínimo." } });
    await pedirSaque(t.page);
    const antes = t.s.gets;
    await confirmar(t.page).click();
    await t.page.getByRole("alert").getByText("Saldo disponível abaixo do mínimo.").waitFor();
    await ate(() => t.s.gets > antes);
    assert.equal(await campo(t.page).inputValue(), CHAVE);
  });
});

// mutação: tirar o ramo do 429 de `falhaSaque`: o texto cru do limitador vai à tela
test("429: texto em português, sem o corpo do limitador", async () => {
  await comAfiliado(browser(), {}, async (t) => {
    t.s.post = (r) => r.fulfill({ status: 429, json: { detail: "Rate limit exceeded: 10 per 1 hour" } });
    await pedirSaque(t.page);
    await confirmar(t.page).click();
    await t.page.getByRole("alert").getByText("Muitos pedidos de saque").waitFor();
    assert.equal(await t.page.getByText("Rate limit").count(), 0);
  });
});

// mutação: voltar a mostrar `e.detalhe` em todo 403: o texto técnico do CSRF vai à tela
test("403 de CSRF é sessão: nada de 'Token CSRF' na tela", async () => {
  await comAfiliado(browser(), {}, async (t) => {
    t.s.post = (r) => r.fulfill({ status: 403, json: { detail: "Token CSRF inválido ou ausente." } });
    await pedirSaque(t.page);
    await confirmar(t.page).click();
    await t.page.getByRole("alert").getByText("Sua sessão terminou").waitFor();
    assert.equal(await t.page.getByText("CSRF").count(), 0);
  });
});

// mutação: tirar o ramo de `exclusao` de `falhaSaque`: a conta agendada lê "sua sessão terminou", que é falso
test("403 de conta agendada para exclusão: mostra o motivo", async () => {
  await comAfiliado(browser(), {}, async (t) => {
    t.s.post = (r) => r.fulfill({ status: 403, json: { detail: "Esta conta está agendada para exclusão em 2026-12-01." } });
    await pedirSaque(t.page);
    await confirmar(t.page).click();
    await t.page.getByRole("alert").getByText("agendada para exclusão").waitFor();
  });
});

// mutação: tirar o `useEffect` do foco em Saque.tsx: a releitura trava o botão focado e o foco cai no BODY
test("depois do saque e da releitura o foco fica no título da seção, não no BODY", async () => {
  await comAfiliado(browser(), {}, async (t) => {
    await pedirSaque(t.page);
    await confirmar(t.page).click();
    await t.page.getByText("Você já tem um saque em análise.").waitFor();
    await t.page.waitForFunction(() => document.activeElement?.id === "afil-saque", null, { timeout: 3000 });
  });
});

// mutação: tirar o `invalidateQueries`: a resposta perdida deixa a tela sem o saque que o servidor criou, e o botão segue ativo
test("resposta perdida: o saque foi criado e a rede caiu; a releitura mostra 'em análise' e trava o botão", async () => {
  await comAfiliado(browser(), {}, async (t) => {
    t.s.post = (r) => { t.s.corpo = COM_SAQUE; return r.abort(); };
    await pedirSaque(t.page);
    await confirmar(t.page).click();
    await t.page.getByRole("alert").getByText("Não deu para confirmar o saque").waitFor();
    await t.page.getByText("Você já tem um saque em análise.").waitFor();
    assert.equal(await sacar(t.page).isDisabled(), true);
    assert.equal(t.s.posts.length, 1);
  });
});

// mutação: tirar o `!velho` de `pode`: com a releitura falhando, o botão pede um valor que pode não ser o do servidor
test("a releitura falha depois do erro: o valor velho fica à vista com aviso, o botão trava, e a chave não some", async () => {
  await comAfiliado(browser(), {}, async (t) => {
    t.s.post = (r) => r.abort();
    await pedirSaque(t.page);
    t.s.get = (r) => r.fulfill({ status: 400, json: { detail: "x" } }); // 4xx: sem as retentativas do meQuery.retry
    await confirmar(t.page).click();
    await t.page.getByText("Não foi possível atualizar").waitFor();
    assert.equal(await sacar(t.page).isDisabled(), true);
    assert.equal(await campo(t.page).inputValue(), CHAVE);
    t.s.get = null;
    await t.page.getByRole("button", { name: "Tentar de novo" }).click();
    await t.page.waitForFunction(() => !document.body.innerText.includes("Não foi possível atualizar"));
    assert.equal(await sacar(t.page).isEnabled(), true);
    assert.equal(await campo(t.page).inputValue(), CHAVE);
  });
});

// mutação: tirar `abaixo` de `pode`: o botão fica ativo abaixo do mínimo
test("abaixo do mínimo: botão inerte, com o motivo; o clique não abre nada", async () => {
  await comAfiliado(browser(), { corpo: ABAIXO }, async (t) => {
    await t.page.getByText("Você ainda não chegou ao mínimo de R$ 50,00.").waitFor();
    assert.equal(await sacar(t.page).isDisabled(), true);
    await sacar(t.page).click({ force: true });
    assert.equal(await t.page.getByRole("dialog").count(), 0);
  });
});

// mutação: tirar o `stats.requested > 0` ou o `payouts.some` de `aberto`: cada fixture abaixo solta um dos dois lados
test("saque aberto: botão inerte, tanto por stats.requested quanto pela lista de saques", async () => {
  await comAfiliado(browser(), { corpo: { ...COM_SAQUE, stats: { ...COM_SAQUE.stats, available: 200 } } }, async (t) => {
    await t.page.getByText("Você já tem um saque em análise.").waitFor();
    assert.equal(await sacar(t.page).isDisabled(), true);
  });
  await comAfiliado(browser(), { corpo: { ...COM_SAQUE, payouts: [], stats: { ...COM_SAQUE.stats, available: 200 } } }, async (t) => {
    await t.page.getByText("Você já tem um saque em análise.").waitFor();
  });
  await comAfiliado(browser(), { corpo: { ...COM_SAQUE, stats: { ...COM_SAQUE.stats, available: 200, requested: 0 } } }, async (t) => {
    await t.page.getByText("Você já tem um saque em análise.").waitFor();
  });
});

// controle negativo medido à mão: gravar a chave em sessionStorage no `onChange` deixa este teste vermelho (relato do Coder).
test("a chave Pix não vai a storage, cookie, URL, histórico nem console; o campo tem autocomplete off", async () => {
  await comAfiliado(browser(), {}, async (t) => {
    const logs = [];
    t.page.on("console", (m) => logs.push(m.text()));
    await t.page.locator("#afil-pix").waitFor();
    assert.equal(await campo(t.page).getAttribute("autocomplete"), "off");
    await pedirSaque(t.page);
    const vazou = () => t.page.evaluate((k) => {
      const onde = { local: JSON.stringify({ ...localStorage }), sessao: JSON.stringify({ ...sessionStorage }), cookie: document.cookie, url: location.href, estado: JSON.stringify(history.state) };
      return Object.keys(onde).filter((n) => (onde[n] ?? "").includes(k));
    }, CHAVE);
    assert.deepEqual(await vazou(), [], "com a confirmação aberta");
    await confirmar(t.page).click();
    await t.page.getByText("Saque solicitado.").waitFor();
    assert.deepEqual(await vazou(), [], "depois do saque");
    assert.ok(!logs.some((l) => l.includes(CHAVE)), "console");
    assert.equal(await t.page.getByText(CHAVE).count(), 0, "a chave saiu da tela");
  });
});

// mutação: pôr `key={d.stats.available}` no <Saque> ou desmontá-lo em isFetching: a releitura apaga o digitado
test("uma releitura com a tela aberta não apaga o que foi digitado", async () => {
  await comAfiliado(browser(), {}, async (t) => {
    t.s.post = (r) => r.fulfill({ status: 400, json: { detail: "Tente de novo." } });
    await pedirSaque(t.page);
    const antes = t.s.gets;
    t.s.corpo = { ...ATIVA, stats: { ...ATIVA.stats, available: 130 } };
    await confirmar(t.page).click();
    await ate(() => t.s.gets > antes);
    await t.page.getByRole("button", { name: "Sacar R$ 130,00" }).waitFor();
    assert.equal(await campo(t.page).inputValue(), CHAVE);
  });
});

// mutação: tirar `api\/affiliate` do `urlDe` (lib/v2.ts): vira /api/v2/api/affiliate/payout e a tela nem carrega
test("401 de sessão no POST: o auth-refresh renova, repete e o saque sai uma vez só", async () => {
  await comAfiliado(browser(), {}, async (t) => {
    let refresh = 0;
    await t.ctx.route("**/auth/refresh", (r) => { refresh += 1; return r.fulfill({ json: { ok: true, user_id: 1, confirmada: true } }); });
    t.s.post = (r) => (t.s.posts.length === 1 ? r.fulfill({ status: 401, headers: { "WWW-Authenticate": "Bearer" }, json: { detail: "x" } }) : undefined);
    await pedirSaque(t.page);
    await confirmar(t.page).click();
    await t.page.getByText("Saque solicitado.").waitFor();
    assert.equal(refresh, 1);
    assert.equal(t.s.posts.length, 2, "o 401 não criou saque: a repetição é a primeira que passa");
    assert.equal(t.s.posts[1].corpo, JSON.stringify({ pix_key: CHAVE }));
  });
});

// mutação: tirar `useDonoDaTela()` de AreaAfiliado: ninguém relê o perfil, a query não é zerada e a chave da conta A fica no campo com os números dela
test("troca de conta com a tela aberta: a chave digitada e os números da conta anterior não sobram", async () => {
  await comAfiliado(browser(), {}, async (t) => {
    await t.page.locator("#afil-pix").waitFor();
    await campo(t.page).fill(CHAVE);
    let uid = 1;
    await t.ctx.route("**/auth/dashboard-profile", (r) => r.fulfill({ json: { user_id: uid, display_name: null, email: null, whatsapp_linked: true } }));
    uid = 2;
    t.s.corpo = { ...ATIVA, code: "BIA2026", link: "https://pigbankai.com/r/BIA2026", stats: { ...ATIVA.stats, available: 77 } };
    await t.page.evaluate(() => { window.dispatchEvent(new Event("visibilitychange")); window.dispatchEvent(new Event("focus")); document.dispatchEvent(new Event("visibilitychange")); });
    await t.page.getByText("BIA2026", { exact: true }).waitFor();
    assert.equal(await campo(t.page).inputValue(), "");
    assert.equal(await t.page.getByText("ANA2026").count(), 0);
    assert.equal((await quadros(t.page))["Disponível"], "R$ 77,00");
  });
});
