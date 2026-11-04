// Plano Família, dados (api/v2/familia_dados.py): permissões do membro, histórico "Quem viu" e as leituras do titular sobre um membro.
// Mesmo estilo de leitor de borda do api.ts: formato errado vira ErroApi(200, "contrato"), campo extra é ignorado, dinheiro segue TEXTO (nunca Number()).
// Toda leitura do titular grava uma linha no histórico do membro: sem releitura no foco, sem intervalo, `gcTime: 0` (sair do painel apaga o dado da memória)
// e `meta.foto` (o aviso do SSE, que relê tudo, não alcança estas chaves: ver `leitura`).
// O `ref` e o cursor só vão ao servidor no path/pageParam; o `ref` também está na chave de cache, em memória: nunca em URL do navegador nem storage.
// Nada relê sozinho: nem foco, nem intervalo, nem o aviso do SSE, nem NENHUMA mudança de permissão/status com o painel aberto (revogar, liberar pela 1ª vez, religar, suspender, reativar, sair): menos acesso vira texto na hora, mais acesso só depois de Voltar + Ver (VerMembro.tsx, tabela estados x eventos).
// Só o retry de rede/5xx (até 3 GETs por leitura): uma resposta perdida DEPOIS do commit do servidor pode duplicar uma linha do log. Limite: um GET já em voo quando a permissão cai ainda é gravado no servidor.
import { infiniteQueryOptions, queryOptions } from "@tanstack/react-query";
import type { Orcamento } from "../lib/api-v2.gen";
import { moneyText } from "../lib/format.js";
import { DEMO, ErroApi, chamar, escrever, meQuery } from "../lib/v2";
import { ESCOPOS, contrato, inteiro, objeto, permissoesDe, texto, type Escopo } from "./api";

export type Leitura = { quando: string; quem: string; escopo: Escopo; mes: string };
export type Historico = { itens: Leitura[]; proximo: string | null };
export type Categoria = { chave: string; nome: string; valor: string; quantidade: number };
export type Agregados = { mes: string; entrou: string; saiu: string; categorias: Categoria[]; orcamentos: Orcamento[] | null; historico_desde: string | null; inicio_do_historico: boolean };
export type LancamentoMembro = { data: string; tipo: "entrada" | "saida"; valor: string; moeda: string; descricao: string | null; categoria: string | null };
export type Pagina = { itens: LancamentoMembro[]; proximo: string | null };

const dinheiro = (v: unknown): v is string => typeof v === "string" && moneyText(v) !== null;
const opcional = (v: unknown): v is string | null => v === null || typeof v === "string";
const lista = (v: unknown): unknown[] => { if (!Array.isArray(v)) throw contrato(); return v; };

function permissoesEscritas(v: unknown): Escopo[] {
  if (!objeto(v) || !Array.isArray(v.permissoes)) throw contrato();
  return permissoesDe(v.permissoes);
}
export const liberar = async (escopo: Escopo) => permissoesEscritas(await escrever<unknown>("POST", "/familia/permissoes", { escopo }));
export const revogar = async (escopo: Escopo) => permissoesEscritas(await escrever<unknown>("DELETE", `/familia/permissoes/${escopo}`, {}));

function historicoDe(v: unknown): Historico {
  if (!objeto(v) || !opcional(v.proximo)) throw contrato();
  const itens = lista(v.itens).map((i): Leitura => {
    if (!objeto(i) || !texto(i.quando) || typeof i.quem !== "string" || !ESCOPOS.includes(i.escopo as Escopo) || !/^\d{4}-\d{2}$/.test(String(i.mes))) throw contrato();
    return { quando: i.quando, quem: i.quem, escopo: i.escopo as Escopo, mes: i.mes as string };
  });
  return { itens, proximo: v.proximo as string | null };
}

function orcamentoDe(o: unknown): Orcamento {
  if (!objeto(o) || typeof o.categoria !== "string" || typeof o.icone !== "string" || typeof o.cor !== "string" || !["verde", "amarelo", "vermelho"].includes(o.status as string)
    || !dinheiro(o.teto) || !dinheiro(o.gasto) || !dinheiro(o.restante) || !dinheiro(o.usado_pct)) throw contrato();
  return o as unknown as Orcamento;
}

function agregadosDe(v: unknown): Agregados {
  if (!objeto(v) || !texto(v.mes) || !dinheiro(v.entrou) || !dinheiro(v.saiu) || !opcional(v.historico_desde) || !Array.isArray(v.motivos)) throw contrato();
  const categorias = lista(v.categorias).map((c): Categoria => {
    if (!objeto(c) || !texto(c.chave) || typeof c.nome !== "string" || !dinheiro(c.valor) || !inteiro(c.quantidade)) throw contrato();
    return { chave: c.chave, nome: c.nome, valor: c.valor, quantidade: c.quantidade };
  });
  let orcamentos: Orcamento[] | null = null; // null = mês passado: não há orçamento
  if (v.orcamentos !== null) { if (!objeto(v.orcamentos)) throw contrato(); orcamentos = lista(v.orcamentos.itens).map(orcamentoDe); }
  return { mes: v.mes, entrou: v.entrou, saiu: v.saiu, categorias, orcamentos, historico_desde: v.historico_desde as string | null, inicio_do_historico: v.motivos.includes("inicio_do_historico") };
}

