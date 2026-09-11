/**
 * Coleta uma baseline comparável das rotas públicas pelo navegador de verdade.
 * Não substitui RUM/Lighthouse: registra a comparação entre versões do site.
 * Exemplo: node scripts/medir_frontend_publico.mjs --runs 5
 */
import { mkdir, writeFile } from "node:fs/promises";
import { execFileSync } from "node:child_process";
import { dirname, resolve } from "node:path";
import { chromium, devices } from "playwright";

const ORIGIN_PADRAO = "https://pigbankai.com";
const ROTAS_PADRAO = ["/", "/precos", "/como-funciona"];
const PERFIL_PADRAO = "iPhone 13";
const ESPERA_ESTABILIZACAO_MS = 1_000;
const LIMITE_LOAD_MS = 10_000;
const REDE_4G = {
  offline: false,
  latency: 150,
  downloadThroughput: 1_600 * 1024 / 8,
  uploadThroughput: 750 * 1024 / 8,
  connectionType: "cellular4g",
};

const informar = mensagem => process.stdout.write(`${mensagem}\n`);
const informarErro = mensagem => process.stderr.write(`${mensagem}\n`);

function commitLocal() {
  try {
    return execFileSync("git", ["rev-parse", "HEAD"], { encoding: "utf8" }).trim();
  } catch {
    return null;
  }
}

function uso() {
  return `Uso: node scripts/medir_frontend_publico.mjs [opções]

  --origin URL       Origem a medir (padrão: ${ORIGIN_PADRAO})
  --routes LISTA      Rotas separadas por vírgula (padrão: ${ROTAS_PADRAO.join(",")})
  --runs N            Repetições medidas por cache e rota (padrão: 5)
  --profile NOME      Dispositivo Playwright (padrão: ${PERFIL_PADRAO})
  --sem-throttle      Não emula rede 4G nem CPU 4x
  --output ARQUIVO    JSON de saída (padrão: tmp/frontend-baseline-<data>.json)
  --help              Mostra esta ajuda

Cada rota recebe N contextos frios e N medições quentes. A visita que aquece o
cache não entra na amostra quente. O JSON registra todas as amostras, a mediana
e a dispersão; não edite o resultado manualmente.`;
}

// eslint-disable-next-line complexity -- opções independentes da CLI precisam validar cada argumento.
function argumentos(argv) {
  const opcoes = {
    origin: ORIGIN_PADRAO,
    routes: ROTAS_PADRAO,
    runs: 5,
    profile: PERFIL_PADRAO,
    throttle: true,
    output: "",
  };
  for (let i = 0; i < argv.length; i += 1) {
    const atual = argv[i];
    if (atual === "--help") return { help: true };
    if (atual === "--sem-throttle") {
      opcoes.throttle = false;
      continue;
    }
    const valor = argv[i + 1];
    if (!valor || valor.startsWith("--")) throw new Error(`faltou valor para ${atual}`);
    i += 1;
    if (atual === "--origin") opcoes.origin = valor.replace(/\/$/, "");
    else if (atual === "--routes") {
      opcoes.routes = valor.split(",").map(item => item.trim()).filter(Boolean);
    } else if (atual === "--runs") opcoes.runs = Number.parseInt(valor, 10);
    else if (atual === "--profile") opcoes.profile = valor;
    else if (atual === "--output") opcoes.output = valor;
    else throw new Error(`opção desconhecida: ${atual}`);
  }
  if (!Number.isInteger(opcoes.runs) || opcoes.runs < 1) {
    throw new Error("--runs precisa ser um inteiro maior que zero");
  }
  if (!opcoes.routes.length || opcoes.routes.some(rota => !rota.startsWith("/"))) {
    throw new Error("--routes precisa conter rotas absolutas, como /precos");
  }
  if (!devices[opcoes.profile]) throw new Error(`perfil Playwright inexistente: ${opcoes.profile}`);
  return opcoes;
}

