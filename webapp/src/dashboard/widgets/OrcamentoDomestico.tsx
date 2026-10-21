// A página /orcamento (método dos potes) sobre /api/v2/orcamento-domestico: o servidor faz a conta
// (tetos, gasto, restante, %), o cliente só mostra; Number() só dá a largura das barras e as fatias.
// 4xx nunca mostra dado guardado; rede/5xx com dado mostra o antigo, com aviso.
import { useState, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { useIsMutating, useQuery } from "@tanstack/react-query";
import type { OrcamentoDomestico } from "../lib/api-v2.gen";
import { moneyText } from "../lib/format.js";
import { mesAno } from "../lib/metas-textos";
import { DEMO, ErroApi, orcamentoQuery } from "../lib/v2";
import { mesDe } from "../lib/store.js";
import type { DashState } from "../lib/types";
import { Bloqueio } from "../parts/Bloqueio";
import { Frame } from "../parts/Frame";
import { Info } from "../parts/Info";
import { GastoFixoForm } from "../parts/GastoFixoForm";
import { PoteLancamentos } from "../parts/PoteLancamentos";
import { PotesTabela } from "../parts/PotesTabela";
import { PotesVisao } from "../parts/PotesVisao";
import { CHAVE_ESCRITA, OrcamentoForm, useEscrita, type Modo } from "../parts/OrcamentoForm";

function Corpo({ d, mes, busy, antigo, slot }: { d: OrcamentoDomestico; mes: string; busy: boolean; antigo: ReactNode; slot: HTMLElement | null }) {
  const [aberto, setAberto] = useState<Modo | null>(null);
  const [poteAberto, setPoteAberto] = useState<string | null>(null);
  const escrita = useEscrita(mes);
  const trava = useIsMutating({ mutationKey: CHAVE_ESCRITA }) > 0;
  const sem = d.total.usado_pct === null;
  const informada = d.renda.origem === "informada";
  const doModal = d.potes.find((p) => p.chave === poteAberto);
  const avisos = antigo || escrita.isError || sem;
  return <div className="orc-conteudo" aria-busy={busy}>
    {/* "Editar percentuais" no cabeçalho da página (pages.tsx dá o lugar); no protótipo não há escrita. */}
    {!DEMO && slot && createPortal(
      <button type="button" className="btn btn-ghost" disabled={trava} onClick={() => setAberto("potes")}><i className="ph ph-sliders-horizontal" aria-hidden="true" /> Editar percentuais</button>, slot)}
    {avisos && (
      <div className="span-12 orc-avisos">
        {antigo}
        {escrita.isError && <p role="alert" className="warn">{escrita.erro}</p>}
        {sem && <p className="orc-dica" role="status">Sem renda neste mês: informe a renda para dividir nos potes.</p>}
      </div>
    )}
    <PotesVisao d={d} />
    <div className="span-5 stack orc-lado">
      <div className="panel">
        <Frame id="orcamento-renda" className="orc-w" real title="Renda do mês"
          info={<Info rotulo="Como os potes são calculados"><p>Os percentuais valem sobre a sua renda. Metas e Liberdade financeira contam o que você guardou; os outros potes, o que você gastou.</p></Info>}
          aside={!DEMO && <button type="button" className="btn btn-ghost orc-editar" disabled={trava} onClick={() => setAberto("renda")}><i className="ph ph-pencil-simple" aria-hidden="true" /> Editar</button>}>
          <p className="orc-renda-num num">{moneyText(d.renda.valor)}</p>
          <div className="orc-origem">
            <span className="orc-ico orc-ico-neutro" aria-hidden="true"><i className="ph ph-bank" /></span>
            {informada ? <>
              <span><b>Informada por você</b></span>
              {!DEMO && <button type="button" className="btn btn-quiet" disabled={trava} onClick={() => escrita.mutate({ renda: { mes, valor: null } })}>Usar a calculada</button>}
            </> : <span><b>Calculada automaticamente</b><small className="faint">Soma das suas receitas no mês</small></span>}
          </div>
        </Frame>
      </div>
    </div>
    <PotesTabela potes={d.potes} abrir={setPoteAberto} />
    {doModal && <PoteLancamentos p={doModal} mes={mes} guarda={doModal.guardado} fechar={() => setPoteAberto(null)} />}
    {aberto && <OrcamentoForm key={aberto} modo={aberto} mes={mes} d={d} fechar={() => setAberto(null)} />}
  </div>;
}

export function OrcamentoPainel({ s, slot }: { s: DashState; slot: HTMLElement | null }) {
  const mes = mesDe(s);
  const q = useQuery(orcamentoQuery(mes));
  const e = q.error instanceof ErroApi ? q.error : null;
  const e4 = e && e.status >= 400 && e.status < 500 ? e : null;
  const d = !e4 ? q.data : undefined;
  // O gasto fixo tem plano próprio (Essencial, e o orçamento é Plus): fica de pé com o orçamento carregando, com erro ou bloqueado.
  const [gastoFixo, setGastoFixo] = useState<"form" | "feito" | null>(null);
  const gasto = !DEMO && <>
    {slot && createPortal(<button type="button" className="btn btn-ghost" onClick={() => setGastoFixo("form")}><i className="ph ph-plus" aria-hidden="true" /> Gasto fixo</button>, slot)}
    {gastoFixo === "feito" && <div className="span-12 orc-avisos"><p role="status" className="orc-dica">Gasto fixo cadastrado. Ele entra na Previsão.</p></div>}
    {gastoFixo === "form" && <GastoFixoForm feito={() => setGastoFixo("feito")} fechar={() => setGastoFixo((g) => g === "form" ? null : g)} />}
  </>;
  if (d) {
    return <>{gasto}<Corpo key={mes} d={d} mes={mes} busy={q.isFetching} slot={slot} antigo={q.isRefetchError && (
      <div className="prev-antigo" role="status">
        <p>Não deu para atualizar. Mostrando o que carregou antes.</p>
        <button type="button" className="btn btn-quiet" onClick={() => q.refetch()}>Tentar de novo</button>
      </div>
    )} /></>;
  }
  return (
    <>{gasto}<div className="panel span-12">
      <Frame id="orcamento" title={`Potes de ${mesAno(mes)}`} real>
        {e4 ? <Bloqueio e={e4} convite="O Orçamento doméstico é do Plus e do Pro." o="o orçamento" />
          : q.isError && !q.isFetching
            ? (
              <div className="empty" role="alert">
                <p>Não deu para carregar o orçamento.</p>
                <button type="button" className="btn btn-ghost retry" onClick={() => q.refetch()}>Tentar de novo</button>
              </div>
            )
            : <p role="status" className="faint">Carregando…</p>}
      </Frame>
    </div></>
  );
}
