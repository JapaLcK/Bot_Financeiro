// O seletor de bancos (o módulo REAL /open-finance-connect.js, com um SDK da Pluggy falso) dentro da tela Bancos conectados do /painel.
//   · o aviso do seletor (teto do plano, /auth/me fora) aparece ACIMA do overlay dele, que tem z-index 9999 (como o toast do /settings);
//   · sair da página com o widget aberto não descarta o item que a Pluggy autorizar depois (senão o consentimento fica órfão: a Pluggy tem o item e o PigBank não);
//   · duplo clique no "+" não fecha o seletor; nome de banco absurdo não empurra o "Conectar" para fora do modal.
import { test } from "node:test";
import assert from "node:assert/strict";
import { abrir, navegador } from "./_guia.mjs";
import { con, servidorBancos } from "./_bancos.mjs";

const browser = navegador();
void browser;

const pluggy = (c) => ({ ...c, provider: "pluggy" }); // só conexão `pluggy` conta no teto do módulo
const CONEXOES = () => [con(11, "Nubank"), con(12, "Inter"), con(13, "Itaú")].map(pluggy);
const ME_OK = { app_access: true, of_ui_enabled: true, of_banks_max: 10, cobranca_em_atraso: false };

// O SDK da Pluggy falso: guarda as opções do widget em `window.__pc` (os callbacks `onSuccess`/`onError`/`onClose`) e abre sem rede.
const SDK = () => { window.__pcs = []; window.PluggyConnect = class { constructor(o) { window.__pc = o; window.__pcs.push(o); } init() { return Promise.resolve(); } }; };

async function painel({ me, conexoes = CONEXOES(), conectores, tela = {}, tokenMs = 0 } = {}) {
  const s = servidorBancos({ bancos: "padrao", conexoes, seletorReal: true, ...(conectores ? { conectores } : {}) });
  s.posts = [];
  const p = await abrir({
    guia: "concluido", rota: "/conta/bancos", ...tela,
    antes: async (ctx) => {
      await s.antes(ctx);
      await ctx.addInitScript(SDK);
      await ctx.route((u) => u.pathname === "/auth/me", (r) => (me === 500 ? r.fulfill({ status: 500, json: { detail: "x" } }) : r.fulfill({ json: me ?? ME_OK })));
      await ctx.route((u) => u.pathname === "/open-finance/7/connect-token", async (r) => { if (tokenMs) await new Promise((ok) => setTimeout(ok, tokenMs)); return r.fulfill({ json: { accessToken: "tok", includeSandbox: false, products: ["ACCOUNTS"] } }).catch(() => {}); });
      await ctx.route((u) => u.pathname === "/open-finance/7/pluggy-item", (r) => { s.posts.push(r.request().postDataJSON()); return r.fulfill({ json: { ok: true, connections: [], accounts: [], transactions: [] } }); });
    },
  });
  return { ...p, srv: s };
}
const maisDe = (page, nome) => page.getByRole("link", { name: `Conectar ${nome}`, exact: true });
const esperaDisponiveis = (page) => page.getByRole("region", { name: "Disponíveis para conectar" }).waitFor({ timeout: 8000 });
const abertoNoPicker = (page) => page.evaluate(() => !!document.querySelector("#bankpick-overlay.open"));
const clicaConectar = async (page) => { await page.locator("#bankpick-go:not([disabled])").waitFor(); await page.waitForTimeout(400); await page.locator("#bankpick-go").click(); }; // passada a janela do duplo clique (350 ms)

