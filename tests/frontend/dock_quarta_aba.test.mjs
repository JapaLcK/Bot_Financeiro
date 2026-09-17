/**
 * INVARIANTE do dock do modo app: em toda carga, a aba da página é a única
 * `active` e a única `pb-live`, tem `aria-current="page"`, a bolha está em
 * cima dela e mostra o ícone QUE ESTÁ DESENHADO nela.
 *
 * O 4º lugar da barra é disputado: "O que pedir" (/comandos-app) ou
 * "Notícias" (/changelog, Pro). Três defeitos quebravam o invariante, os três
 * vindos de a barra guardar à parte "qual aba é a da página" e "qual ícone
 * mostrar", separado das abas que ela desenhou:
 *   (a) ícone por POSIÇÃO FIXA (`TABS[i].icon`): com Notícias desenhada no 4º
 *       lugar a bolha mostrava o ícone de "O que pedir" — em /changelog a toda
 *       carga, e no arrasto sobre Notícias;
 *   (b) o 4º lugar decidia só pelo plano: Pro em /comandos-app ficava sem aba
 *       da página e a bolha caía em Início;
 *   (c) a troca do /auth/me reescreve a aba no lugar e não avisa a bolha nem
 *       o `pb-live` — com (b) consertado ela nunca tira a aba da página.
 * Decisão de produto (opção A): na página "O que pedir" o 4º lugar é "O que
 * pedir" mesmo para quem tem Notícias, e na changelog é Notícias mesmo para
 * quem a barra não trata como Pro; nas outras páginas vale o plano.
 *
 * O cache do plano (`localStorage.pbNewsTab`) é semeado por /manifest.json e
 * NÃO por addInitScript: o init script regravaria o valor antes do reload e
 * apagaria o cenário da carga seguinte, que é metade do bug.
 *
 * `/auth/me` manda `plan` e `changelog_enabled` sempre coerentes (pro com true,
 * free com false): o cache final que a espera exige sai de `me.plan`, e um
 * fixture que separe os dois pode travar a espera.
 *
 * Os arquivos saem do DISCO pela rota do Playwright, não do `_server.mjs`: com
 * suítes em paralelo o `http.server` resetava a conexão do `/app-mode.js`, a
 * barra não montava e o caso ficava vermelho por transporte — mesmo motivo de
 * `toast_fab_app_375.test.mjs`.
 *
 * Service worker BLOQUEADO e espera por ESTADO, não `networkidle` (mesma lição
 * do #447). A home.html registra o /service-worker.js; na carga 2 o
 * `app-mode.js` vinha por ele, o `networkidle` não enxerga pedido que passa
 * pelo worker e o reload voltava com `readyState=interactive`, sem barra —
 * vermelho intermitente sob carga. A espera é: resposta do /auth/me, documento
 * `complete`, 4 abas montadas e o cache com o plano do /auth/me. O cache só
 * PROVA que o syncNewsTab rodou na carga que o troca (a 1ª, com cache semeado
 * diferente do plano): ele grava o cache e troca a aba no mesmo callback
 * síncrono. Nas outras cargas o cache já está no valor final antes de carregar
 * e a espera não garante o callback; ali ele não mexe na barra
 * (`isPro === prev`), então o retrato é o mesmo antes ou depois dele. Um
 * travamento vira vermelho pelo `timeout` de cada teste.
 * E a barra tem de estar ASSENTADA: o bloco `@media (prefers-reduced-motion:
 * reduce)` do `site.css` põe `transition-duration: .01ms` em tudo — só a
 * changelog carrega esse CSS — e o
 * `transform` que o JS escreve na bolha vira transição. Logo após a espera
 * acima a bolha ainda fica alguns quadros em x=10 (sem translate) com o
 * `style.transform` já certo; dois rAF sozinhos não bastam. Só as transições
 * de até 1 ms são esperadas: esperar QUALQUER animação escondia uma bolha que
 * desliza devagar até a aba e chega lá antes da medição.
 *
 * Máquinário de espera/medição (abrir, carregar, assentar, estado, problemas,
 * duasCargas…) mora em `./_dock.mjs`, compartilhado com
 * `dock_quarta_aba_spa.test.mjs` (troca de tela pelo pb-nav) — dois arquivos
 * de teste não podem se importar um ao outro sem registrar os testes em
 * dobro, e juntos os dois passavam do teto do `quality/max-lines` (CLAUDE.md
 * §0.5).
 *
 * Cego a: WKWebView real (o arrasto aqui é mouse, lá é toque); a
 * `env(safe-area-inset-*)`, que vale 0 no headless — nenhuma asserção abaixo
 * depende de inset, só de posição relativa bolha × aba; à troca de tela do
 * pb-nav (SPA): todo caso deste arquivo é carga de documento — a cobertura da
 * troca client-side mora em `dock_quarta_aba_spa.test.mjs` (S1-S3); a uma
 * troca da barra feita DEPOIS de gravar o cache (um passo assíncrono a mais
 * no syncNewsTab): a espera termina no cache e o retrato sai antes dela; à
 * navegação AGENDADA ao soltar: o T4 roda com reduced-motion, onde soltar
 * navega na hora, e a do caminho animado (por `setTimeout`) não é vista aqui —
 * soltar na própria aba com movimento padrão foi conferido fora deste arquivo;
 * e à recarga da própria URL: o href da aba (`/changelog`) difere do caminho
 * servido aqui (`/changelog.html`), então navegação indevida aparece como
 * troca de caminho, e a recarga da própria URL — o que aconteceria em
 * produção — não é vista.
 *
 * Rodar: node --test tests/frontend/dock_quarta_aba.test.mjs
 */