function mediana(valores) {
  const ordenados = valores.filter(Number.isFinite).sort((a, b) => a - b);
  if (!ordenados.length) return null;
  const meio = Math.floor(ordenados.length / 2);
  return ordenados.length % 2 ? ordenados[meio] : (ordenados[meio - 1] + ordenados[meio]) / 2;
}

function resumo(amostras) {
  const chaves = [
    "ttfb_ms", "fcp_ms", "lcp_ms", "load_ms", "transferencia_total_bytes",
    "transferencia_primeira_origem_bytes", "transferencia_terceiros_bytes",
  ];
  return Object.fromEntries(chaves.map(chave => {
    const valores = amostras.map(item => item.metricas[chave]).filter(Number.isFinite);
    return [chave, {
      mediana: mediana(valores),
      minimo: valores.length ? Math.min(...valores) : null,
      maximo: valores.length ? Math.max(...valores) : null,
    }];
  }));
}

async function configurarThrottle(page, ativo) {
  if (!ativo) return;
  const cdp = await page.context().newCDPSession(page);
  await cdp.send("Network.enable");
  await cdp.send("Network.emulateNetworkConditions", REDE_4G);
  await cdp.send("Emulation.setCPUThrottlingRate", { rate: 4 });
}

async function medirUmaVez(contexto, url, throttle) {
  const page = await contexto.newPage();
  const transferencias = [];
  const pendentes = [];
  page.on("response", resposta => {
    // A API de tamanhos pertence a Request (e não a Response) no Playwright.
    // Mantemos a resposta para URL/status, que descrevem o recurso entregue.
    pendentes.push(resposta.request().sizes().then(tamanhos => transferencias.push({
      url: resposta.url(),
      tipo: resposta.request().resourceType(),
      status: resposta.status(),
      bytes: tamanhos.responseBodySize + tamanhos.responseHeadersSize,
    })).catch(() => {}));
  });
  await configurarThrottle(page, throttle);
  const inicio = Date.now();
  const resposta = await page.goto(url, { waitUntil: "domcontentloaded", timeout: 30_000 });
  let loadConcluido = true;
  try {
    await page.waitForLoadState("load", { timeout: LIMITE_LOAD_MS });
  } catch {
    // Terceiro que não termina não invalida FCP/LCP/bytes. Fica explícito no JSON.
    loadConcluido = false;
  }
  await page.waitForTimeout(ESPERA_ESTABILIZACAO_MS);
  const navegacao = await page.evaluate(() => {
    const entrada = performance.getEntriesByType("navigation")[0];
    const fcp = performance.getEntriesByName("first-contentful-paint")[0];
    return {
      ttfb_ms: entrada ? entrada.responseStart : null,
      fcp_ms: fcp ? fcp.startTime : null,
      load_ms: entrada?.loadEventEnd > 0 ? entrada.loadEventEnd : null,
      lcp: window.__pbUltimoLcp || null,
    };
  });
  await page.close();
  // Uma tag de terceiro pode manter a resposta aberta indefinidamente. Fechar a
  // página primeiro encerra essas requisições e impede uma amostra sem fim.
  await Promise.allSettled(pendentes);
  const origem = new URL(url).origin;
  const somar = filtro => transferencias.filter(filtro).reduce((total, item) => total + item.bytes, 0);
  return {
    url,
    status: resposta?.status() || null,
    load_concluido_ate_limite: loadConcluido,
    duracao_total_ms: Date.now() - inicio,
    metricas: {
      ttfb_ms: navegacao.ttfb_ms,
      fcp_ms: navegacao.fcp_ms,
      lcp_ms: navegacao.lcp?.startTime ?? null,
      load_ms: navegacao.load_ms,
      transferencia_total_bytes: somar(() => true),
      transferencia_primeira_origem_bytes: somar(item => new URL(item.url).origin === origem),
      transferencia_terceiros_bytes: somar(item => new URL(item.url).origin !== origem),
    },
    lcp: navegacao.lcp,
    recursos_maiores: transferencias.sort((a, b) => b.bytes - a.bytes).slice(0, 10),
  };
}