for (const [rotulo, me, texto] of [
  ["/auth/me fora do ar (não deu para confirmar o plano)", 500, /Não deu pra confirmar seu plano agora/],
  ["teto do plano atingido com o /limite do painel velho", { ...ME_OK, of_banks_max: 2 }, /Seu plano conecta até 2 bancos/],
]) {
  test(`o aviso do seletor fica ACIMA do overlay: ${rotulo}`, async () => {
    for (const [width, height] of [[1280, 800], [390, 844]]) {
      const { ctx, page, erros } = await painel({ me, tela: { width, height } });
      await esperaDisponiveis(page);
      await maisDe(page, "Bradesco").click();
      await clicaConectar(page);
      const aviso = page.getByRole("alert");
      await aviso.waitFor({ timeout: 8000 });
      const r = { texto: await aviso.innerText(), aberto: await abertoNoPicker(page), cima: await page.evaluate(() => {
        const e = document.querySelector('[role="alert"]'), b = e.getBoundingClientRect(), t = document.elementFromPoint(b.left + b.width / 2, b.top + b.height / 2);
        return { achou: !!t && (t === e || e.contains(t)), overlay: !!t?.closest("#bankpick-overlay") };
      }) };
      await ctx.close();
      assert.match(r.texto, texto, `${width}`);
      assert.equal(r.aberto, true, `${width}: o seletor segue aberto (o problema é o aviso atrás dele)`);
      assert.deepEqual(r.cima, { achou: true, overlay: false }, `${width}: o aviso está atrás do overlay`);
      assert.deepEqual(erros, []);
    }
  });
}

test("o aviso do seletor some ao abrir de novo e ao tocar nele; sem conexão em curso, sair da página destrói o seletor (o overlay sai do DOM)", async () => {
  const { ctx, page } = await painel({ me: 500 });
  await esperaDisponiveis(page);
  await maisDe(page, "Bradesco").click();
  await clicaConectar(page);
  await page.getByRole("alert").waitFor({ timeout: 8000 });
  await page.keyboard.press("Escape"); // fecha o seletor, o aviso fica
  assert.equal(await page.getByRole("alert").count(), 1);
  await maisDe(page, "Bradesco").click(); // nova abertura: a mensagem velha sai
  const aoReabrir = await page.getByRole("alert").count();
  await page.keyboard.press("Escape");
  await page.evaluate(() => { location.hash = "#/"; });
  await page.waitForFunction(() => !document.querySelector("#bankpick-overlay"), null, { timeout: 8000 }); // controle positivo: sem conexão em curso o destroy limpa tudo
  await ctx.close();
  assert.equal(aoReabrir, 0);
});

test("sair da página com o widget aberto NÃO descarta o item autorizado depois: o POST /pluggy-item sai com o item, sem erro de página", async () => {
  const { ctx, page, srv, erros } = await painel();
  await esperaDisponiveis(page);
  await maisDe(page, "Bradesco").click();
  await clicaConectar(page);
  await page.waitForFunction(() => !!window.__pc, null, { timeout: 8000 }); // o widget foi criado
  await page.evaluate(() => { location.hash = "#/"; }); // o voltar do navegador, com o widget ainda aberto
  await page.waitForFunction(() => document.querySelector("#page-title")?.textContent.trim() !== "Bancos conectados", null, { timeout: 8000 });
  await page.evaluate(() => { window.__pc.onSuccess({ item: { id: "item-x" } }); window.__pc.onClose(); }); // a Pluggy autoriza DEPOIS
  for (const fim = Date.now() + 5000; !srv.posts.length && Date.now() < fim;) await page.waitForTimeout(50);
  await page.waitForTimeout(300);
  await ctx.close();
  assert.deepEqual(srv.posts, [{ item: { id: "item-x" } }]);
  assert.deepEqual(erros, []);
});

test("duplo clique no '+' de um disponível: o seletor segue aberto e o foco fica dentro do modal (1280×800 e 390×844)", async () => {
  for (const [width, height] of [[1280, 800], [390, 844]]) {
    const { ctx, page } = await painel({ tela: { width, height } });
    await esperaDisponiveis(page);
    await maisDe(page, "Bradesco").dblclick();
    await page.waitForTimeout(150);
    const r = await page.evaluate(() => ({ aberto: !!document.querySelector("#bankpick-overlay.open"), dentro: !!document.querySelector("#bankpick-overlay .bankpick-modal")?.contains(document.activeElement) }));
    await ctx.close();
    assert.deepEqual(r, { aberto: true, dentro: true }, `${width}×${height}`);
  }
});

