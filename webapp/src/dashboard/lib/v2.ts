// Cliente da /api/v2. Os tipos saem do contrato (api-v2.gen.ts); o fetch é o global,
// que o auth-refresh.js do /painel envolve (renova no 401 e repete).
import { infiniteQueryOptions, queryOptions, useQuery, type QueryClient } from "@tanstack/react-query";
import type { ErroV2, QueryGet, RotasGet, RotasPost, RotasPut } from "./api-v2.gen";
import { MONTHS } from "./api";

// O helper de CSRF do auth-refresh.js (carregado antes do bundle no /painel).
declare global { interface Window { pbCsrfHeaders?: (extra?: Record<string, string>) => Record<string, string> } }

// O protótipo (dashboard-v2/index.html) não tem backend: main.tsx semeia o cache e as
// consultas de dado não saem.
export const DEMO = typeof window.PIGBANK_DEMO_PLAN === "string";

// "AAAA-MM" de agora no fuso do app, o mesmo do backend (que recusa mês futuro): o mês do
// aparelho ou do UTC vira antes, das 21h às 24h do último dia.
export const FUSO = "America/Sao_Paulo";
export const mesAtual = (agora = new Date()) =>
  new Intl.DateTimeFormat("en-CA", { timeZone: FUSO, year: "numeric", month: "2-digit" }).format(agora);

// Os meses do seletor, do mais antigo ao atual: no protótipo os sintéticos; com backend, os
// 6 últimos reais. ponytail: calculados ao abrir a página; aberta na virada, só recarregando.
export const MESES: string[] = DEMO ? MONTHS : (() => {
  const [y, m] = mesAtual().split("-").map(Number);
  return Array.from({ length: 6 }, (_, i) => mesAtual(new Date(Date.UTC(y, m - 6 + i, 15))));
})();

// O nome do plano na tela, por tier: a fonte única do painel (o servidor não manda nome de exibição).
export const NOME_DO_PLANO: Record<string, string> = { essencial: "Essencial", plus: "Plus", pro: "Pro" };

// Sem envelope (o `{"detail"}` do CSRF, HTML, JSON inválido) o code é `http_<status>`;
// sem resposta, status 0 e code "network". A `message` do envelope não vai para a tela:
// em 402/404 ela é o texto padrão do HTTP em inglês ("Payment Required", "Not Found").
export class ErroApi extends Error {
  status: number;
  code: string;
  details?: ErroV2["error"]["details"];
  // O `{"detail": "<texto PT>"}` das rotas antigas (/settings, /auth), já escrito para a tela. Fica de fora
  // no 401 que carrega WWW-Authenticate: esse é o 401 de SESSÃO, e quem o recebe não pode confundi-lo com
  // "Senha incorreta." (que não carrega).
  detalhe?: string;
  // O corpo lido, inteiro: as rotas antigas mandam `detail` OBJETO (`{error, message, retryable}` do /agents), que `detalhe` (só texto) não guarda.
  corpo?: unknown;
  constructor(status: number, code: string, details?: ErroV2["error"]["details"], detalhe?: string, corpo?: unknown) {
    super(code);
    this.status = status;
    this.code = code;
    this.details = details;
    this.detalhe = detalhe;
    this.corpo = corpo;
  }
}

