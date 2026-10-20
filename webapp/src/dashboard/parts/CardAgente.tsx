import { useEffect, type ReactNode } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { ContaTrocada, daConta, uidValido } from "../conta/api";
import type { CardAgente as Card } from "../lib/agentes";
import type { Investido } from "../lib/api-v2.gen";
import { DEMO, ErroApi, assinaturasQuery, investidoQuery, lerContaFresca, meQuery, orcamentosCategoriaQuery, resumoMesQuery } from "../lib/v2";
import { moneyText } from "../lib/format.js";
import { mesAno } from "../lib/metas-textos";
import { textos } from "../lib/bancos";
import { CardAssinaturas, contratoAssinaturas } from "./CardAssinaturas";
import { CardInvestido } from "./CardInvestido";
import { CardOrcamentos, contratoOrcamentos } from "./CardOrcamentos";
import { CardResumoMes, contratoResumoMes } from "./CardResumoMes";

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

type Leitura = { chave: string; titulo: string; ler: (signal: AbortSignal) => Promise<unknown>; contrato: (d: unknown) => boolean; desenha: (d: any) => ReactNode }; // eslint-disable-line @typescript-eslint/no-explicit-any

function leituraDe(c: Card): Leitura {
  switch (c.tipo) {
    case "assinaturas":
      return { chave: "card-assinaturas", titulo: "Assinaturas", ler: (signal) => assinaturasQuery.queryFn({ signal }), contrato: contratoAssinaturas, desenha: (d) => <CardAssinaturas d={d} /> };
    case "resumo_mes":
      return { chave: `card-resumo_mes-${c.mes}`, titulo: `Resumo de ${mesAno(c.mes)}`, ler: (signal) => resumoMesQuery(c.mes).queryFn({ signal }), contrato: (d) => contratoResumoMes(d, c.mes), desenha: (d) => <CardResumoMes d={d} motivos={c.motivos} /> };
    case "orcamentos":
      return { chave: "card-orcamentos", titulo: "Orçamentos", ler: (signal) => orcamentosCategoriaQuery.queryFn({ signal }), contrato: contratoOrcamentos, desenha: (d) => <CardOrcamentos d={d} /> };
    default: {
      const foco = c.tipo === "renda_fixa" ? "renda_fixa" : undefined;
      return { chave: "card-investido", titulo: foco ? "Renda fixa" : "Investimentos", ler: (signal) => investidoQuery.queryFn({ signal }), contrato: contratoInvestido, desenha: (d: Investido) => <CardInvestido d={d} foco={foco} /> };
    }
  }
}

export function CardAgente({ uid, c }: { uid: number; c: Card }) {
  const l = leituraDe(c), qc = useQueryClient();
  const q = useQuery({
    queryKey: ["conta", l.chave, uid], // ["conta", assunto, uid]: a chave que main.tsx trata na troca de conta
    queryFn: async ({ signal }) => daConta(uid, signal, await l.ler(signal)),
    staleTime: 0, refetchOnWindowFocus: true, networkMode: "always", retry: meQuery.retry,
    enabled: !DEMO && uidValido(uid),
  });
  const trocada = q.error instanceof ContaTrocada;
  useEffect(() => { if (trocada) void lerContaFresca(qc).catch(() => {}); }, [trocada, qc]);
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
  return <>{l.desenha(q.data)}</>;
}