import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import { chromium } from "playwright";
import { PRO, FREE, LIMITE, abrir, assentar, doisFrames, estado, resumo, nomeIco, problemas, duasCargas }
  from "./_dock.mjs";

let browser;
before(async () => { browser = await chromium.launch(); });
after(async () => { await browser?.close(); });

test("T1: Pro em O que pedir com cache 0 — a troca do plano não tira a aba da página", LIMITE, async () => {
  assert.deepEqual(await duasCargas(browser, "/comandos-app.html", "0", PRO, "O que pedir"), []);
});

test("T2: Pro em Notícias com cache 1 — a bolha mostra o ícone de Notícias", LIMITE, async () => {
  assert.deepEqual(await duasCargas(browser, "/changelog.html", "1", PRO, "Notícias"), []);
});

test("T3: cache velho nas duas direções, e não-Pro na changelog — a página vem antes do plano", LIMITE, async () => {
  const erros = [
    ...await duasCargas(browser, "/changelog.html", "0", PRO, "Notícias"),
    ...await duasCargas(browser, "/comandos-app.html", "1", FREE, "O que pedir"),
    // Quem o servidor deixa abrir a changelog sem ser `plan === "pro"` (Plus,
    // trial): a página vem antes do plano, então Notícias fica ativa.
    ...await duasCargas(browser, "/changelog.html", "0", FREE, "Notícias"),
  ];
  assert.deepEqual(erros, []);
});

test("T4: arrasto da bolha em Notícias — o ícone é o da aba sob ela, e soltar na página não navega", LIMITE, async () => {
  const { ctx, page } = await abrir(browser, "/changelog.html", "1", PRO);
  const erros = [];
  try {
    const s0 = await estado(page);
    const box = await page.locator(".pb-dock-bead").boundingBox();
    const y = box.y + box.height / 2;
    await page.mouse.move(box.x + box.width / 2, y);
    await page.mouse.down();
    for (let i = 0; i < s0.abas.length; i++) {
      await page.mouse.move(s0.abas[i].cx, y, { steps: 8 });
      await doisFrames(page);
      const s = await estado(page);
      const alvo = s.abas[i];
      const vivas = s.abas.filter((x) => x.live).map((x) => x.label);
      if (vivas.length !== 1 || vivas[0] !== alvo.label) {
        erros.push(`arrasto sobre "${alvo.label}": pb-live em [${vivas}] — ${resumo(s)}`);
      }
      if (s.beadIco !== alvo.ico) {
        erros.push(`arrasto sobre "${alvo.label}": bolha com ícone "${nomeIco(s.beadIco)}" — ${resumo(s)}`);
      }
    }
    const casa = s0.abas.find((a) => a.label === "Notícias");
    await page.mouse.move(casa.cx, y, { steps: 8 });
    // Dois frames parados antes de soltar: o tick zera a velocidade, senão a
    // projeção do soltar (x + vel*5) pode encaixar na vizinha.
    await doisFrames(page);
    await page.mouse.up();
    // Se soltar navegou, a página nova não tem barra e o assentar lança antes
    // da checagem de caminho: o motivo real sai do catch, pela URL.
    await assentar(page).catch((e) => {
      const path = new URL(page.url()).pathname;
      throw path === "/changelog.html" ? e : new Error(`soltar na aba da página navegou para ${path} — ${e.message}`);
    });
    const fim = await estado(page);
    if (fim.path !== "/changelog.html") erros.push(`soltar na aba da página navegou para ${fim.path}`);
    erros.push(...problemas(fim, "Notícias", "depois de soltar"));
  } finally { await ctx.close(); }
  assert.deepEqual(erros, []);
});

test("T5 (positivos): Free em O que pedir; fora da disputa o plano decide — Pro ganha Notícias, Free não", LIMITE, async () => {
  const quarta = (rotulo) => (s, onde) => (s.abas[2]?.label === rotulo ? []
    : [`${onde}: 4º lugar "${s.abas[2]?.label}", esperava "${rotulo}" — ${resumo(s)}`]);
  const erros = [
    ...await duasCargas(browser, "/comandos-app.html", "0", FREE, "O que pedir"),
    ...await duasCargas(browser, "/home.html", "0", PRO, "Início", quarta("Notícias")),
    // Cache velho de Pro num Free: sem este caso "Notícias para todo mundo" passava.
    ...await duasCargas(browser, "/home.html", "1", FREE, "Início", quarta("O que pedir")),
  ];
  assert.deepEqual(erros, []);
});
