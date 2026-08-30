/**
 * Smoke de produção pós-deploy — as páginas LOGADAS, com JavaScript de verdade.
 *
 * O smoke HTTP (scripts/smoke_prod.py) vê o HTML que o servidor manda; ele não
 * vê o que o JS faz depois. Os cards do dashboard são montados por dezenas de
 * fetches do dashboard.js, e foi exatamente ali que apareceu o card "TODAS AS
 * CATEGORIAS" renderizando `Erro: (HTTP 404) {"message":"Application not
 * found"}` com o servidor respondendo 200 na página.
 *
 * Read-only: só navega e troca de aba. Não cria, não edita, não apaga nada.
 *
 * Credenciais vêm do ambiente (GitHub Secrets no CI) — nunca do código:
 *     SMOKE_EMAIL=... SMOKE_PASSWORD=... node scripts/smoke_prod_ui.mjs
 *
 * A conta tem de ser exclusiva de automação, sem dados reais, SEM MFA (o
 * segundo fator não tem como ser respondido aqui) e com plano ativo.
 */
import { chromium } from "playwright";

const BASE = process.env.SMOKE_BASE || "https://pigbankai.com";
const EMAIL = process.env.SMOKE_EMAIL;
const SENHA = process.env.SMOKE_PASSWORD;

const TELAS = ["/app", "/home", "/settings"];

/**
 * Verificação POSITIVA — o que tem de EXISTIR na tela, não o que não pode estar.
 *
 * Procurar a palavra "Erro" pega o card que falhou reclamando; não pega o card
 * que simplesmente sumiu (fetch pendurado para sempre, view que não montou,
 * JS que morreu antes de chegar nela). Uma tela em branco passa em qualquer
 * asserção negativa.
 *
 * O alvo de cada aba é o contêiner de STATS, não a lista: ele é preenchido pelo
 * render de sucesso mesmo com a conta vazia (as tiles mostram zero). Uma
 * asserção de "tem itens" reprovaria na conta de automação, que não tem dado.
 *
 * E o alvo dentro dele é `.stat-value`, não o texto do contêiner: o
 * loadBudgetsView (dashboard.js:2623) escreve um ESQUELETO síncrono antes do
 * fetch, com os rótulos já preenchidos — "tem texto" passava nele, e um fetch
 * pendurado para sempre saía verde. O esqueleto põe `.sk`; só o render de
 * sucesso põe `.stat-value` com número dentro.
 *
 * São duas abas, não as onze: `categories` é a que quebrou de verdade e
 * `budgets` é a irmã de outro endpoint. Amplia-se quando houver motivo medido,
 * não por simetria.
 */
const ANCORAS = [
  { aba: "categories", seletor: "#categories-stats", nome: "Categorias" },
  { aba: "budgets", seletor: "#budgets-stats", nome: "Orçamentos" },
];

// Texto de erro renderizado DENTRO da tela.
const MARCAS_DE_ERRO = ["Application not found", "Erro: (HTTP", "Internal Server Error"];

if (!EMAIL || !SENHA) {
  console.error("SMOKE_EMAIL e SMOKE_PASSWORD são obrigatórios.");
  process.exit(2);
}

const falhas = [];
const navegador = await chromium.launch();
const contexto = await navegador.newContext({ viewport: { width: 1280, height: 900 } });
const pagina = await contexto.newPage();

let telaAtual = "/login";

/**
 * Respostas >= 400 que são o comportamento CORRETO do produto, não defeito.
 * Cada uma é (caminho, status, quando) — o par exato, nunca o caminho inteiro:
 * um 500 em /auth/validate continua reprovando.
 */
const ESPERADOS = [
  // nav-auth.js:242 pergunta "estou logado?" com um fetch. Na tela de login a
  // resposta certa é 401 — só ali, para não engolir sessão caindo em tela logada.
  { caminho: "/auth/validate", status: 401, so_em: "/login" },
  { caminho: "/auth/refresh", status: 401, so_em: "/login" },
  // frontend/routes/affiliates.py:4, no próprio docstring do módulo: "404 se o
  // user não é afiliado". A conta de automação não é, e nunca vai ser.
  { caminho: "/api/affiliate/me", status: 404, so_em: null },
];

const esperado = (caminho, status) =>
  ESPERADOS.some(
    (e) => e.caminho === caminho && e.status === status && (e.so_em === null || e.so_em === telaAtual),
  );

// Rede: só o que a NOSSA origem serve. Um 4xx de CDN, do Meta Pixel ou de
// fonte externa não é regressão do PigBank e deixaria o smoke instável — o
// preço de reprovar por isso é o teste virar ruído e parar de ser lido.
pagina.on("response", (r) => {
  if (r.status() < 400 || !r.url().startsWith(BASE)) return;
  const caminho = new URL(r.url()).pathname;
  if (esperado(caminho, r.status())) return;
  falhas.push(`[${telaAtual}] HTTP ${r.status()} em ${r.url().replace(BASE, "")}`);
});

// pageerror = exceção que subiu até o topo e interrompeu a execução do script
// que a lançou. Vale mesmo vindo de terceiro: Chart.js estourando deixa o
// gráfico sem desenhar, e isso É a tela quebrada (o P1-05 do roteiro).
pagina.on("pageerror", (e) => falhas.push(`[${telaAtual}] erro de JS: ${e.message}`));

