import { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { AGENTES, acessoDe, agentesQuery, energiaDe } from "../lib/agentes";
import { agenteImg } from "../lib/brand";
import type { Msg } from "../lib/conversation";
import { ErroApi } from "../lib/v2";
import { GRUPO, GRUPOS } from "./Mencoes";
import { tituloDe } from "./CardAgente";

// O painel à direita da conversa (#/piggy, só em tela larga): quem dá para chamar e o índice dos cards desta conversa.
// Só texto de catálogo: nenhum número (saved_365d, fired_30d e a energia livre ficam fora de propósito).
// `uid` só vem de uma conta conferida (PiggyChat); `escondido` é a releitura da conta: some e não responde a clique, como a conversa.
// A reserva de espaço (`body[data-chat-painel]`, chat.css) acompanha o painel: sem catálogo e sem cards o painel nem existe, e a tela é a de uma coluna só.
export function PainelChat({ msgs, uid, escondido, conferindo }: { msgs: Msg[]; uid: number | undefined; escondido: boolean; conferindo: boolean }) {
  const q = useQuery(agentesQuery(uid));
  const d = q.isError ? undefined : q.data; // falhou: o bloco não existe (nem o dado antigo)
  const cards = msgs.filter((m) => m.role === "piggy" && m.agente && m.cartao);
  const itens = d ? GRUPOS.flatMap((g) => AGENTES.flatMap((a) => { const acesso = acessoDe(d, a.kind); return acesso && GRUPO[acesso] === g ? [{ ...a, acesso, g }] : []; })) : [];
  // 5xx ou rede (os que se tentam de novo): o painel fica, com "Tentar de novo"; sumir faria a coluna saltar depois das tentativas.
  // 4xx (beta desligado, plano): sem painel, a tela de uma coluna só.
  const falhou = q.isError && q.error instanceof ErroApi && (q.error.status === 0 || q.error.status >= 500);
  // O "Tentar de novo" volta a query a "pending" (sem dado): sem esta memória, o botão sumiria no clique e o foco cairia no body.
  const [erroDe, setErroDe] = useState<number>();
  useEffect(() => { if (falhou) setErroDe(uid); else if (d) setErroDe(undefined); }, [falhou, d, uid]);
  const instavel = falhou || (uid != null && erroDe === uid && !d && q.isFetching);
  const carregando = !d && !q.isError && !instavel && (conferindo || q.isFetching);
  const ligado = itens.length > 0 || cards.length > 0 || carregando || instavel;
  useEffect(() => {
    if (ligado) document.body.dataset.chatPainel = ""; else delete document.body.dataset.chatPainel;
    return () => { delete document.body.dataset.chatPainel; };
  }, [ligado]);
  if (!ligado) return null;

  let ultimo = "";
  const ir = (id: number) => { const el = document.getElementById(`msg-${id}`); el?.scrollIntoView({ block: "start" }); el?.focus({ preventScroll: true }); };
  return (
    <aside className="chat-painel" aria-label="Agentes e cards da conversa" inert={escondido} data-reconferindo={escondido || undefined}>
      {carregando && <p className="painel-nota" role="status" aria-busy="true">Carregando agentes…</p>}
      {instavel && (
        <div className="painel-erro">
          <p className="painel-nota" role="status">{q.isFetching ? "Tentando de novo…" : "Não deu para carregar os agentes agora."}</p>
          {/* aria-disabled, não disabled: o botão desabilitado perde o foco */}
          <button type="button" className="btn btn-ghost" aria-disabled={q.isFetching || undefined} onClick={() => { if (!q.isFetching) void q.refetch(); }}>Tentar de novo</button>
        </div>
      )}
      {itens.length > 0 && (
        <ul className="painel-lista">
          {itens.map((a) => {
            const cabeca = a.g !== ultimo ? (ultimo = a.g) : null, custo = a.acesso === "activate" || a.acesso === "no_energy" ? energiaDe(d!, a.kind)?.custo : undefined;
            return (
              <li key={a.kind}>
                {cabeca && <h2 className="painel-tit">{cabeca}</h2>}
                <button type="button" className="painel-item" aria-label={`Perguntar ao ${a.nome}`} onClick={() => window.dispatchEvent(new CustomEvent("dash:mencao", { detail: { nome: a.nome } }))}>
                  <img src={agenteImg(a.kind)} alt="" width={28} height={28} />
                  <span>{a.nome}</span>
                  {custo != null && <span className="msg-tag">⚡{custo}</span>}
                </button>
              </li>
            );
          })}
        </ul>
      )}
      {cards.length > 0 && (
        <section>
          <h2 className="painel-tit">Nesta conversa</h2>
          <ul className="painel-lista">
            {cards.map((m) => (
              <li key={m.id}>
                <button type="button" className="painel-item" onClick={() => ir(m.id)}>
                  <img src={agenteImg(m.agente!)} alt="" width={28} height={28} />
                  <span>{tituloDe(m.cartao!)}</span>
                </button>
              </li>
            ))}
          </ul>
        </section>
      )}
    </aside>
  );
}
