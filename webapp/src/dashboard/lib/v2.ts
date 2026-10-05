// Cliente da /api/v2. Os tipos saem do contrato (api-v2.gen.ts); o fetch é o global,
// que o auth-refresh.js do /painel envolve (renova no 401 e repete).
import { infiniteQueryOptions, useQuery } from "@tanstack/react-query";
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

// Sem envelope (o `{"detail"}` do CSRF, HTML, JSON inválido) o code é `http_<status>`;
// sem resposta, status 0 e code "network". A `message` do envelope não vai para a tela:
// em 402/404 ela é o texto padrão do HTTP em inglês ("Payment Required", "Not Found").
export class ErroApi extends Error {
  status: number;
  code: string;
  details?: ErroV2["error"]["details"];
  constructor(status: number, code: string, details?: ErroV2["error"]["details"]) {
    super(code);
    this.status = status;
    this.code = code;
    this.details = details;
  }
}

async function chamar<T>(path: string, init: RequestInit): Promise<T> {
  let r: Response;
  try {
    r = await fetch("/api/v2" + path, { credentials: "same-origin", ...init });
  } catch (e) {
    if (init.signal?.aborted) throw e;
    throw new ErroApi(0, "network");
  }
  let body: unknown;
  try { body = await r.json(); } catch { body = undefined; }
  if (r.ok && body !== undefined) return body as T;
  const erro = (body as Partial<ErroV2> | undefined)?.error;
  if (!r.ok && erro && typeof erro.code === "string") {
    throw new ErroApi(r.status, erro.code, erro.details);
  }
  throw new ErroApi(r.status, `http_${r.status}`);
}

type Query<P> = P extends keyof QueryGet ? QueryGet[P] : never;
export function apiGet<P extends keyof RotasGet>(path: P, signal?: AbortSignal, query?: Query<P>) {
  const qs = new URLSearchParams(Object.entries(query ?? {}).filter(([, v]) => v != null) as [string, string][]).toString();
  return chamar<RotasGet[P]>(qs ? `${path}?${qs}` : path, { headers: { Accept: "application/json" }, signal });
}

// Escrita: o x-csrf-token sai do cookie `csrf_token` pelo helper do auth-refresh.js.
function escrever<T>(method: "POST" | "PUT", path: string, corpo: unknown) {
  const json = { Accept: "application/json", "Content-Type": "application/json" };
  return chamar<T>(path, { method, body: JSON.stringify(corpo), headers: window.pbCsrfHeaders?.(json) ?? json });
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
export const guiaQuery = {
  queryKey: ["guia"],
  queryFn: ({ signal }: { signal: AbortSignal }) => apiGet("/guia", signal),
  ...vivo,
};
// A conversa com o Piggy ainda responde com dado de exemplo (lib/topics.tsx): decide o selo
// do chat e o `data-dado` da barra de conversa. Vira true quando a conversa usar a API.
export const CHAT_REAL = false;
export const resumoMesQuery = (mes: string) => ({
  queryKey: ["resumo-do-mes", mes],
  queryFn: ({ signal }: { signal: AbortSignal }) => apiGet("/resumo-do-mes", signal, { mes }),
  ...vivo,
});

// Só dentro da árvore que o portão (parts/Entrada.tsx) libera: lá o /me já chegou.
export const usePlan = () => useQuery(meQuery).data!.plan_tier;

export const categoriasQuery = {
  queryKey: ["categorias"],
  queryFn: ({ signal }: { signal: AbortSignal }) => apiGet("/categorias", signal),
  ...vivo,
};
export const lancamentosQuery = (filtros: Omit<QueryGet["/lancamentos"], "cursor">) => infiniteQueryOptions({
  queryKey: ["lancamentos", filtros],
  initialPageParam: null as string | null,
  queryFn: ({ signal, pageParam }) => apiGet("/lancamentos", signal, { ...filtros, cursor: pageParam }),
  getNextPageParam: (pagina) => pagina.proximo ?? undefined,
  ...vivo,
});
