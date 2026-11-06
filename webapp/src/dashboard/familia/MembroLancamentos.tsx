import { useEffect, useRef } from "react";
import { useInfiniteQuery, useQueryClient } from "@tanstack/react-query";
import { moneyText } from "../lib/format.js";
import { falhaFamilia } from "./api";
import { lancamentosQuery, motivoDe } from "./dados";
import { FalhaFamilia } from "./escrita";

const cap = (s: string) => s.charAt(0).toUpperCase() + s.slice(1);

// Titular: os lançamentos de um membro, de página em página. A categoria é a CHAVE do membro: o nome vem das categorias dos resumos dele (`nomes`),
// nunca do catálogo do titular (que é de outra conta). Só entra aqui depois do resumo (o pai só monta com `lancamentos` liberado).
export function MembroLancamentos({ uid, refMembro, nome, mes, nomes, aoParar, aoSair }: { uid: number | undefined; refMembro: string; nome: string; mes: string; nomes: Map<string, string>; aoParar: () => void; aoSair: () => void }) {
  const qc = useQueryClient();
  const opcoes = lancamentosQuery(uid, refMembro, mes);
  const q = useInfiniteQuery(opcoes);
  const motivo = q.isError ? motivoDe(q.error) : null;
  const reiniciou = useRef(false); // cursor recusado: descarta as páginas e relê a partir da primeira, UMA vez
  useEffect(() => {
    if (motivo === "consentimento") aoParar();
    else if (motivo === "saiu") aoSair();
    else if (motivo === "cursor" && !reiniciou.current) { reiniciou.current = true; void qc.resetQueries({ queryKey: opcoes.queryKey }); }
  }, [motivo]);

  const itens = (q.data?.pages ?? []).flatMap((p) => p.itens);
  let corpo;
  if (motivo === "consentimento") corpo = <p role="alert" className="warn">{nome} parou de compartilhar os lançamentos.</p>;
  else if (motivo === "plano") corpo = <FalhaFamilia falha={falhaFamilia(q.error)} />;
  else if (motivo === "saiu") corpo = <p role="alert" className="warn">{nome} não está mais na família.</p>;
  else if (q.isError) corpo = (
    <div className="empty" role="alert">
      <p>{motivo === "sessao" ? "Sua sessão terminou." : "Não foi possível carregar."}</p>
      {motivo === "sessao" ? <button type="button" className="btn btn-ghost retry" onClick={() => location.reload()}>Recarregar</button>
        : <button type="button" className="btn btn-ghost retry" onClick={() => void (q.isFetchNextPageError ? q.fetchNextPage() : q.refetch())}>Tentar de novo</button>}
    </div>
  );
  else if (q.isPending) corpo = <p role="status" className="faint">Carregando…</p>;
  else corpo = <>
    {itens.length > 0 && <ul className="fam-lanc-lista">{itens.map((l, n) => {
      const ganho = l.tipo === "entrada", chave = l.categoria;
      const valor = l.moeda === "BRL" ? moneyText(l.valor) : `${l.valor} ${l.moeda}`;
      return <li key={n} className="lanc-linha fam-lanc">
        <span className="fam-lanc-txt"><b>{l.descricao ?? "Sem descrição"}</b><span className="faint">{l.data.slice(8, 10)}/{l.data.slice(5, 7)} · <bdi>{chave ? nomes.get(chave) ?? cap(chave) : "Sem categoria"}</bdi></span></span>
        <span className={`lanc-valor num${ganho ? " gain" : ""}`}>{ganho ? "+" : "−"}{valor}</span>
      </li>;
    })}</ul>}
    {!itens.length && !q.hasNextPage && <p className="faint">Nenhum lançamento neste mês.</p>}
    {q.hasNextPage && <button type="button" className="btn btn-ghost" aria-disabled={q.isFetchingNextPage || undefined} onClick={() => { if (!q.isFetchingNextPage) void q.fetchNextPage(); }}>Ver mais</button>}
  </>;
  return <section aria-labelledby="fam-lanc-t"><h3 id="fam-lanc-t">Lançamentos</h3>{corpo}</section>;
}
