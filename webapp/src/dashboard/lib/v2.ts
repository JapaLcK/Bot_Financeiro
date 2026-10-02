// Cliente da /api/v2. Os tipos saem do contrato (api-v2.gen.ts); o fetch é o global,
// que o auth-refresh.js do /painel envolve (renova no 401 e repete).
import { useQuery } from "@tanstack/react-query";
import type { ErroV2, RotasGet, RotasPost } from "./api-v2.gen";

// O helper de CSRF do auth-refresh.js (carregado antes do bundle no /painel).
declare global { interface Window { pbCsrfHeaders?: (extra?: Record<string, string>) => Record<string, string> } }

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

export const apiGet = <P extends keyof RotasGet>(path: P, signal?: AbortSignal) =>
  chamar<RotasGet[P]>(path, { headers: { Accept: "application/json" }, signal });

export function apiPost<P extends keyof RotasPost>(path: P, corpo: RotasPost[P]["corpo"]) {
  const json = { Accept: "application/json", "Content-Type": "application/json" };
  return chamar<RotasPost[P]["resposta"]>(path, {
    method: "POST", body: JSON.stringify(corpo), headers: window.pbCsrfHeaders?.(json) ?? json,
  });
}

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

// Só dentro da árvore que o portão (parts/Entrada.tsx) libera: lá o /me já chegou.
export const usePlan = () => useQuery(meQuery).data!.plan_tier;