function paginaDe(v: unknown): Pagina {
  if (!objeto(v) || !opcional(v.proximo)) throw contrato();
  const itens = lista(v.itens).map((i): LancamentoMembro => {
    if (!objeto(i) || !/^\d{4}-\d{2}-\d{2}/.test(String(i.data)) || (i.tipo !== "entrada" && i.tipo !== "saida") || !dinheiro(i.valor) || !texto(i.moeda) || !opcional(i.descricao) || !opcional(i.categoria)) throw contrato();
    return { data: i.data as string, tipo: i.tipo, valor: i.valor, moeda: i.moeda, descricao: i.descricao as string | null, categoria: i.categoria as string | null };
  });
  return { itens, proximo: v.proximo as string | null };
}

const GET = (signal?: AbortSignal) => ({ headers: { Accept: "application/json" }, signal });
const qs = (p: Record<string, string | null | undefined>) => {
  const q = new URLSearchParams(Object.entries(p).filter(([, v]) => v != null) as [string, string][]).toString();
  return q ? `?${q}` : "";
};

// Chave ["conta", <assunto>, uid]: a forma que o main.tsx já remove/ignora quando a conta troca.
export const historicoQuery = (uid: number | undefined) => infiniteQueryOptions({
  queryKey: ["conta", "familia-historico", uid],
  initialPageParam: null as string | null,
  queryFn: async ({ signal, pageParam }) => historicoDe(await chamar<unknown>(`/familia/historico${qs({ antes: pageParam })}`, GET(signal))),
  getNextPageParam: (p) => p.proximo ?? undefined,
  networkMode: "always", retry: meQuery.retry, refetchOnWindowFocus: false,
  enabled: !DEMO && uid != null,
});

// Leituras do titular: cada GET vira uma linha no histórico do membro. `meta.foto`: o aviso/reconexão do SSE (lib/eventos.ts) não as relê.
// O `historicoQuery` fica de fora de propósito: ler o próprio histórico não grava log, e o aviso deve trazer a leitura nova ao vivo.
const leitura = { meta: { foto: true }, staleTime: 0, gcTime: 0, refetchOnMount: "always", refetchOnWindowFocus: false, retry: meQuery.retry, networkMode: "always" } as const;
export const chaveDe = (assunto: "agregados" | "lancamentos", ref: string, mes: string, uid: number | undefined) => ["conta", `familia-membro:${assunto}:${ref}:${mes}`, uid] as const;
/** Todas as consultas de dados deste membro, de qualquer mês (403: o que sobrou na memória sai). */
export const doMembro = (ref: string) => ({ predicate: (q: { queryKey: readonly unknown[] }) => ESCOPOS.some((a) => q.queryKey[1]?.toString().startsWith(`familia-membro:${a}:${ref}:`)) });

export const agregadosQuery = (uid: number | undefined, ref: string, mes: string, habilitada = true) => queryOptions({
  queryKey: chaveDe("agregados", ref, mes, uid),
  queryFn: async ({ signal }) => agregadosDe(await chamar<unknown>(`/familia/membros/${encodeURIComponent(ref)}/agregados${qs({ mes })}`, GET(signal))),
  ...leitura, enabled: !DEMO && uid != null && habilitada,
});
export const lancamentosQuery = (uid: number | undefined, ref: string, mes: string, habilitada = true) => infiniteQueryOptions({
  queryKey: chaveDe("lancamentos", ref, mes, uid),
  initialPageParam: null as string | null,
  queryFn: async ({ signal, pageParam }) => paginaDe(await chamar<unknown>(`/familia/membros/${encodeURIComponent(ref)}/lancamentos${qs({ mes, cursor: pageParam })}`, GET(signal))),
  getNextPageParam: (p) => p.proximo ?? undefined,
  ...leitura, enabled: !DEMO && uid != null && habilitada,
});

export type Motivo = "consentimento" | "saiu" | "plano" | "mes" | "cursor" | "sessao" | "erro";
/** O erro de uma leitura do titular, em um dos casos da tela (o texto do servidor nunca vai para a tela). */
export function motivoDe(e: unknown): Motivo {
  if (!(e instanceof ErroApi)) return "erro";
  if (e.status === 403 && e.code === "consent_required") return "consentimento";
  if (e.status === 404) return "saiu";
  if (e.status === 402) return "plano";
  if (e.status === 401) return "sessao";
  const campos = (e.details ?? []).flatMap((d) => d.loc);
  if (e.status === 422 && campos.includes("mes")) return "mes";
  if (e.status === 422 && campos.includes("cursor")) return "cursor";
  return "erro";
}
