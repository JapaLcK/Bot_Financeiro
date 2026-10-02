/**
 * O dashboard v2 como o /painel o entrega, para os testes `dashboard_v2_*`.
 *
 * Os testes abrem o ARTEFATO commitado (`frontend/dashboard-app.*`), o mesmo que a
 * produção serve, e não buildam: quem prova que ele bate com `webapp/src` é o gate do
 * CI, que rebuilda antes do `npm run test:frontend`. Localmente, a guarda de mtime
 * reprova com a instrução em vez de testar um bundle velho.
 *
 * Não é `*.test.mjs` de propósito: o `node --test tests/frontend/*.test.mjs` não deve
 * rodá-lo como suíte.
 */
import { readFileSync, readdirSync, statSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

export const RAIZ = join(dirname(fileURLToPath(import.meta.url)), "..", "..");
export const FRONTEND = join(RAIZ, "frontend");
// Porta fictícia: toda requisição é atendida do disco pela rota, nada vai para a rede.
export const ORIGIN = "http://127.0.0.1:1";
// `frontend/painel.html` servido da raiz, como o /painel (mesma pasta, mesmos caminhos).
export const PAINEL = `${ORIGIN}/painel.html`;
// O protótipo, servido da raiz do repositório (como o `python3 -m http.server`).
export const PROTOTIPO = `${ORIGIN}/dashboard-v2/index.html`;

export function exigeArtefatoEmDia() {
  const bundle = statSync(join(FRONTEND, "dashboard-app.js")).mtimeMs;
  const src = join(RAIZ, "webapp", "src", "dashboard");
  const velho = readdirSync(src, { recursive: true })
    .map((f) => join(src, f))
    .find((f) => statSync(f).isFile() && statSync(f).mtimeMs > bundle);
  if (velho) {
    throw new Error(`frontend/dashboard-app.js é mais velho que ${velho}: rode \`npm --prefix webapp run build\` e commite o artefato.`);
  }
}

// As respostas da /api/v2 que os testes servem; tests/test_api_v2_contrato.py as valida
// pelos modelos Pydantic.
export const RESPOSTAS = JSON.parse(readFileSync(join(RAIZ, "tests", "frontend", "api_v2_respostas.json"), "utf8"));

/**
 * Atende o contexto do disco: `raiz` = frontend/ (o /painel) ou RAIZ (o protótipo). O
 * `/api/v2/me` responde o `plano` pelas fixtures; o GET `/api/v2/assinaturas`, a lista
 * `cheia` no Plus e no Pro e o 403 `pro_required` nos outros; o `/api/v2/eventos` (SSE) fica
 * pendente para sempre: stream aberto e mudo. Registrar de novo vale para as
 * próximas requisições: no Playwright a rota registrada por último vence.
 */
export async function servir(ctx, raiz = FRONTEND, { plano = "pro" } = {}) {
  const me = RESPOSTAS.me[plano];
  if (!me) throw new Error(`plano sem fixture: ${plano}`);
  await ctx.route("**/*", (r) => {
    const url = new URL(r.request().url());
    if (url.origin !== ORIGIN) return r.abort();
    if (url.pathname === "/api/v2/me") return r.fulfill({ json: me });
    if (url.pathname === "/api/v2/eventos") return;
    if (url.pathname === "/api/v2/assinaturas" && r.request().method() === "GET") {
      const pago = plano === "plus" || plano === "pro";
      const e = RESPOSTAS.erros["403_pro_required"];
      return pago ? r.fulfill({ json: RESPOSTAS.assinaturas.cheia }) : r.fulfill({ status: e.status, json: e.body });
    }
    const path = decodeURIComponent(url.pathname).replace(/\/$/, "/index.html");
    return r.fulfill({ path: join(raiz, path) }).catch(() => r.fulfill({ status: 404, body: "" }));
  });
}
