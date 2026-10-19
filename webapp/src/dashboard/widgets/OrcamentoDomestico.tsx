// A página /orcamento (método dos potes) sobre /api/v2/orcamento-domestico: o servidor faz a conta
// (tetos, gasto, restante, %), o cliente só mostra; Number() só dá a largura das barras.
// 4xx nunca mostra dado guardado; rede/5xx com dado mostra o antigo, com aviso.
import { useState, type ReactNode } from "react";
import { useIsMutating, useQuery } from "@tanstack/react-query";
import type { OrcamentoDomestico, Pote } from "../lib/api-v2.gen";
import { moneyText } from "../lib/format.js";
import { ehNegativo, mesAno } from "../lib/metas-textos";
import { DEMO, ErroApi, orcamentoQuery } from "../lib/v2";
import { mesDe } from "../lib/store.js";
import type { DashState } from "../lib/types";
import { Bloqueio } from "../parts/Bloqueio";
import { Frame } from "../parts/Frame";
import { GastoFixoForm } from "../parts/GastoFixoForm";
import { PoteLancamentos } from "../parts/PoteLancamentos";
import { CHAVE_ESCRITA, OrcamentoForm, mostra, useEscrita, type Modo } from "../parts/OrcamentoForm";

const pctTxt = (v: string | null) => v === null ? "—" : `${Math.round(Number(v))}%`;
const largura = (v: string | null) => v === null ? 0 : Math.min(Math.max(Number(v), 0), 100);
const COR = /^#[0-9a-f]{6}$/i;
const cor = (p: Pote) => COR.test(p.cor) ? p.cor : "var(--ink-3)";

// Donut da alocação (teto de cada pote sobre a soma dos tetos); a renda no centro. SVG puro: uma circunferência por fatia.
const R = 70, C = 2 * Math.PI * R;
function Donut({ d }: { d: OrcamentoDomestico }) {
  const total = d.potes.reduce((a, p) => a + Number(p.teto), 0);
  const fatias = total > 0 ? d.potes.filter((p) => Number(p.teto) > 0) : [];
  let acc = 0;
  const rotulo = total > 0
    ? `Alocação da renda: ${d.potes.map((p) => `${p.nome}: ${mostra(p.pct)}% (${moneyText(p.teto)})`).join("; ")}`
    : "Alocação da renda: sem renda neste mês";
  return (
    <div className="orc-donut">
      <svg viewBox="0 0 200 200" role="img" aria-label={rotulo}>
        <circle cx="100" cy="100" r={R} fill="none" stroke="var(--rule)" strokeWidth="24" />
        {fatias.map((p) => {
          const len = Number(p.teto) / total * C;
          const el = <circle key={p.chave} className="orc-fatia" cx="100" cy="100" r={R} fill="none" stroke={cor(p)} strokeWidth="24"
            strokeDasharray={`${Math.max(len - (fatias.length > 1 ? 2 : 0), 0.01)} ${C}`} strokeDashoffset={-acc} transform="rotate(-90 100 100)" />;
          acc += len;
          return el;
        })}
      </svg>
      <div className="orc-donut-centro" aria-hidden="true">
        <span className="orc-rotulo">Renda</span>
        <b className="num">{moneyText(d.renda.valor)}</b>
      </div>
    </div>
  );
}

function Corpo({ d, mes, busy, antigo }: { d: OrcamentoDomestico; mes: string; busy: boolean; antigo: ReactNode }) {
  const [aberto, setAberto] = useState<Modo | null>(null);
  const [poteAberto, setPoteAberto] = useState<string | null>(null);
  const [gastoFixo, setGastoFixo] = useState<"form" | "feito" | null>(null);
  const escrita = useEscrita(mes);
  const trava = useIsMutating({ mutationKey: CHAVE_ESCRITA }) > 0;
  const sem = d.total.usado_pct === null;
  const informada = d.renda.origem === "informada";
  const livreNeg = ehNegativo(d.total.restante_livre); // sem o excedente dos potes guardados: só estouro de verdade
  const doModal = d.potes.find((p) => p.chave === poteAberto);
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
            <button type="button" className="btn btn-ghost" disabled={trava} onClick={() => setGastoFixo("form")}>+ Gasto fixo</button>
          </div>
        )}
      </section>
      {gastoFixo === "feito" && <p role="status" className="orc-dica">Gasto fixo cadastrado. Ele entra na Previsão.</p>}
      {escrita.isError && <p role="alert" className="warn">{escrita.erro}</p>}
      {sem && <p className="orc-dica" role="status">Sem renda neste mês: informe a renda para dividir nos potes.</p>}
      <div className="orc-grade">
        <Donut d={d} />
        <ul className="orc-potes">
          {d.potes.map((p) => {
            const guarda = p.guardado;
            const neg = ehNegativo(p.restante);
            const passou = neg && !guarda;
            return (
              <li key={p.chave} className="orc-pote">
                  <div className="orc-pote-topo">
                    <span className="orc-ponto" style={COR.test(p.cor) ? { background: p.cor } : undefined} aria-hidden="true" />
                    <h3><button type="button" className="orc-pote-btn" aria-haspopup="dialog" onClick={() => setPoteAberto(p.chave)}>{p.nome}</button></h3>
                    <span className="num faint">{mostra(p.pct)}%</span>
                  </div>
                  <div className={`meter${passou ? " warn" : ""}`} aria-hidden="true"><span style={{ width: `${largura(p.usado_pct)}%` }} /></div>
                  <dl className="orc-valores">
                    <div><dt>Teto</dt><dd className="num">{moneyText(p.teto)}</dd></div>
                    <div><dt>{guarda ? "Guardado" : "Gasto"}</dt><dd className="num">{moneyText(p.gasto)}{p.usado_pct !== null && <span className="faint"> · {pctTxt(p.usado_pct)}</span>}</dd></div>
                    <div><dt>Restante</dt><dd className={`num${passou ? " warn" : ""}`}>
                      {passou ? <><i className="ph ph-warning" aria-hidden="true" /> passou {moneyText(p.restante.slice(1))} do teto</>
                        : neg ? `guardou ${moneyText(p.restante.slice(1))} a mais` : moneyText(p.restante)}
                    </dd></div>
                  </dl>
              </li>
            );
          })}
        </ul>
      </div>
      <dl className="orc-total orc-valores">
        <div><dt>Total dos tetos</dt><dd className="num">{moneyText(d.total.teto)}</dd></div>
        <div><dt>Gasto no mês</dt><dd className="num">{moneyText(d.total.gasto)}<span className="faint"> · {pctTxt(d.total.usado_pct)}</span></dd></div>
        <div><dt>Restante</dt><dd className={`num${livreNeg ? " warn" : ""}`}>{livreNeg ? `passou ${moneyText(d.total.restante_livre.slice(1))}` : moneyText(d.total.restante_livre)}</dd></div>
      </dl>
      {doModal && <PoteLancamentos p={doModal} mes={mes} guarda={doModal.guardado} fechar={() => setPoteAberto(null)} />}
      {gastoFixo === "form" && <GastoFixoForm feito={() => setGastoFixo("feito")} fechar={() => setGastoFixo((g) => g === "form" ? null : g)} />}
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
        {e4 ? <Bloqueio e={e4} convite="O Orçamento doméstico é do Plus e do Pro." o="o orçamento" />
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