// console.error: só o de script da nossa origem. `console.error` de biblioteca
// de terceiro é barulho dela, não defeito nosso. Sem `location.url` (chamada
// de contexto que o CDP não atribui) o registro é ambíguo — não reprova.
pagina.on("console", (m) => {
  if (m.type() !== "error") return;
  // "Failed to load resource" é o navegador narrando a MESMA resposta que o
  // listener acima já tratou — e sem o status nem a URL, que ele tem. Contar
  // os dois dobra toda falha de rede e ressuscita as que ali são esperadas.
  if (m.text().startsWith("Failed to load resource")) return;
  const origem = m.location()?.url || "";
  if (origem.startsWith(BASE)) {
    falhas.push(`[${telaAtual}] console.error: ${m.text().slice(0, 200)}`);
  }
});

try {
  // ── login ───────────────────────────────────────────────────────────────
  await pagina.goto(`${BASE}/login`, { waitUntil: "domcontentloaded", timeout: 30000 });
  await pagina.fill("#email", EMAIL);
  await pagina.fill("#senha", SENHA);
  await pagina.click("#btn-login");
  await pagina.waitForURL((u) => !u.pathname.startsWith("/login"), { timeout: 30000 });

  if (await pagina.locator("#form-mfa").isVisible().catch(() => false)) {
    throw new Error("PARADA: a conta de automação tem MFA ligado — o smoke não passa do 2º fator");
  }
  if (new URL(pagina.url()).pathname.startsWith("/precos")) {
    throw new Error(`PARADA: login caiu em ${pagina.url().replace(BASE, "")} — a conta não tem plano ativo`);
  }

  // ── telas ───────────────────────────────────────────────────────────────
  for (const tela of TELAS) {
    telaAtual = tela;
    const resp = await pagina.goto(`${BASE}${tela}`, { waitUntil: "load", timeout: 30000 });
    if (!resp || resp.status() !== 200) {
      falhas.push(`[${tela}] a página respondeu ${resp ? resp.status() : "nada"}`);
      continue;
    }
    // `goto` segue redirect e devolve a resposta FINAL: 200 sozinho não prova
    // que chegou onde pediu. Se /home passar a desviar para /app, a tela some
    // e o loop passa — nenhuma das marcas de erro está numa página saudável.
    const chegou = new URL(pagina.url()).pathname;
    if (chegou !== tela) {
      falhas.push(`[${tela}] desviou para ${chegou} — a rota pedida não foi servida`);
      continue;
    }
    // Os cards enchem por fetch depois do load. networkidle sozinho é frágil
    // (o WebSocket do dashboard nunca fica idle), então: idle com teto, e segue.
    await pagina.waitForLoadState("networkidle", { timeout: 15000 }).catch(() => {});
    const texto = await pagina.locator("body").innerText();
    for (const marca of MARCAS_DE_ERRO) {
      if (texto.includes(marca)) falhas.push(`[${tela}] a tela mostra "${marca}"`);
    }
  }

  // ── âncoras positivas, no dashboard ─────────────────────────────────────
  telaAtual = "/app";
  await pagina.goto(`${BASE}/app`, { waitUntil: "load", timeout: 30000 });
  for (const { aba, seletor, nome } of ANCORAS) {
    telaAtual = `/app#${aba}`;
    // navigateTo é global (dashboard.js é script clássico) e é o mesmo caminho
    // do clique no menu: troca a view E dispara o load daquela aba.
    const existe = await pagina.evaluate((v) => {
      if (typeof navigateTo !== "function") return false;
      navigateTo(v);
      return true;
    }, aba);
    if (!existe) {
      falhas.push(`[${telaAtual}] navigateTo() não existe — o dashboard.js não carregou`);
      continue;
    }
    try {
      await pagina.locator(seletor).waitFor({ state: "visible", timeout: 20000 });
      await pagina.waitForFunction(
        (s) => {
          const caixa = document.querySelector(s);
          if (!caixa || caixa.querySelector(".sk")) return false; // ainda no esqueleto
          const valores = [...caixa.querySelectorAll(".stat-value")];
          return valores.length > 0 && valores.every((v) => v.innerText.trim().length > 0);
        },
        seletor,
        { timeout: 20000 },
      );
    } catch {
      const grid = await pagina
        .locator(seletor.replace("-stats", "-grid") + ", " + seletor.replace("-stats", "-list"))
        .innerText()
        .catch(() => "");
      falhas.push(
        `[${telaAtual}] o card "${nome}" (${seletor}) não terminou de carregar` +
          (grid ? ` — a lista mostra: ${grid.slice(0, 120).replace(/\s+/g, " ")}` : " — e a lista está vazia"),
      );
    }
  }
} catch (e) {
  falhas.push(`[${telaAtual}] ${e.message.split("\n")[0]}`);
} finally {
  await navegador.close();
}

if (falhas.length) {
  console.error(`\n${falhas.length} falha(s):`);
  for (const f of falhas) console.error(`  FALHA: ${f}`);
  process.exit(1);
}
console.log(
  `smoke de UI ok: ${TELAS.length} telas logadas + ${ANCORAS.length} cards carregados, ` +
    `sem erro de rede nem de console na origem`,
);
