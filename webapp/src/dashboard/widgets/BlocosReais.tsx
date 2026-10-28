// Os blocos do Resumo que mostram dado real: fatura, parcelas, renda, "Piggy notou" e Rendimentos. O corpo é o MESMO card do chat (parts/cardIa/), alimentado pela
// /api/v2 em vez do bloco da IA; dinheiro é o texto da API. Resposta fora do contrato vira a frase abaixo, nunca "R$ NaN". 4xx nunca mostra dado guardado.
import type { ReactNode } from "react";
import { useQuery, type UseQueryResult } from "@tanstack/react-query";
import { ErroApi, FUSO, cartoesQuery, insightsQuery, parcelasQuery, rendaQuery, rendimentoQuery } from "../lib/v2";
import { Bloqueio } from "../parts/Bloqueio";
import { LimiteCard } from "../parts/CardIa";
import { CardFatura, CardParcelas, cartoesOk, parcelasOk } from "../parts/cardIa/cartao";
import { texto } from "../parts/cardIa/legado";
import { CardPiggy, insightsOk } from "../parts/cardIa/piggy";
import { CardRenda, rendaOk } from "../parts/cardIa/renda";
import { CardRendimento, rendimentoOk } from "../parts/cardIa/rendimento";
import { Frame } from "../parts/Frame";
import { AvisoAntigo } from "./Previsao";

const NAO_LEU = <p className="empty" role="alert">Não deu para ler este bloco agora.</p>;
const hora = (ms: number) => new Intl.DateTimeFormat("pt-BR", { timeZone: FUSO, hour: "2-digit", minute: "2-digit" }).format(ms);
const SEM_PLANO = "Este bloco não está no seu plano.";

function BlocoReal<D>({ id, title, q, ok, o, convite = SEM_PLANO, esperando = "Carregando…", children }: {
  id: string; title: string; q: UseQueryResult<D, Error>; ok: (d: unknown) => boolean;
  o: string; convite?: string; esperando?: string; children: (d: D) => ReactNode;
}) {
  const e4 = q.error instanceof ErroApi && q.error.status >= 400 && q.error.status < 500 ? q.error : null;
  const d = q.data;
  return (
    <Frame id={id} title={title} real>
      {e4 ? <Bloqueio e={e4} convite={convite} o={o} />
        : d === undefined ? (q.isError && !q.isFetching
          ? (
            <div className="empty" role="alert">
              <p>Não deu para carregar {o}.</p>
              <button type="button" className="btn btn-ghost retry" onClick={() => q.refetch()}>Tentar de novo</button>
            </div>
          )
          : <p role="status" className="faint">{esperando}</p>)
        : !ok(d) ? NAO_LEU
        : (
          <div className="card-ia ci-corpo bloco-real" role="region" tabIndex={0} aria-label={title}>
            {q.isRefetchError && <AvisoAntigo q={q} texto={`Mostrando o que foi lido às ${hora(q.dataUpdatedAt)}.`} />}
            <LimiteCard fallback={NAO_LEU}>{children(d)}</LimiteCard>
          </div>
        )}
    </Frame>
  );
}

export const FaturaReal = () => (
  <BlocoReal id="fatura" title="Fatura do cartão" q={useQuery(cartoesQuery)} ok={(d) => cartoesOk(d, texto)} o="a fatura">{(d) => <CardFatura d={d} />}</BlocoReal>
);
export const ParcelasReal = () => (
  <BlocoReal id="parcelas" title="Parcelas futuras" q={useQuery(parcelasQuery)} ok={(d) => parcelasOk(d, texto)} o="as parcelas">{(d) => <CardParcelas d={d} />}</BlocoReal>
);
export const RendaReal = () => (
  <BlocoReal id="renda" title="Renda mês a mês" q={useQuery(rendaQuery)} ok={(d) => rendaOk(d, texto)} o="a renda" convite="A renda mês a mês é do Plus e do Pro.">{(d) => <CardRenda d={d} />}</BlocoReal>
);
export const PiggyReal = () => (
  <BlocoReal id="piggy" title="Piggy notou" q={useQuery(insightsQuery)} ok={insightsOk} o="os destaques" convite="Os destaques do Piggy são do Plus e do Pro." esperando="O Piggy está olhando os seus números…">
    {(d) => <CardPiggy d={d} />}
  </BlocoReal>
);
export const RendimentoReal = () => (
  <BlocoReal id="rendimento" title="Rendimentos" q={useQuery(rendimentoQuery)} ok={rendimentoOk} o="os rendimentos">{(d) => <CardRendimento d={{ ...d, rendimentos: d.rendimentos ?? null }} />}</BlocoReal>
);