test("clique no fundo ainda fecha o seletor passada a janela do duplo clique (o comportamento normal do fundo)", async () => {
  const { ctx, page } = await painel();
  await esperaDisponiveis(page);
  await maisDe(page, "Bradesco").click();
  await page.locator("#bankpick-overlay.open").waitFor();
  await page.waitForTimeout(600);
  await page.mouse.click(10, 400); // fora do modal
  await page.locator("#bankpick-overlay.open").waitFor({ state: "detached", timeout: 3000 }).catch(() => {});
  const aberto = await abertoNoPicker(page);
  await ctx.close();
  assert.equal(aberto, false);
});

test("nome de banco de 300 letras sem espaço a 320×640: o 'Conectar Open Finance' fica dentro do modal e da tela", async () => {
  const conectores = [{ id: 301, name: `Itaú ${"W".repeat(300)}`, type: "PERSONAL_BANK", color: "EC7000", logo: "", inv: false }];
  const { ctx, page } = await painel({ conectores, tela: { width: 320, height: 640 } });
  await esperaDisponiveis(page);
  await page.getByRole("link", { name: /^Conectar Itaú W/ }).click();
  await page.locator("#bankpick-go:not([disabled])").waitFor();
  const r = await page.evaluate(() => {
    const m = document.querySelector("#bankpick-overlay .bankpick-modal").getBoundingClientRect(), b = document.getElementById("bankpick-go").getBoundingClientRect();
    return { dentro: b.left >= m.left - 1 && b.right <= m.right + 1 && b.right <= innerWidth, lateral: document.documentElement.scrollWidth - innerWidth };
  });
  await ctx.close();
  assert.deepEqual(r, { dentro: true, lateral: 0 });
});