async function medirRota(browser, opcoes, rota) {
  const dispositivo = devices[opcoes.profile];
  const criarContexto = () => browser.newContext({
    ...dispositivo,
    javaScriptEnabled: true,
  });
  const url = new URL(rota, `${opcoes.origin}/`).toString();
  const fria = [];
  for (let i = 0; i < opcoes.runs; i += 1) {
    const contexto = await criarContexto();
    await contexto.addInitScript(() => {
      window.__pbUltimoLcp = null;
      new PerformanceObserver(lista => {
        const entrada = lista.getEntries().at(-1);
        if (entrada) {
          const elemento = entrada.element;
          window.__pbUltimoLcp = {
            startTime: entrada.startTime,
            size: entrada.size,
            url: entrada.url,
            id: entrada.id,
            elemento: elemento ? {
              tag: elemento.tagName,
              id: elemento.id || "",
              className: typeof elemento.className === "string" ? elemento.className : "",
            } : null,
          };
        }
      }).observe({ type: "largest-contentful-paint", buffered: true });
    });
    fria.push(await medirUmaVez(contexto, url, opcoes.throttle));
    await contexto.close();
  }
  const contextoQuente = await criarContexto();
  await contextoQuente.addInitScript(() => {
    window.__pbUltimoLcp = null;
    new PerformanceObserver(lista => {
      const entrada = lista.getEntries().at(-1);
      if (entrada) {
        const elemento = entrada.element;
        window.__pbUltimoLcp = {
          startTime: entrada.startTime,
          size: entrada.size,
          url: entrada.url,
          id: entrada.id,
          elemento: elemento ? {
            tag: elemento.tagName,
            id: elemento.id || "",
            className: typeof elemento.className === "string" ? elemento.className : "",
          } : null,
        };
      }
    }).observe({ type: "largest-contentful-paint", buffered: true });
  });
  await medirUmaVez(contextoQuente, url, opcoes.throttle);
  const quente = [];
  for (let i = 0; i < opcoes.runs; i += 1) {
    quente.push(await medirUmaVez(contextoQuente, url, opcoes.throttle));
  }
  await contextoQuente.close();
  return {
    rota,
    fria: { amostras: fria, resumo: resumo(fria) },
    quente: { amostras: quente, resumo: resumo(quente) },
  };
}

async function main() {
  const opcoes = argumentos(process.argv.slice(2));
  if (opcoes.help) return informar(uso());
  const browser = await chromium.launch();
  try {
    const inicio = new Date().toISOString();
    const rotas = [];
    for (const rota of opcoes.routes) {
      informar(`Medindo ${rota} (${opcoes.runs} fria + ${opcoes.runs} quente)...`);
      rotas.push(await medirRota(browser, opcoes, rota));
    }
    const output = resolve(opcoes.output || `tmp/frontend-baseline-${inicio.replace(/[:.]/g, "-")}.json`);
    const resultado = {
      schema: 1,
      iniciado_em: inicio,
      commit_local: commitLocal(),
      origem: opcoes.origin,
      configuracao: {
        repeticoes_medidas_por_cache: opcoes.runs,
        perfil_playwright: opcoes.profile,
        throttle: opcoes.throttle ? { rede: REDE_4G, cpu_rate: 4 } : null,
        espera_estabilizacao_ms: ESPERA_ESTABILIZACAO_MS,
        limite_load_ms: LIMITE_LOAD_MS,
      },
      rotas,
    };
    await mkdir(dirname(output), { recursive: true });
    await writeFile(output, `${JSON.stringify(resultado, null, 2)}\n`);
    informar(`Baseline salva em ${output}`);
  } finally {
    await browser.close();
  }
}

main().catch(erro => {
  informarErro(`Falha na coleta: ${erro.message}`);
  process.exitCode = 1;
});
