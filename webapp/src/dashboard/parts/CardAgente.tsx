import type { ReactNode } from "react";
import { useQuery } from "@tanstack/react-query";
import { daConta, uidValido } from "../conta/api";
import type { CardAgente as Card } from "../lib/agentes";
import type { Investido } from "../lib/api-v2.gen";
import { DEMO, ErroApi, assinaturasQuery, caixinhasQuery, contasAPagarQuery, investidoQuery, meQuery, orcamentosCategoriaQuery, resumoMesQuery } from "../lib/v2";
import { moneyText } from "../lib/format.js";
import { mesAno } from "../lib/metas-textos";
import { textos } from "../lib/bancos";
import { CardAssinaturas, contratoAssinaturas } from "./CardAssinaturas";
import { CardCaixinhas, contratoCaixinhas } from "./CardCaixinhas";
import { CardContas, contratoContas } from "./CardContas";
import { CardInvestido } from "./CardInvestido";
import { CardOrcamentos, contratoOrcamentos } from "./CardOrcamentos";
import { CardComparacaoMeses, contratoComparacaoMeses, tituloComparacao } from "./CardComparacaoMeses";
import { CardResumoMes, contratoResumoMes } from "./CardResumoMes";
import { useContaTrocada } from "./cardIa/naConta";

// O card abaixo da resposta de um agente (o servidor só diz QUAL: `card.tipo`, e o mês no resumo). Os números vêm da rota v2 do tipo, a mesma
// regra do que o agente leu. A /api/v2 lê o usuário da SESSÃO, não o `uid`: a resposta só vale depois de conferir que a sessão ainda é a de
// `uid` (daConta). Sessão de outra conta = nenhum número na tela, e a conta é relida (a conversa da anterior some em contaDaSessao).
const NUM = /^-?\d+\.\d+$/;
// Corpo fora do contrato não desenha meio número. Os motivos viram selos (texto): objeto ali derrubaria o render (Codex no #1198).
const contratoInvestido = (d: unknown) => {
  const x = d as { total?: unknown; por_tipo?: unknown; motivos?: unknown } | null;
  return !!x && (x.total === null || (typeof x.total === "string" && moneyText(x.total) !== null)) && textos(x.motivos) && Array.isArray(x.por_tipo)
    && (x.por_tipo as { tipo?: unknown; rotulo?: unknown; valor?: unknown; motivos?: unknown }[]).every((p) => p && typeof p.tipo === "string" && typeof p.rotulo === "string"
      && (p.valor === null || (typeof p.valor === "string" && NUM.test(p.valor))) && textos(p.motivos));
};

// `outroPeriodo`: a rota respondeu um mês ou dia que não é o do texto (virou entre a consulta e o card): nenhum card, nem erro.
type Leitura = { chave: string; titulo: string; ler: (signal: AbortSignal) => Promise<unknown>; contrato: (d: unknown) => boolean; desenha: (d: any) => ReactNode; outroPeriodo?: (d: any) => boolean }; // eslint-disable-line @typescript-eslint/no-explicit-any

function leituraDe(c: Card): Leitura {
  switch (c.tipo) {
    case "assinaturas":
      return { chave: "card-assinaturas", titulo: "Assinaturas", ler: (signal) => assinaturasQuery.queryFn({ signal }), contrato: contratoAssinaturas, desenha: (d) => <CardAssinaturas d={d} /> };
    case "resumo_mes":
      return { chave: `card-resumo_mes-${c.mes}`, titulo: `Resumo de ${mesAno(c.mes)}`, ler: (signal) => resumoMesQuery(c.mes).queryFn({ signal }), contrato: (d) => contratoResumoMes(d, c.mes), desenha: (d) => <CardResumoMes d={d} motivos={c.motivos} /> };
    case "comparacao_meses": {
      // Duas leituras, uma conferência de conta depois das duas (o queryFn abaixo): falha de uma = o card inteiro falha, nunca um lado só.
      const [a, b] = c.meses;
      return { chave: `card-comparacao_meses-${a}-${b}`, titulo: tituloComparacao(c.meses), ler: (signal) => Promise.all(c.meses.map((m) => resumoMesQuery(m).queryFn({ signal }))), contrato: (d) => contratoComparacaoMeses(d, c.meses), desenha: (d) => <CardComparacaoMeses d={d} meses={c.meses} motivos={c.motivos} /> };
    }
    case "orcamentos":
      return { chave: "card-orcamentos", titulo: `Orçamentos de ${mesAno(c.mes)}`, ler: (signal) => orcamentosCategoriaQuery.queryFn({ signal }), contrato: contratoOrcamentos, desenha: (d) => <CardOrcamentos d={d} />, outroPeriodo: (d) => d.mes !== c.mes };
    case "contas":
      return { chave: "card-contas", titulo: "Contas a pagar", ler: (signal) => contasAPagarQuery.queryFn({ signal }), contrato: contratoContas, desenha: (d) => <CardContas d={d} />, outroPeriodo: (d) => d.hoje !== c.hoje };
    case "caixinhas":
      return { chave: "card-caixinhas", titulo: "Caixinhas", ler: (signal) => caixinhasQuery.queryFn({ signal }), contrato: contratoCaixinhas, desenha: (d) => <CardCaixinhas d={d} /> };
    default: {
      const foco = c.tipo === "renda_fixa" ? "renda_fixa" : undefined;
      return { chave: "card-investido", titulo: foco ? "Renda fixa" : "Investimentos", ler: (signal) => investidoQuery.queryFn({ signal }), contrato: contratoInvestido, desenha: (d: Investido) => <CardInvestido d={d} foco={foco} /> };
    }
  }
}

export const tituloDe = (c: Card) => leituraDe(c).titulo;

export function CardAgente({ uid, c }: { uid: number; c: Card }) {
  const l = leituraDe(c);
  const q = useQuery({
    queryKey: ["conta", l.chave, uid], // ["conta", assunto, uid]: a chave que main.tsx trata na troca de conta
    queryFn: async ({ signal }) => daConta(uid, signal, await l.ler(signal)),
    staleTime: 0, refetchOnWindowFocus: true, networkMode: "always", retry: meQuery.retry,
    enabled: !DEMO && uidValido(uid),
  });
  const trocada = useContaTrocada(q.error);
  if (DEMO || !uidValido(uid) || trocada) return null;
  const e = q.error;
  if (e instanceof ErroApi && (e.status === 401 || e.status === 403)) return null; // sessão ou plano: o texto já disse o que importa
  if (q.isPending) return <section className="card-agente" aria-busy="true"><p className="card-agente-tit"><b>{l.titulo}</b></p><p className="faint">Carregando…</p></section>;
  if (e || !l.contrato(q.data)) {
    return (
      <section className="card-agente">
        <p className="card-agente-tit"><b>{l.titulo}</b></p>
        <p className="faint">Não deu para carregar os dados agora.</p>
        <button type="button" className="btn btn-ghost" disabled={q.isFetching} onClick={() => void q.refetch()}>Tentar de novo</button>
      </section>
    );
  }
  if (l.outroPeriodo?.(q.data)) return null;
  return <>{l.desenha(q.data)}</>;
}
