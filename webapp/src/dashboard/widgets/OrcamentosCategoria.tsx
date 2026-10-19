// A página /orcamentos-categoria sobre /api/v2/orcamentos-categoria: o servidor faz a conta (gasto, restante, %, status),
// o cliente só mostra; Number() só dá a largura das barras. 4xx nunca mostra dado guardado; rede/5xx com dado mostra o antigo, com aviso.
import { useState, type ReactNode } from "react";
import { useIsMutating, useQuery } from "@tanstack/react-query";
import type { Orcamento, OrcamentosCategoria } from "../lib/api-v2.gen";
import { moneyText } from "../lib/format.js";
import { ehNegativo, mesAno } from "../lib/metas-textos";
import { DEMO, ErroApi, orcamentosCategoriaQuery } from "../lib/v2";
import { Frame } from "../parts/Frame";
import { IconeCategoria } from "../parts/IconeCategoria";
import { CHAVE_ESCRITA, OrcamentoCategoriaForm } from "../parts/OrcamentoCategoriaForm";
import { Bloqueio } from "../parts/Bloqueio";

const pctTxt = (v: string) => `${Math.round(Number(v))}%`;
const largura = (v: string) => Math.min(Math.max(Number(v), 0), 100);
const COR = /^#[0-9a-f]{6}$/i;
const sem = (v: string) => moneyText(v.slice(1)); // "-20.00" -> "R$ 20,00"

function Linha({ o }: { o: Orcamento }) {
  const passou = o.status === "vermelho";
  return <>
    <span className="orc-icone" style={COR.test(o.cor) ? { color: o.cor } : undefined}><IconeCategoria icone={o.icone} /></span>
    <span className="orc-linha-corpo">
      <span className="orc-linha-topo"><b>{o.categoria}</b><span className="num faint">{moneyText(o.gasto)} de {moneyText(o.teto)}</span></span>
      <span className={`meter${passou ? " warn" : ""}`} aria-hidden="true"><span style={{ width: `${largura(o.usado_pct)}%` }} /></span>
      <span className={passou ? "warn" : "faint"}>
        {passou ? <><i className="ph ph-warning" aria-hidden="true" /> {ehNegativo(o.restante) ? `passou ${sem(o.restante)}` : "chegou ao teto"}</>
          : o.status === "amarelo" ? `${pctTxt(o.usado_pct)} · perto do teto` : `${pctTxt(o.usado_pct)} · ${moneyText(o.restante)} restantes`}
      </span>
    </span>
  </>;
}

function Corpo({ d, busy, antigo }: { d: OrcamentosCategoria; busy: boolean; antigo: ReactNode }) {
  const [aberto, setAberto] = useState<Orcamento | "novo" | null>(null);
  const trava = useIsMutating({ mutationKey: CHAVE_ESCRITA }) > 0;
  const noLimite = d.limite !== null && d.orcamentos.length >= d.limite;
  const t = d.total;
  const passou = ehNegativo(t.restante);
  return (
    <div className="orc-corpo" aria-busy={busy}>
      {antigo}
      {d.orcamentos.length > 0 && (
        <dl className="orc-total orc-valores">
          <div><dt>Orçado</dt><dd className="num">{moneyText(t.teto)}</dd></div>
          <div><dt>Gasto no mês</dt><dd className="num">{moneyText(t.gasto)}<span className="faint"> · {pctTxt(t.usado_pct)}</span></dd></div>
          <div><dt>Disponível</dt><dd className={`num${passou ? " warn" : ""}`}>{passou ? `passou ${sem(t.restante)}` : moneyText(t.restante)}</dd></div>
          <div><dt>Em risco</dt><dd className="num">{t.em_risco}<span className="faint"> · categorias com 80% ou mais</span></dd></div>
        </dl>
      )}
      {d.limite !== null && <p className="orc-dica" role="status">{d.orcamentos.length} de {d.limite} orçamentos no seu plano</p>}
      {!DEMO && d.orcamentos.length > 0 && (
        <div className="orc-acoes">
          <button type="button" className="btn btn-primary" disabled={trava || noLimite} onClick={() => setAberto("novo")}>Novo orçamento</button>
          {noLimite && <a className="btn btn-ghost" href="/precos">Ver planos</a>}
        </div>
      )}
      {d.orcamentos.length === 0 ? (
        <div className="empty">
          <p><b>Nenhum orçamento ainda</b></p>
          <p>Crie um teto para uma categoria e acompanhe o mês.</p>
          {!DEMO && <button type="button" className="btn btn-primary" disabled={trava} onClick={() => setAberto("novo")}>Criar orçamento</button>}
        </div>
      ) : (
        <ul className="orc-lista">
          {d.orcamentos.map((o) => (
            <li key={o.categoria}>
              {DEMO ? <div className="orc-linha"><Linha o={o} /></div>
                : <button type="button" className="orc-linha" disabled={trava} aria-label={`Editar o orçamento de ${o.categoria}`} onClick={() => setAberto(o)}><Linha o={o} /></button>}
            </li>
          ))}
        </ul>
      )}
      {aberto && <OrcamentoCategoriaForm key={aberto === "novo" ? "novo" : aberto.categoria} orcamento={aberto === "novo" ? undefined : aberto}
        orcamentos={d.orcamentos} limite={d.limite} fechar={() => setAberto(null)} />}
    </div>
  );
}

export function OrcamentosCategoriaPainel() {
  const q = useQuery(orcamentosCategoriaQuery);
  const e = q.error instanceof ErroApi ? q.error : null;
  const e4 = e && e.status >= 400 && e.status < 500 ? e : null;
  const d = !e4 ? q.data : undefined;
  return (
    <div className="panel span-12">
      <Frame id="orcamentos-categoria" title={d ? `Orçamentos de ${mesAno(d.mes)}` : "Orçamentos do mês"} real>
        {e4 ? <Bloqueio e={e4} convite="Os Orçamentos por categoria não estão no seu plano." o="os orçamentos por categoria" />
          : !d ? (q.isError && !q.isFetching
            ? (
              <div className="empty" role="alert">
                <p>Não deu para carregar os orçamentos.</p>
                <button type="button" className="btn btn-ghost retry" onClick={() => q.refetch()}>Tentar de novo</button>
              </div>
            )
            : <p role="status" className="faint">Carregando…</p>)
          : <Corpo d={d} busy={q.isFetching} antigo={q.isRefetchError && (
            <div className="prev-antigo" role="status">
              <p>Não deu para atualizar. Mostrando o que carregou antes.</p>
              <button type="button" className="btn btn-quiet" onClick={() => q.refetch()}>Tentar de novo</button>
            </div>
          )} />}
      </Frame>
    </div>
  );
}
