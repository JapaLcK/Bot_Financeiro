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
import { readdirSync, statSync } from "node:fs";
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

/** Atende o contexto do disco: `raiz` = frontend/ (o /painel) ou RAIZ (o protótipo). */
export async function servir(ctx, raiz = FRONTEND) {
  await ctx.route("**/*", (r) => {
    const url = new URL(r.request().url());
    if (url.origin !== ORIGIN) return r.abort();
    const path = decodeURIComponent(url.pathname).replace(/\/$/, "/index.html");
    return r.fulfill({ path: join(raiz, path) }).catch(() => r.fulfill({ status: 404, body: "" }));
  });
}