test("2º clique no mesmo ponto do '+' (100 e 250 ms depois, caindo numa linha do modal) NÃO troca o banco; passada a janela, clicar numa linha escolhe o banco", async () => {
  for (const [width, height] of [[390, 844], [360, 740]]) {
    const pre = await painel({ tela: { width, height } }); // pré-condição: sob o ponto do "+" há OUTRA linha do modal (senão o teste não mede nada)
    await esperaDisponiveis(pre.page);
    await maisDe(pre.page, "Bradesco").scrollIntoViewIfNeeded(); // a página tem 4 cards: o "Conectar" dos disponíveis fica abaixo da dobra e o ponto tem de estar na tela
    const bb = await maisDe(pre.page, "Bradesco").boundingBox();
    await maisDe(pre.page, "Bradesco").click();
    await pre.page.locator("#bankpick-overlay.open .bank-row").first().waitFor();
    // O botão agora fica abaixo da dobra e a página rola: acha, com o modal aberto, a altura (em tela) de uma linha de OUTRO banco e quanto rolar para o centro do botão cair nela.
    const alvo = await pre.page.evaluate(([cx, cy]) => {
      const r = [...document.querySelectorAll("#bankpick-overlay .bank-row")].filter((e) => !/Bradesco/.test(e.textContent)).map((e) => e.getBoundingClientRect()).find((b) => b.top > 200 && b.bottom < innerHeight - 60);
      return r ? { y: (r.top + r.bottom) / 2, rola: cy - (r.top + r.bottom) / 2, sob: document.elementFromPoint(cx, (r.top + r.bottom) / 2)?.closest(".bank-row")?.textContent ?? null } : null;
    }, [bb.x + bb.width / 2, bb.y + bb.height / 2]);
    await pre.ctx.close();
    assert.ok(alvo && alvo.sob && !/Bradesco/.test(alvo.sob), `${width}×${height}: precisa haver OUTRA linha sob o cursor (${alvo?.sob})`);
    for (const intervalo of [100, 250]) {
      // A janela do módulo é de 350 ms: o que vale é o intervalo REAL entre os dois cliques (medido na página), não o `waitForTimeout`; sob carga o
      // `mouse.click` demora, e um intervalo real >= 330 ms não prova nada (cai fora da janela): repete o caso em página nova, até 4 vezes.
      let r, real, rotulo = `${width}×${height} +${intervalo}ms`;
      for (let tentativa = 0; tentativa < 4 && !(real < 330); tentativa++) {
        const { ctx, page } = await painel({ tela: { width, height } });
        await esperaDisponiveis(page);
        await page.evaluate(() => { window.__t = []; document.addEventListener("click", () => window.__t.push(performance.now()), true); });
        await maisDe(page, "Bradesco").scrollIntoViewIfNeeded();
        await page.evaluate((rola) => window.scrollBy(0, rola), alvo.rola); // o centro do botão cai na linha de outro banco que o modal vai pôr ali
        const b = await maisDe(page, "Bradesco").boundingBox();
        const x = b.x + b.width / 2, y = b.y + b.height / 2;
        assert.ok(Math.abs(y - alvo.y) < 2, `${rotulo}: o botão ficou em ${y}, não em ${alvo.y}`);
        await page.mouse.click(x, y);
        await page.waitForTimeout(intervalo); // nada entre os dois cliques: o tempo medido é o do duplo clique
        await page.mouse.click(x, y);
        await page.waitForTimeout(50);
        real = await page.evaluate(() => window.__t[1] - window.__t[0]);
        r = { aberto: await abertoNoPicker(page), escolhido: await page.locator(".bankpick-count").innerText() };
        await ctx.close();
      }
      assert.ok(real < 330, `${rotulo}: o intervalo real ficou ${Math.round(real)} ms em 4 tentativas (máquina carregada)`);
      assert.equal(r.aberto, true, rotulo);
      assert.match(r.escolhido, /Bradesco/, `${rotulo}: o 2º clique trocou o banco`);
    }
  }
  const { ctx, page } = await painel({ tela: { width: 390, height: 844 } }); // controle positivo: passada a janela a linha vale
  await esperaDisponiveis(page);
  await maisDe(page, "Bradesco").click();
  await page.locator("#bankpick-overlay.open .bank-row").first().waitFor();
  await page.waitForTimeout(450);
  await page.locator("#bankpick-overlay .bank-row", { hasText: "Santander" }).click();
  const escolhido = await page.locator(".bankpick-count").innerText();
  await ctx.close();
  assert.match(escolhido, /Santander/);
});

test("sair da página enquanto o /connect-token ainda está em voo aborta tudo: nenhum widget abre depois, o overlay sai e o '+' volta a abrir o seletor", async () => {
  const { ctx, page, erros } = await painel({ tokenMs: 1000 });
  await esperaDisponiveis(page);
  await maisDe(page, "Bradesco").click();
  await clicaConectar(page); // o /connect-token fica 1 s em voo
  await page.evaluate(() => { location.hash = "#/"; });
  await page.waitForFunction(() => document.querySelector("#page-title")?.textContent.trim() !== "Bancos conectados", null, { timeout: 8000 });
  await page.waitForTimeout(1500);
  const depois = await page.evaluate(() => ({ widgets: window.__pcs.length, overlay: !!document.querySelector("#bankpick-overlay") }));
  await page.evaluate(() => { location.hash = "#/conta/bancos"; });
  await esperaDisponiveis(page);
  await maisDe(page, "Bradesco").click(); // a guarda "uma conexão por vez" ficou livre: o open() não é ignorado
  const reabriu = await abertoNoPicker(page);
  await ctx.close();
  assert.deepEqual(depois, { widgets: 0, overlay: false });
  assert.equal(reabriu, true);
  assert.deepEqual(erros, []);
});
