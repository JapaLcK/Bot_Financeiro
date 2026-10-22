import { useEffect, useRef } from "react";
import { useInfiniteQuery, useQuery, useQueryClient } from "@tanstack/react-query";
import { ContaTrocada, antesDaConta, comPrazo, daConta, uidValido } from "../../conta/api";
import { SESSAO } from "../../lib/conversaIa";
import { DEMO, ErroApi, apiGet, categoriasQuery, lerContaFresca, meQuery } from "../../lib/v2";
import type { Lancamento } from "../../lib/api-v2.gen";
import { LancamentoLinha } from "../LancamentoLinha";
import { textos } from "../../lib/bancos";
import { existe } from "../../lib/extrato-dias";
import { decimal } from "../../lib/format.js";

// Os lançamentos que um card da IA abre dentro dele (contas agora; categorias e dias no PR 4). Só leitura, de 10 em 10, e a conta é conferida
// ANTES de cada página (nenhum GET sai com a sessão de outra conta) e DEPOIS (a troca durante a busca não desenha nada). A chave tem o uid por
// ÚLTIMO e 3 elementos: é a forma que o main.tsx reconhece como "da conta anterior" e não relê com a sessão nova. `meta.foto`: o aviso do SSE não a relê (lib/eventos.ts).
// Página com item fora do contrato é erro com "Tentar de novo", nunca lista com linha faltando (é dinheiro) nem card derrubado.
type Filtros = { mes: string; conta?: number };
export const PRAZO_PAGINA_MS = 15_000; // o `comLimite` do app
const FALHA = "Não deu para carregar os lançamentos.";
const TEXTOS_DA_LINHA = ["descricao", "categoria", "mensagem", "hora", "instituicao"] as const; // o que o LancamentoLinha desenha como texto: objeto ali derrubaria o card
const itemOk = (i: unknown) => { const x = i as Partial<Lancamento> | null; return !!x && typeof x.id === "string" && typeof x.data === "string" && existe(x.data.slice(0, 10)) && (x.tipo === "entrada" || x.tipo === "saida") && decimal(x.valor) !== null && typeof x.moeda === "string" && textos(x.motivos) && TEXTOS_DA_LINHA.every((k) => x[k] == null || typeof x[k] === "string"); };
// O prazo cobre a página INTEIRA: as leituras do perfil (antes e depois) não levam sinal, então o corte rejeita por cima delas.
const cortavel = <T,>(p: Promise<T>, s: AbortSignal) => Promise.race([p, new Promise<never>((_, no) => {
  const corta = () => no(new DOMException("Aborted", "AbortError"));
  if (s.aborted) corta(); else s.addEventListener("abort", corta, { once: true });
})]);

export function LancamentosDoCard({ uid, filtros }: { uid: number; filtros: Filtros }) {
  const qc = useQueryClient();
  const q = useInfiniteQuery({
    queryKey: ["conta", `card-ia-lanc:${JSON.stringify(filtros)}`, uid],
    initialPageParam: null as string | null,
    queryFn: ({ signal, pageParam }) => comPrazo((s) => cortavel((async () => {
      await antesDaConta(qc, uid);
      const r = await apiGet("/lancamentos", s, { ...filtros, limite: 10, cursor: pageParam });
      if (!Array.isArray(r?.itens) || !r.itens.every(itemOk) || !(r.proximo == null || typeof r.proximo === "string")) throw new Error("lançamentos fora do contrato");
      return daConta(uid, s, r);
    })(), s), PRAZO_PAGINA_MS, signal),
    meta: { foto: true },
    getNextPageParam: (p) => p.proximo ?? undefined,
    staleTime: 0, gcTime: 0, refetchOnWindowFocus: false, networkMode: "always", retry: meQuery.retry, // gcTime 0: lançamento não fica no cache depois de recolher
    enabled: !DEMO && uidValido(uid),
  });
  const cats = useQuery(categoriasQuery).data?.categorias ?? [];
  const trocada = q.error instanceof ContaTrocada;
  // O botão que ia ao ar (a última página tira "Carregar mais"; o sucesso tira o erro) levava o foco junto, para o <body>: o foco vai para o 1º item novo
  // (ou, sem item, o card; falhou de novo, o "Tentar de novo"). Só quando o botão tinha o foco no clique: o clique de mouse do Safari não foca o botão.
  const raiz = useRef<HTMLDivElement>(null), foco = useRef<number | null>(null);
  useEffect(() => {
    if (q.isFetching || foco.current === null) return;
    const n = foco.current;
    foco.current = null;
    if (document.activeElement !== document.body) return; // o foco ficou no botão (há mais páginas, ou falhou de novo) ou foi para outro lugar
    const alvo = raiz.current?.querySelector<HTMLElement>(q.error ? ".ci-erro button" : `.ci-lanc-lista > li:nth-child(${n + 1})`) ?? raiz.current;
    alvo?.setAttribute("tabindex", "-1");
    alvo?.focus();
  }, [q.isFetching]);
  useEffect(() => { if (trocada) void lerContaFresca(qc).catch(() => {}); }, [trocada, qc]);
  if (trocada) return null; // a conta mudou: o perfil relido apaga a conversa, e o card some com ela
  const vistos = new Set<string>(); // a mesma linha em duas páginas (a lista é lida ao vivo) entra uma vez
  const itens = (q.data?.pages ?? []).flatMap((p) => p.itens).filter((i) => !vistos.has(i.id) && vistos.add(i.id));
  const e = q.error, sessao = e instanceof ErroApi && (e.status === 401 || e.status === 403);
  const focoNo = (b: EventTarget) => { foco.current = document.activeElement === b ? itens.length : null; };
  const de = () => void (q.isFetchNextPageError ? q.fetchNextPage() : q.refetch());
  return (
    <div ref={raiz} className="ci-lanc" aria-busy={q.isFetching}>
      {itens.length > 0 && <ul className="ci-lanc-lista">{itens.map((i) => <LancamentoLinha key={i.id} item={i} categorias={cats} />)}</ul>}
      {q.isPending && (
        <>
          <p className="sr-only" role="status">Carregando lançamentos…</p>
          <ul className="ci-lanc-lista" aria-hidden="true">{[0, 1, 2].map((n) => <li key={n} className="ci-esq"><i /><span /><b /></li>)}</ul>
        </>
      )}
      {e && (
        <div className="ci-erro" role="alert">
          <p>{sessao ? SESSAO : FALHA}</p>
          {!sessao && <button type="button" className="btn btn-ghost" aria-disabled={q.isFetching} onClick={(ev) => { if (!q.isFetching) { focoNo(ev.currentTarget); de(); } }}>Tentar de novo</button>}
        </div>
      )}
      {!q.isPending && !e && itens.length === 0 && <p className="ci-vazio">Nenhum lançamento encontrado.</p>}
      {q.hasNextPage && !e && <button type="button" className="btn btn-ghost ci-mais" aria-disabled={q.isFetchingNextPage} onClick={(ev) => { if (!q.isFetchingNextPage) { focoNo(ev.currentTarget); void q.fetchNextPage(); } }}>Carregar mais lançamentos</button>}
    </div>
  );
}
