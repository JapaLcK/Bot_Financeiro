import { useInfiniteQuery } from "@tanstack/react-query";
import { ErroApi, FUSO, instante } from "../lib/v2";
import { mesAno } from "../lib/metas-textos";
import { historicoQuery } from "./dados";
import { PerfilFalhou } from "./escrita";

const QUANDO = new Intl.DateTimeFormat("pt-BR", { timeZone: FUSO, day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit", hourCycle: "h23" });
const quando = (iso: string) => { const p = Object.fromEntries(QUANDO.formatToParts(instante(iso)).map((x) => [x.type, x.value])); return `${p.day}/${p.month} ${p.hour}:${p.minute}`; };

// Membro: quem leu o quê (o log que cada leitura do titular grava). Erro nunca mostra a lista velha.
export function Historico({ uid, perfilFalhou }: { uid: number | undefined; perfilFalhou: (() => void) | undefined }) {
  const q = useInfiniteQuery(historicoQuery(uid));
  const sessao = q.error instanceof ErroApi && q.error.status === 401;
  const itens = (q.data?.pages ?? []).flatMap((p) => p.itens);
  let corpo;
  if (perfilFalhou) corpo = <PerfilFalhou tentar={perfilFalhou} />;
  else if (q.isError) corpo = (
    <div className="empty" role="alert">
      <p>{sessao ? "Sua sessão terminou." : "Não foi possível carregar."}</p>
      {sessao ? <button type="button" className="btn btn-ghost retry" onClick={() => location.reload()}>Recarregar</button>
        : <button type="button" className="btn btn-ghost retry" onClick={() => void (q.isFetchNextPageError ? q.fetchNextPage() : q.refetch())}>Tentar de novo</button>}
    </div>
  );
  else if (q.isPending) corpo = <p role="status" className="faint">Carregando…</p>;
  else if (!itens.length) corpo = <p className="faint">Ninguém viu nada ainda.</p>;
  else corpo = <>
    <ul className="fam-hist">
      {itens.map((i, n) => (
        <li key={n}><span><b>{i.quem}</b> viu {i.escopo === "agregados" ? "o resumo" : "os lançamentos"} de {mesAno(i.mes)}</span><span className="faint">{quando(i.quando)}</span></li>
      ))}
    </ul>
    {q.hasNextPage && <button type="button" className="btn btn-ghost" aria-disabled={q.isFetchingNextPage || undefined} onClick={() => { if (!q.isFetchingNextPage) void q.fetchNextPage(); }}>Ver mais</button>}
  </>;
  return <section className="fam-hist-sec" aria-labelledby="fam-hist-t"><h2 id="fam-hist-t">Quem viu</h2>{corpo}</section>;
}
