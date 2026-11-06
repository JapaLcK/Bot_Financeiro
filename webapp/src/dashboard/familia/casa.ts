// A casa do grupo (api/v2/familia_casa.py): metas e orçamentos que a família divide. Mesmo estilo de leitor de borda do api.ts: formato errado vira ErroApi(200, "contrato"), campo extra é ignorado,
// dinheiro segue TEXTO (nunca Number()). Chave ["conta","familia-casa",uid] (a forma que o main.tsx já limpa na troca de conta), em memória, nada em storage.
// Ler a casa NÃO grava log (ao contrário das leituras do titular em dados.ts): relê no foco da aba e no aviso do SSE (lib/eventos.ts), o que traz a edição de outro membro e reduz conflito (`versao`).
// Escrita devolve a Casa inteira (vai para o cache); DELETE responde 204 e relê.
import { queryOptions } from "@tanstack/react-query";
import { ERRO_VALOR } from "../lib/format.js";
import { DEMO, ErroApi, chamar, escrever, meQuery } from "../lib/v2";
import { contrato, falhaFamilia, inteiro, objeto, semCorpo, texto, type Falha } from "./api";
import { dinheiro, lista, opcional } from "./dados";

export type MetaCasa = { ref: string; nome: string; alvo: string; guardado: string; falta: string; percentual: number; prazo: string | null; autor: string | null; versao: number };
export type OrcamentoCasa = { ref: string; categoria: string; limite: string; gasto: string; restante: string; autor: string | null; versao: number };
export type Casa = { mes: string; metas: MetaCasa[]; orcamentos: OrcamentoCasa[] };
export type Tipo = "metas" | "orcamentos";

const autor = (v: unknown) => { if (!opcional(v)) throw contrato(); return v; }; // null = ex-membro, "" = membro sem nome

function casaDe(v: unknown): Casa {
  if (!objeto(v) || !/^\d{4}-\d{2}$/.test(String(v.mes))) throw contrato();
  const metas = lista(v.metas).map((m): MetaCasa => {
    if (!objeto(m) || !texto(m.ref) || typeof m.nome !== "string" || !dinheiro(m.alvo) || !dinheiro(m.guardado) || !dinheiro(m.falta) || !inteiro(m.percentual)
      || (m.prazo !== null && !/^\d{4}-\d{2}-\d{2}$/.test(String(m.prazo))) || !inteiro(m.versao)) throw contrato();
    return { ref: m.ref, nome: m.nome, alvo: m.alvo, guardado: m.guardado, falta: m.falta, percentual: m.percentual, prazo: m.prazo as string | null, autor: autor(m.autor), versao: m.versao };
  });
  const orcamentos = lista(v.orcamentos).map((o): OrcamentoCasa => {
    if (!objeto(o) || !texto(o.ref) || typeof o.categoria !== "string" || !dinheiro(o.limite) || !dinheiro(o.gasto) || !dinheiro(o.restante) || !inteiro(o.versao)) throw contrato();
    return { ref: o.ref, categoria: o.categoria, limite: o.limite, gasto: o.gasto, restante: o.restante, autor: autor(o.autor), versao: o.versao };
  });
  return { mes: v.mes as string, metas, orcamentos };
}

/** `null` = sem família (404 not_found): a tela mostra o caminho para criar uma. */
export const casaQuery = (uid: number | undefined) => queryOptions({
  queryKey: ["conta", "familia-casa", uid],
  queryFn: async ({ signal }): Promise<Casa | null> => {
    try { return casaDe(await chamar<unknown>("/familia/casa", { headers: { Accept: "application/json" }, signal })); }
    catch (e) { if (e instanceof ErroApi && e.status === 404 && e.code === "not_found") return null; throw e; }
  },
  staleTime: 0, refetchOnWindowFocus: true, networkMode: "always", retry: meQuery.retry,
  enabled: !DEMO && uid != null,
});

// O `ref` é opaco ("m12", "o12"): só volta ao servidor, no path.
const url = (tipo: Tipo, ref?: string, fim = "") => `/familia/casa/${tipo}${ref ? `/${encodeURIComponent(ref)}` : ""}${fim}`;
export const criar = async (tipo: Tipo, corpo: object) => casaDe(await escrever<unknown>("POST", url(tipo), corpo));
export const editar = async (tipo: Tipo, ref: string, corpo: object) => casaDe(await escrever<unknown>("PUT", url(tipo, ref), corpo));
export const mover = async (tipo: Tipo, ref: string, sinal: "somar" | "subtrair", valor: string) => casaDe(await escrever<unknown>("POST", url(tipo, ref, "/movimentos"), { tipo: sinal, valor }));
export const apagar = (tipo: Tipo, ref: string) => semCorpo("DELETE", url(tipo, ref));

export type FalhaCasa = Falha & { campo?: "nome" | "categoria" | "valor" | "prazo"; recarregar?: true };
export const PERDIDA = "A resposta se perdeu. Confira antes de tentar de novo.";
export const ERRO_PRAZO = "Escolha uma data de hoje em diante.";
const SEM_SALVAR = "Não foi possível salvar. Seu rascunho foi mantido.";

/** O erro de uma escrita da casa em texto da tela (o texto do servidor nunca vai para a tela). `campo`: qual controle leva `aria-invalid`. */
export function falhaCasa(e: unknown, tipo: Tipo): FalhaCasa {
  if (!(e instanceof ErroApi) || e.status === 0 || e.status >= 500 || e.status < 400) return { texto: PERDIDA };
  if (e.status === 422) {
    const c = (e.details ?? []).flatMap((d) => d.loc); // `valor` vale também para alvo/limite zerado ou negativo (o servidor o recusa em `_valor`)
    if (c.some((x) => x === "alvo" || x === "limite" || x === "valor")) return { texto: ERRO_VALOR, campo: "valor" };
    if (c.some((x) => x === "prazo" || x === "data_alvo")) return { texto: ERRO_PRAZO, campo: "prazo" };
    if (c.includes("nome")) return { texto: "Dê um nome.", campo: "nome" };
    if (c.includes("categoria")) return { texto: "Escreva uma categoria.", campo: "categoria" };
    return { texto: SEM_SALVAR };
  }
  if (e.code === "edit_conflict") return { texto: "Alguém mudou isso. Recarregue.", recarregar: true };
  if (e.code === "category_taken") return { texto: "Essa categoria já tem orçamento.", campo: "categoria" };
  if (e.code === "insufficient") return { texto: tipo === "metas" ? "Não tem tanto guardado." : "Não tem tanto gasto lançado." };
  return falhaFamilia(e);
}
/** O que o servidor já mudou por baixo da tela: relê a casa (resposta perdida, item sumido, versão velha). */
export const deveReler = (e: unknown) => !(e instanceof ErroApi) || e.status === 0 || e.status >= 500 || e.status === 404 || e.code === "edit_conflict";
