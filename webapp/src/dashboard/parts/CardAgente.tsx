import { useEffect } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { ContaTrocada, daConta, uidValido } from "../conta/api";
import type { CardAgente as Card } from "../lib/agentes";
import { DEMO, ErroApi, investidoQuery, lerContaFresca, meQuery } from "../lib/v2";
import { moneyText } from "../lib/format.js";
import { CardInvestido } from "./CardInvestido";

// O card abaixo da resposta de um agente (o servidor só diz QUAL: `card.tipo`). Os números vêm do /api/v2/investido, a mesma regra do
// que o agente leu. A /api/v2 lê o usuário da SESSÃO, não o `uid`: a resposta só vale depois de conferir que a sessão ainda é a de
// `uid` (daConta). Sessão de outra conta = nenhum número na tela, e a conta é relida (a conversa da anterior some em contaDaSessao).
const TITULO = { investido: "Investimentos", renda_fixa: "Renda fixa" };
const NUM = /^-?\d+\.\d+$/;
// Corpo fora do contrato não desenha meio número. Os motivos viram selos (texto): objeto ali derrubaria o render (Codex no #1198).
const textos = (v: unknown) => Array.isArray(v) && v.every((m) => typeof m === "string");
const contratoOk = (d: unknown) => {
  const x = d as { total?: unknown; por_tipo?: unknown; motivos?: unknown } | null;
  return !!x && (x.total === null || (typeof x.total === "string" && moneyText(x.total) !== null)) && textos(x.motivos) && Array.isArray(x.por_tipo)
    && (x.por_tipo as { tipo?: unknown; rotulo?: unknown; valor?: unknown; motivos?: unknown }[]).every((p) => p && typeof p.tipo === "string" && typeof p.rotulo === "string"
      && (p.valor === null || (typeof p.valor === "string" && NUM.test(p.valor))) && textos(p.motivos));
};

export function CardAgente({ uid, c }: { uid: number; c: Card }) {
  const qc = useQueryClient();
  const q = useQuery({
    queryKey: ["conta", "card-investido", uid], // ["conta", assunto, uid]: a chave que main.tsx trata na troca de conta
    queryFn: async ({ signal }) => daConta(uid, signal, await investidoQuery.queryFn({ signal })),
    staleTime: 0, refetchOnWindowFocus: true, networkMode: "always", retry: meQuery.retry,
    enabled: !DEMO && uidValido(uid),
  });
  const trocada = q.error instanceof ContaTrocada;
  useEffect(() => { if (trocada) void lerContaFresca(qc).catch(() => {}); }, [trocada, qc]);
  if (DEMO || !uidValido(uid) || trocada) return null;
  const e = q.error;
  if (e instanceof ErroApi && (e.status === 401 || e.status === 403)) return null; // sessão ou plano: o texto já disse o que importa
  if (q.isPending) return <section className="card-agente" aria-busy="true"><p className="card-agente-tit"><b>{TITULO[c.tipo]}</b></p><p className="faint">Carregando…</p></section>;
  if (e || !contratoOk(q.data)) {
    return (
      <section className="card-agente">
        <p className="card-agente-tit"><b>{TITULO[c.tipo]}</b></p>
        <p className="faint">Não deu para carregar os dados agora.</p>
        <button type="button" className="btn btn-ghost" disabled={q.isFetching} onClick={() => void q.refetch()}>Tentar de novo</button>
      </section>
    );
  }
  return <CardInvestido d={q.data} foco={c.tipo === "renda_fixa" ? "renda_fixa" : undefined} />;
}