export async function chamar<T>(path: string, init: RequestInit): Promise<T> {
  let r: Response;
  try {
    r = await fetch((/^\/(auth|settings|open-finance|billing|agents)\//.test(path) ? "" : "/api/v2") + path, { credentials: "same-origin", ...init });
  } catch (e) {
    if (init.signal?.aborted) throw e;
    throw new ErroApi(0, "network");
  }
  let body: unknown;
  // Corpo cortado pelo sinal numa resposta 2xx é prazo, não resposta vazia (par da linha do `fetch`); o 4xx/5xx cortado mantém o status.
  try { body = await r.json(); } catch (e) { if (r.ok && init.signal?.aborted) throw e; body = undefined; }
  if (r.ok && body !== undefined) return body as T;
  const erro = (body as Partial<ErroV2> | undefined)?.error;
  if (!r.ok && erro && typeof erro.code === "string") {
    throw new ErroApi(r.status, erro.code, erro.details);
  }
  const d = (body as { detail?: unknown } | undefined)?.detail;
  const sessao = r.status === 401 && !!r.headers.get("WWW-Authenticate");
  throw new ErroApi(r.status, `http_${r.status}`, undefined, !r.ok && typeof d === "string" && !sessao ? d : undefined, body);
}

type Query<P> = P extends keyof QueryGet ? QueryGet[P] : never;
export function apiGet<P extends keyof RotasGet>(path: P, signal?: AbortSignal, query?: Query<P>) {
  const qs = new URLSearchParams(Object.entries(query ?? {}).filter(([, v]) => v != null) as [string, string][]).toString();
  return chamar<RotasGet[P]>(qs ? `${path}?${qs}` : path, { headers: { Accept: "application/json" }, signal });
}

// Escrita: o x-csrf-token sai do cookie `csrf_token` pelo helper do auth-refresh.js.
// `corpo` undefined (o DELETE): sem corpo e sem Content-Type.
export function escrever<T>(method: "POST" | "PUT" | "PATCH" | "DELETE", path: string, corpo: unknown, signal?: AbortSignal) {
  const json = { Accept: "application/json", ...(corpo === undefined ? {} : { "Content-Type": "application/json" }) };
  return chamar<T>(path, { method, body: corpo === undefined ? undefined : JSON.stringify(corpo), headers: window.pbCsrfHeaders?.(json) ?? json, signal });
}
export const apiPost = <P extends keyof RotasPost>(path: P, corpo: RotasPost[P]["corpo"]) =>
  escrever<RotasPost[P]["resposta"]>("POST", path, corpo);
export const apiPut = <P extends keyof RotasPut>(path: P, corpo: RotasPut[P]["corpo"]) =>
  escrever<RotasPut[P]["resposta"]>("PUT", path, corpo);

export const meQuery = {
  queryKey: ["me"],
  queryFn: ({ signal }: { signal: AbortSignal }) => apiGet("/me", signal),
  staleTime: Infinity,
  // Após o portão mostrar erro, foco de aba não deve iniciar outra consulta; a recuperação é manual.
  refetchOnWindowFocus: false,
  // "always": no padrão ("online") o evento `offline` pausa a query e o retry, e o portão
  // fica em "Carregando…" para sempre, sem erro nem Recarregar.
  networkMode: "always" as const,
  // 4xx nunca repete (nem o 429): só rede e 5xx, até 3 tentativas no total.
  retry: (n: number, e: Error) => n < 2 && e instanceof ErroApi && (e.status === 0 || e.status >= 500),
};

export const assinaturasQuery = {
  queryKey: ["assinaturas"],
  queryFn: ({ signal }: { signal: AbortSignal }) => apiGet("/assinaturas", signal),
  staleTime: Infinity,
  refetchOnWindowFocus: false,
  networkMode: "always" as const,
  retry: meQuery.retry,
};

// Nome e e-mail da conta (`GET /auth/dashboard-profile`, fora da /api/v2): só em memória, nunca em localStorage.
export const contaQuery = {
  queryKey: ["conta"],
  queryFn: ({ signal }: { signal: AbortSignal }) => chamar<{ display_name: string | null; email: string | null; user_id: number; whatsapp_linked: boolean }>("/auth/dashboard-profile", { headers: { Accept: "application/json" }, signal }),
  staleTime: 0,
  refetchOnWindowFocus: false,
  networkMode: "always" as const,
  retry: meQuery.retry,
  enabled: !DEMO,
};
// A conta numa leitura que SAIU agora ou depois. Uma leitura já em voo pode ter saído com a sessão anterior (a conta mudou noutra
// aba): espera por ela e lê de novo. Cancelar não serve, quem a esperava receberia o cancelamento. Toda conferência de conta do
// chat passa por aqui: montagem, foco, pergunta ao Piggy e menção (Codex no #1157).
export async function lerContaFresca(qc: QueryClient) {
  if (qc.isFetching({ queryKey: contaQuery.queryKey })) await qc.fetchQuery(contaQuery).catch(() => {});
  return qc.fetchQuery(contaQuery);
}

// Dado que muda: relido a cada montagem, foco de aba e aviso do SSE.
const vivo = { staleTime: 0, refetchOnWindowFocus: true, networkMode: "always" as const, retry: meQuery.retry, enabled: !DEMO };
export const perfilQuery = {
  queryKey: ["perfil"],
  queryFn: ({ signal }: { signal: AbortSignal }) => apiGet("/perfil", signal),
  ...vivo,
};
export const contasQuery = {
  queryKey: ["contas"],
  queryFn: ({ signal }: { signal: AbortSignal }) => apiGet("/contas", signal),
  ...vivo,
};
// Os bancos conectados (`GET /api/v2/bancos`): só o `motivos` de cima alimenta o aviso do topo (parts/AvisosDoTopo.tsx).
// Não é o `bancosQuery(uid)` da Conta (conta/api.ts): aquele mostra valores em R$ e por isso confere a conta depois da resposta (um GET
// a mais de /auth/dashboard-profile e o `uid` do contaQuery, que fora da Conta ninguém lê). O aviso só lê `motivos`, como `contasQuery`
// e `perfilQuery`, e não monta em /conta/* (SEM_PILHA): os dois nunca pedem /bancos ao mesmo tempo (dashboard_v2_pilha_avisos, "sem GET duplicado").
export const avisoDeBancoQuery = {
  queryKey: ["bancos"],
  queryFn: ({ signal }: { signal: AbortSignal }) => apiGet("/bancos", signal),
  ...vivo,
};
// As metas e o custo mensal da reserva (`GET /api/v2/metas`); toda escrita invalida esta chave.
// Lida pela página Metas e pelo assunto `metas` do chat (parts/MetasResposta.tsx).
export const metasQuery = {
  queryKey: ["metas"],
  queryFn: ({ signal }: { signal: AbortSignal }) => apiGet("/metas", signal),
  ...vivo,
};
// O orçamento doméstico do mês (`GET /api/v2/orcamento-domestico`); a escrita devolve a resposta nova e ela entra nesta chave.
export const orcamentoQuery = (mes: string) => queryOptions({
  queryKey: ["orcamento-domestico", mes],
  queryFn: ({ signal }) => apiGet("/orcamento-domestico", signal, { mes }),
  ...vivo,
});
// O patrimônio de hoje para o Progresso ao milhão (`GET /api/v2/progresso-milhao`); a projeção é do cliente (lib/milhao.ts).
export const milhaoQuery = {
  queryKey: ["progresso-milhao"],
  queryFn: ({ signal }: { signal: AbortSignal }) => apiGet("/progresso-milhao", signal),
  ...vivo,
};
// Os orçamentos por categoria do mês corrente (`GET /api/v2/orcamentos-categoria`, sem `mes`); a escrita devolve a resposta nova e ela entra nesta chave.
export const orcamentosCategoriaQuery = {
  queryKey: ["orcamentos-categoria"],
  queryFn: ({ signal }: { signal: AbortSignal }) => apiGet("/orcamentos-categoria", signal),
  ...vivo,
};
// As contas a pagar e as caixinhas lidas pelos cards do Carteiro e do Banqueiro (sem sync nem juros: parts/CardAgente.tsx).
export const contasAPagarQuery = {
  queryKey: ["contas-a-pagar"],
  queryFn: ({ signal }: { signal: AbortSignal }) => apiGet("/contas-a-pagar", signal),
  ...vivo,
};
export const caixinhasQuery = {
  queryKey: ["caixinhas"],
  queryFn: ({ signal }: { signal: AbortSignal }) => apiGet("/caixinhas", signal),
  ...vivo,
};
export const guiaQuery = {
  queryKey: ["guia"],
  queryFn: ({ signal }: { signal: AbortSignal }) => apiGet("/guia", signal),
  ...vivo,
};
// A conversa com o Piggy ainda responde com dado de exemplo (lib/topics.tsx), menos o assunto
// `investido` e `metas` (resposta pronta do servidor, selo por mensagem em parts/PiggyChat.tsx): decide
// o `data-dado` da barra de conversa. Vira true quando a conversa usar a API.
export const CHAT_REAL = false;
// O total investido nos bancos conectados (`GET /api/v2/investido`), o assunto real do chat.
// Só a bolha consulta (a cópia do role=status não: parts/InvestidoResposta.tsx). Montar de
// novo só relê depois de erro (a pergunta repetida tenta outra vez) ou de aviso do SSE com a
// conversa fechada (inativa, a consulta só fica invalidada); o resto do dado novo vem do SSE e do foco.
export const investidoQuery = {
  queryKey: ["investido"],
  queryFn: ({ signal }: { signal: AbortSignal }) => apiGet("/investido", signal),
  ...vivo,
  refetchOnMount: (q: { state: { status: string; isInvalidated: boolean } }) => q.state.status === "error" || q.state.isInvalidated,
};
export const resumoMesQuery = (mes: string) => ({
  queryKey: ["resumo-do-mes", mes],
  queryFn: ({ signal }: { signal: AbortSignal }) => apiGet("/resumo-do-mes", signal, { mes }),
  ...vivo,
});
// "Para onde vai" (`GET /api/v2/gastos`): o Resumo e a /gastos leem a mesma chave, uma requisição só.
export const gastosQuery = (mes: string) => queryOptions({
  queryKey: ["gastos", mes],
  queryFn: ({ signal }) => apiGet("/gastos", signal, { mes }),
  ...vivo,
});
// O detalhe de uma categoria do mês (`/gastos/categoria`), também o calendário dela.
export const gastosCategoriaQuery = (mes: string, categoria: string) => queryOptions({
  queryKey: ["gastos", mes, categoria],
  queryFn: ({ signal }) => apiGet("/gastos/categoria", signal, { mes, categoria }),
  ...vivo,
});

// O mês contra o anterior equivalente (`GET /api/v2/comparacao`, Plus+). `gcTime: 0`, como a
// previsão: dado Plus sem tela olhando sai do cache, e o downgrade não acha número velho. No
// protótipo o cache semeado (main.tsx) não pode sumir ao desmontar.
export const comparacaoQuery = (mes: string) => queryOptions({
  queryKey: ["comparacao", mes],
  queryFn: ({ signal }) => apiGet("/comparacao", signal, { mes }),
  ...vivo,
  gcTime: DEMO ? Infinity : 0,
});

// Só dentro da árvore que o portão (parts/Entrada.tsx) libera: lá o /me já chegou.
export const usePlan = () => useQuery(meQuery).data!.plan_tier;
/** Freio de emergência dos agentes (`AGENTS_UI_ENABLED=0`): ausente (protótipo, resposta antiga) = ligado, como no /auth/me do dashboard antigo. */
export const useAgentesLigados = () => useQuery(meQuery).data?.agents_ui_enabled !== false;

export const categoriasQuery = {
  queryKey: ["categorias"],
  queryFn: ({ signal }: { signal: AbortSignal }) => apiGet("/categorias", signal),
  ...vivo,
};
// A previsão por horizonte (null = o padrão do plano). `gcTime: 0`: dado privado sem tela
// olhando sai do cache na hora (troca de horizonte, downgrade, portão caído), e a chave
// abandonada tem o fetch abortado.
// Reconsulta quando a resposta deixa de valer (`valido_ate`), medido no relógio do servidor.
// ponytail: piso de 60 s contra laço; o teto é o do setTimeout (2³¹−1 ms).
export const instante = (s: unknown) => typeof s === "string" ? Date.parse(s.replace(/(\.\d{3})\d+/, "$1")) : NaN; // 6 casas: NaN no Safari 14
export const previsaoQuery = (dias: 30 | 60 | 90 | null) => queryOptions({
  queryKey: ["previsao", dias],
  queryFn: ({ signal }) => apiGet("/previsao", signal, dias ? { dias: String(dias) as "30" | "60" | "90" } : undefined),
  ...vivo,
  gcTime: 0,
  refetchInterval: (q) => {
    const d = q.state.data;
    if (!d?.valido_ate) return false;
    const ms = instante(d.valido_ate) - instante(d.calculado_em) - (Date.now() - q.state.dataUpdatedAt);
    return Number.isFinite(ms) ? Math.min(Math.max(ms, 60_000), 2 ** 31 - 1) : false;
  },
});

// `guardado: true`: a tela de Lançamentos mostra o guardado (P8); no backend ele é opt-in porque o
// app nativo monta a mesma rota e não conhece o campo.
export const lancamentosQuery = (filtros: Omit<QueryGet["/lancamentos"], "cursor">) => infiniteQueryOptions({
  queryKey: ["lancamentos", filtros],
  initialPageParam: null as string | null,
  queryFn: ({ signal, pageParam }) => apiGet("/lancamentos", signal, { ...filtros, guardado: true, cursor: pageParam }),
  getNextPageParam: (pagina) => pagina.proximo ?? undefined,
  ...vivo,
});

// Sair: o mesmo roteiro do `logoutSettings` (settings.html). O fetch é o GLOBAL, não o `apiPost`: o
// auth-refresh.js reconhece o POST em /auth/logout e limpa o estado do aparelho, mesmo se ele REJEITAR.
// A chave avisa as outras abas (`escutarLogout`); o redirecionamento sai do fetch, resolvido ou não.
const LOGOUT_AT = "finbot_logout_at";
const DEPOIS_DO_LOGOUT = "/?logout=1";
export function sair(e: { currentTarget: HTMLButtonElement }) {
  e.currentTarget.disabled = true; // o 2º clique não manda outro POST; a página sai de qualquer jeito (fim)
  try { localStorage.setItem(LOGOUT_AT, String(Date.now())); } catch { /* storage bloqueado: o logout segue */ }
  const fim = () => location.replace(DEPOIS_DO_LOGOUT);
  fetch("/auth/logout", { method: "POST", credentials: "same-origin", headers: window.pbCsrfHeaders?.() ?? {} }).then(fim, fim);
}
export const escutarLogout = () => window.addEventListener("storage", (e) => { if (e.key === LOGOUT_AT) location.replace(DEPOIS_DO_LOGOUT); });
