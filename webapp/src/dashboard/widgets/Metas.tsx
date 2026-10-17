// A página /metas com backend (/api/v2/metas): o que o servidor mandou, sem somar nem
// recalcular dinheiro. 4xx nunca mostra dado guardado; rede/5xx com dado mostra o antigo, com aviso.
import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import type { Meta } from "../lib/api-v2.gen";
import { moneyText } from "../lib/format.js";
import { MESES_PADRAO, fraseCusto } from "../lib/metas-textos";
import { ErroApi, metasQuery } from "../lib/v2";
import { CRIAR_SENHA } from "../parts/Entrada";
import { Frame } from "../parts/Frame";
import { MetaCartao } from "../parts/MetaCartao";
import { MetaForm } from "../parts/MetaForm";

type Aberto = { meta?: Meta; comecar?: "reserva" | "valor" };

// Reserva primeiro; o resto na ordem do servidor. A página e o chat usam a mesma.
export const emOrdem = (metas: readonly Meta[]) => [...metas].sort((a, b) => Number(b.tipo === "reserva") - Number(a.tipo === "reserva"));
export const SEM_SENHA = "Crie sua senha para ver suas metas. Depois de criar, volte para o painel novo.";

function Bloqueio({ e }: { e: ErroApi }) {
  if (e.code === "password_required") {
    return (
      <div className="empty" role="alert">
        <p>{SEM_SENHA}</p>
        <a className="btn btn-ghost retry" href={CRIAR_SENHA.href}>{CRIAR_SENHA.texto}</a>
      </div>
    );
  }
  return (
    <div className="empty" role="alert">
      <p>{e.status === 401 ? "Sua sessão terminou." : "Não deu para abrir suas metas."}</p>
      <button type="button" className="btn btn-ghost retry" onClick={() => location.reload()}>Recarregar</button>
    </div>
  );
}

export function MetasPainel() {
  const q = useQuery(metasQuery);
  const [aberto, setAberto] = useState<Aberto | null>(null);
  const e = q.error instanceof ErroApi ? q.error : null;
  const e4 = e && e.status >= 400 && e.status < 500 ? e : null;
  const d = !e4 ? q.data : undefined;
  const reserva = d?.metas.find((m) => m.tipo === "reserva");
  const ordem = d ? emOrdem(d.metas) : [];
  const custoTxt = d?.custo_mensal.valor ? moneyText(d.custo_mensal.valor) : null;
  const nova = (
    <button type="button" className="btn btn-primary" disabled={!d} onClick={() => setAberto({})}>
      <i className="ph ph-plus" aria-hidden="true" />Nova meta
    </button>
  );
  return (
    <div className="panel span-12">
      <Frame id="metas-painel" title="Suas metas" real aside={e4 ? null : nova}>
        {e4 ? <Bloqueio e={e4} />
          : !d ? (q.isError && !q.isFetching
            ? (
              <div className="empty" role="alert">
                <p>Não deu para carregar suas metas.</p>
                <button type="button" className="btn btn-ghost retry" onClick={() => q.refetch()}>Tentar de novo</button>
              </div>
            )
            : <p role="status" className="faint">Carregando…</p>)
          : (
            <div className="metas-corpo" aria-busy={q.isFetching}>
              {q.isRefetchError && (
                <div className="prev-antigo" role="status">
                  <p>Não deu para atualizar. Mostrando o que carregou antes.</p>
                  <button type="button" className="btn btn-quiet" onClick={() => q.refetch()}>Tentar de novo</button>
                </div>
              )}
              {!reserva && (
                <div className="meta-convite">
                  <p>
                    <b>Reserva de emergência:</b> {MESES_PADRAO} meses das suas contas fixas
                    {custoTxt && <> (<span className="num">{custoTxt}</span>/mês)</>}
                    {fraseCusto(d.custo_mensal, true) && <>{custoTxt ? " " : ". "}{fraseCusto(d.custo_mensal, true)}</>}
                  </p>
                  <button type="button" className="btn btn-ghost" onClick={() => setAberto({ comecar: "reserva" })}>
                    <i className="ph ph-piggy-bank" aria-hidden="true" />Montar reserva
                  </button>
                </div>
              )}
              {!d.metas.length ? (
                <div className="empty">
                  <i className="ph ph-target" aria-hidden="true" />
                  <p>Você ainda não tem metas. Crie a primeira e eu mostro quanto já tem, quanto falta e se o seu ritmo chega lá.</p>
                  <button type="button" className="btn btn-ghost" onClick={() => setAberto({})}>Criar minha primeira meta</button>
                </div>
              ) : (
                <div className="metas-lista">
                  {ordem.map((m) => (
                    <MetaCartao key={m.id} m={m} custo={d.custo_mensal} editar={(valor) => setAberto({ meta: m, comecar: valor ? "valor" : undefined })} />
                  ))}
                </div>
              )}
            </div>
          )}
      </Frame>
      {aberto && d && <MetaForm key={aberto.meta?.id ?? aberto.comecar ?? "nova"} meta={aberto.meta} custo={d.custo_mensal} temReserva={!!reserva} comecar={aberto.comecar} fechar={() => setAberto(null)} />}
    </div>
  );
}
