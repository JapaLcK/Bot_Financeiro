import { guardarCredenciais, iniciarTentativaBancaria, type Credenciais } from "@/storage/secure";
/** Apoio dos dois arquivos da lógica de `features/openFinance/volta.ts`. */
import { conferirVolta, INTERVALO_MS, JANELA_MS, type Dependencias, type EstadoVolta } from "@/features/openFinance/volta";

import { chamadas, cofre, resposta, rotear, type Rota } from "./auth_apoio";

/** Fixtures de retorno oficial carregam a origem que veio na URI do token. */
export function origemDaTentativa(): string | undefined {
  try { return JSON.parse(cofre.get("pb.of.tentativa") ?? "null")?.tentativa_id; } catch { return undefined; }
}
export const conferirRetornoOficial = (link: unknown, d: Dependencias) => conferirVolta(link, d, origemDaTentativa());

export const ITEM = "c13cb883-item_1";
const conexao = (state: string, label: string) => ({ id: 1, status: "ACTIVE", status_reason: null, last_sync_at: "2026-10-05T12:00:00Z", reconnected_at: null, provider_item_id: ITEM, institution_name: "Nubank", ui: { state, label, detail: null } });
/** O item virou conexão e a coleta terminou. */
export const VIVO = conexao("updated", "Atualizado");
/** O item virou conexão e a coleta ainda roda (medido no iPhone: ~42 s depois do `onSuccess`). */
export const ATUALIZANDO = conexao("updating", "Atualizando…");
export const comEstado = (state: string) => conexao(state, state);
export const lista = (...connections: unknown[]) => resposta(200, { ok: true, connections });

/**
 * Uma janela tem `JANELA_MS / INTERVALO_MS` esperas; o triplo só passa se o
 * teto da janela sumiu. Sem esta guarda o laço gira só microtarefas: não
 * termina, e o timeout do Jest não dispara (estoura o heap).
 */
export const MAX_ESPERAS = 3 * (JANELA_MS / INTERVALO_MS);

/**
 * Sem fake timers: o relógio e a espera são injetados — `esperar` avança o
 * relógio e devolve na hora. Um `esperar` vindo de `extra` também passa pela
 * guarda de `MAX_ESPERAS`: ela conta e só então delega a ele.
 */
export function dependencias({ esperar: esperarDoCaso, ...extra }: Partial<Dependencias> = {}) {
  const relogio = { t: 0 };
  const estados: EstadoVolta[] = [];
  const expirou = jest.fn();
  let esperas = 0;
  const d: Dependencias = {
    agora: () => relogio.t,
    cancelado: () => false,
    aoMudar: (e) => void estados.push(e),
    expirou,
    ...extra,
    esperar: async (ms) => {
      if (++esperas > MAX_ESPERAS) throw new Error(`laço sem teto: mais de ${MAX_ESPERAS} esperas (o prazo da janela parou de valer?)`);
      if (esperarDoCaso) return esperarDoCaso(ms);
      relogio.t += ms;
    },
  };
  return { d, relogio, estados, expirou, ultimo: () => estados[estados.length - 1] };
}

export function servidor(rotas: { get?: Rota; post?: Rota }) {
  rotear({
    "/open-finance/1": rotas.get ?? (() => lista()),
    "/open-finance/1/pluggy-item": rotas.post ?? (() => lista(VIVO)),
  });
}

/** Cada chamada devolve o próximo da fila; o último se repete. */
export function emSequencia(...rotas: Rota[]): Rota {
  return (o) => {
    const rota = rotas.length > 1 ? rotas.shift() : rotas[0];
    if (!rota) throw new Error("emSequencia sem rotas");
    return rota(o);
  };
}

export const caminhos = () => chamadas().map((c) => c.caminho);
export const gets = () => caminhos().filter((c) => c === "/open-finance/1");
export const posts = () => caminhos().filter((c) => c.endsWith("/pluggy-item"));

/** Falhas transitórias: não dizem nada sobre o item. */
export const falhas: [string, Rota][] = [
  ["rede (TypeError)", () => Promise.reject(new TypeError("Network request failed"))],
  ["tempo limite (abort)", () => Promise.reject(Object.assign(new Error("aborted"), { name: "AbortError" }))],
  ["500", () => resposta(500, {})],
  ["503", () => resposta(503, {})],
  ["429", () => resposta(429, { detail: "Muitas tentativas." })],
];

export const SESSAO_OF = "sessao-of";
export const JWT_OF = "eyJhbGciOiJIUzI1NiJ9.eyJqdGkiOiJzZXNzYW8tb2YiLCJuYW1lIjoicyJ9.assinatura";
export async function guardarSessaoOf(c: Credenciais) {
  await guardarCredenciais(c.access === "access-s" ? { ...c, access: JWT_OF } : c);
  if (c.access === "access-s") await iniciarTentativaBancaria(1, SESSAO_OF, []);
}
