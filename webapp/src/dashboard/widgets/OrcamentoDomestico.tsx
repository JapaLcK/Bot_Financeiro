// A página /orcamento (método dos potes) sobre /api/v2/orcamento-domestico: o servidor faz a conta
// (tetos, gasto, restante, %), o cliente só mostra; Number() só dá a largura das barras.
// 4xx nunca mostra dado guardado; rede/5xx com dado mostra o antigo, com aviso.
import { useState, type ReactNode } from "react";
import { useIsMutating, useQuery } from "@tanstack/react-query";
import type { OrcamentoDomestico } from "../lib/api-v2.gen";
import { moneyText } from "../lib/format.js";
import { ehNegativo, mesAno } from "../lib/metas-textos";
import { DEMO, ErroApi, orcamentoQuery } from "../lib/v2";
import { mesDe } from "../lib/store.js";
import type { DashState } from "../lib/types";
import { CRIAR_SENHA } from "../parts/Entrada";
import { Frame } from "../parts/Frame";
import { CHAVE_ESCRITA, OrcamentoForm, mostra, useEscrita, type Modo } from "../parts/OrcamentoForm";
import { ConvitePro } from "./Compromissos";

export function Bloqueio({ e, pro = "O Orçamento doméstico é do Plus e do Pro." }: { e: ErroApi; pro?: string }) {
  if (e.code === "pro_required") return <ConvitePro texto={pro} />;
  if (e.code === "password_required") {
    return (
      <div className="empty" role="alert">
        <p>Crie sua senha para ver o orçamento. Depois de criar, volte para o painel novo.</p>
        <a className="btn btn-ghost retry" href={CRIAR_SENHA.href}>{CRIAR_SENHA.texto}</a>
      </div>
    );
  }
  return (
    <div className="empty" role="alert">
      <p>{e.status === 401 ? "Sua sessão terminou." : "Não deu para abrir o orçamento."}</p>
      <button type="button" className="btn btn-ghost retry" onClick={() => location.reload()}>Recarregar</button>
    </div>
  );
}

const pctTxt = (v: string | null) => v === null ? "—" : `${Math.round(Number(v))}%`;
const largura = (v: string | null) => v === null ? 0 : Math.min(Math.max(Number(v), 0), 100);
const COR = /^#[0-9a-f]{6}$/i;

function Corpo({ d, mes, busy, antigo }: { d: OrcamentoDomestico; mes: string; busy: boolean; antigo: ReactNode }) {
  const [aberto, setAberto] = useState<Modo | null>(null);
  const escrita = useEscrita(mes);
  const trava = useIsMutating({ mutationKey: CHAVE_ESCRITA }) > 0;
  const sem = d.total.usado_pct === null;
  const informada = d.renda.origem === "informada";
  return (
    <div className="orc-corpo" aria-busy={busy}>
      {antigo}
      <section className="orc-renda" aria-label="Renda do mês">
        <div>
          <p className="orc-rotulo">Renda do mês</p>
          <p className="orc-renda-valor num">{moneyText(d.renda.valor)}</p>
          <p className="faint">{informada ? "informada por você" : "calculada pelas receitas do mês"}</p>
        </div>
        {!DEMO && (
          <div className="orc-acoes">
            <button type="button" className="btn btn-ghost" disabled={trava} onClick={() => setAberto("renda")}>Editar renda</button>
            {informada && <button type="button" className="btn btn-quiet" disabled={trava} onClick={() => escrita.mutate({ renda: { mes, valor: null } })}>Usar a calculada</button>}
            <button type="button" className="btn btn-ghost" disabled={trava} onClick={() => setAberto("potes")}>Editar percentuais</button>
          </div>
        )}
      </section>
      {escrita.isError && <p role="alert" className="warn">{escrita.erro}</p>}
      {sem && <p className="orc-dica" role="status">Sem renda neste mês: informe a renda para dividir nos potes.</p>}
      <ul className="orc-potes">
        {d.potes.map((p) => {
          const passou = ehNegativo(p.restante);
          return (
            <li key={p.chave} className="orc-pote">
              <div className="orc-pote-topo">
                <span className="orc-ponto" style={COR.test(p.cor) ? { background: p.cor } : undefined} aria-hidden="true" />
                <h3>{p.nome}</h3>
                <span className="num faint">{mostra(p.pct)}%</span>
              </div>
              <div className={`meter${passou ? " warn" : ""}`} aria-hidden="true"><span style={{ width: `${largura(p.usado_pct)}%` }} /></div>
              <dl className="orc-valores">
                <div><dt>Teto</dt><dd className="num">{moneyText(p.teto)}</dd></div>
                <div><dt>Gasto</dt><dd className="num">{moneyText(p.gasto)}{p.usado_pct !== null && <span className="faint"> · {pctTxt(p.usado_pct)}</span>}</dd></div>
                <div><dt>Restante</dt><dd className={`num${passou ? " warn" : ""}`}>
                  {passou ? <><i className="ph ph-warning" aria-hidden="true" /> passou {moneyText(p.restante.slice(1))} do teto</> : moneyText(p.restante)}
                </dd></div>
              </dl>
            </li>
          );
        })}
      </ul>
      <dl className="orc-total orc-valores">
        <div><dt>Total dos tetos</dt><dd className="num">{moneyText(d.total.teto)}</dd></div>
        <div><dt>Gasto no mês</dt><dd className="num">{moneyText(d.total.gasto)}<span className="faint"> · {pctTxt(d.total.usado_pct)}</span></dd></div>
        <div><dt>Restante</dt><dd className={`num${ehNegativo(d.total.restante) ? " warn" : ""}`}>{ehNegativo(d.total.restante) ? `passou ${moneyText(d.total.restante.slice(1))}` : moneyText(d.total.restante)}</dd></div>
      </dl>
      {aberto && <OrcamentoForm key={aberto} modo={aberto} mes={mes} d={d} fechar={() => setAberto(null)} />}
    </div>
  );
}

export function OrcamentoPainel({ s }: { s: DashState }) {
  const mes = mesDe(s);
  const q = useQuery(orcamentoQuery(mes));
  const e = q.error instanceof ErroApi ? q.error : null;
  const e4 = e && e.status >= 400 && e.status < 500 ? e : null;
  const d = !e4 ? q.data : undefined;
  return (
    <div className="panel span-12">
      <Frame id="orcamento" title={`Potes de ${mesAno(mes)}`} real>
        {e4 ? <Bloqueio e={e4} />
          : !d ? (q.isError && !q.isFetching
            ? (
              <div className="empty" role="alert">
                <p>Não deu para carregar o orçamento.</p>
                <button type="button" className="btn btn-ghost retry" onClick={() => q.refetch()}>Tentar de novo</button>
              </div>
            )
            : <p role="status" className="faint">Carregando…</p>)
          : <Corpo key={mes} d={d} mes={mes} busy={q.isFetching} antigo={q.isRefetchError && (
            <div className="prev-antigo" role="status">
              <p>Não deu para atualizar. Mostrando o que carregou antes.</p>
              <button type="button" className="btn btn-quiet" onClick={() => q.refetch()}>Tentar de novo</button>
            </div>
          )} />}
      </Frame>
    </div>
  );
}
