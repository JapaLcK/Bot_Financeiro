// A página /milhao: quanto falta para o próximo milhão e quando o patrimônio chega lá, no ritmo de hoje.
// O servidor só diz o patrimônio de hoje (texto); a conta de juros compostos é do cliente (lib/milhao.ts).
// 4xx nunca mostra dado guardado; rede/5xx com dado mostra o antigo, com aviso (molde do Orçamento doméstico).
import { useState, type ReactNode } from "react";
import { useQuery } from "@tanstack/react-query";
import type { ProgressoMilhao } from "../lib/api-v2.gen";
import { money0, moneyText } from "../lib/format.js";
import { duracao, projetar } from "../lib/milhao";
import { mesAno } from "../lib/metas-textos";
import { ErroApi, mesAtual, milhaoQuery } from "../lib/v2";
import { Bloqueio } from "../parts/Bloqueio";
import { Frame } from "../parts/Frame";
import { Selos } from "../parts/Selos";
import { href } from "../router";
import { MilhaoGrafico } from "./MilhaoGrafico";

const APORTE = { min: 0, max: 10000, passo: 50, padrao: 0 }; // ponytail: teto declarado de R$ 10 mil por mês
const TAXA = { min: 0, max: 15, passo: 0.5, padrao: 10 };
const pctDe = (v: number, c: { min: number; max: number }) => `${((v - c.min) / (c.max - c.min)) * 100}%`;
const milhoes = new Intl.NumberFormat("pt-BR");
const virgula = (n: number) => String(n).replace(".", ",");

function Corpo({ d, antigo, busy }: { d: ProgressoMilhao; antigo: ReactNode; busy: boolean }) {
  const [aporte, setAporte] = useState(APORTE.padrao);
  const [taxa, setTaxa] = useState(TAXA.padrao);
  const inicio = mesAtual();
  const p = projetar({ patrimonio: Number(d.patrimonio), aporte, taxaAnual: taxa, inicio });
  const passou = p.meta > 1e6;
  const nos = aporte === APORTE.padrao && taxa === TAXA.padrao;
  const alvo = passou ? `R$ ${milhoes.format(p.meta / 1e6)} milhões` : "R$ 1 milhão";
  const resumo = p.chegada
    ? `Patrimônio projetado de ${money0(p.pontos[0])} hoje até ${alvo} em ${mesAno(p.chegada)}.`
    : `Patrimônio projetado de ${money0(p.pontos[0])} hoje: não chega a ${alvo} em 50 anos.`;
  return (
    <div className="mi-corpo" aria-busy={busy}>
      {antigo}
      <section className="mi-topo" aria-label="Quanto falta">
        <h3 className="mi-titulo">{passou ? "Você já passou do milhão" : `Faltam ${money0(Math.ceil(p.faltaCentavos / 100))}`}</h3>
        <p className="faint">{passou ? `Próximo marco: ${alvo}. Faltam ${money0(Math.ceil(p.faltaCentavos / 100))}.` : "para o seu primeiro milhão"}</p>
        <div className="meter" aria-hidden="true"><span style={{ width: `${p.pct}%` }} /></div>
        <p className="mi-hoje">Hoje <b className="num">{moneyText(d.patrimonio)}</b> · {p.pct}%</p>
        <Selos motivos={d.motivos} />
      </section>
      <section className="mi-quando" aria-label="Quando você chega" aria-live="polite">
        {p.chegada ? (
          <><h3 className="mi-titulo">{passou ? `Você chega aos ${alvo} em` : "Você chega em"} {mesAno(p.chegada)}</h3><p className="faint">Daqui a {duracao(p.anos, p.mesesResto)}</p></>
        ) : (
          <><h3 className="mi-titulo">Sem previsão</h3><p className="faint">Arraste o Guardar por mês para ver quando você chega.</p></>
        )}
      </section>
      <div className="mi-controles">
        <label className="mi-ctl">
          <span>Guardar por mês <b className="num">{money0(aporte)}</b></span>
          <input className="range" type="range" min={APORTE.min} max={APORTE.max} step={APORTE.passo} value={aporte}
            style={{ ["--p" as string]: pctDe(aporte, APORTE) }} aria-valuetext={`${money0(aporte)} por mês`}
            onChange={(e) => setAporte(Number(e.target.value))} />
        </label>
        <label className="mi-ctl">
          <span>Rendimento ao ano <b className="num">{virgula(taxa)}%</b></span>
          <input className="range" type="range" min={TAXA.min} max={TAXA.max} step={TAXA.passo} value={taxa}
            style={{ ["--p" as string]: pctDe(taxa, TAXA) }} aria-valuetext={`${virgula(taxa)}% ao ano`}
            onChange={(e) => setTaxa(Number(e.target.value))} />
        </label>
        <button type="button" className="btn btn-quiet" disabled={nos} onClick={() => { setAporte(APORTE.padrao); setTaxa(TAXA.padrao); }}>Voltar ao padrão</button>
      </div>
      <MilhaoGrafico pontos={p.pontos} meta={p.meta} inicio={inicio} resumo={resumo} />
      <p className="faint mi-rodape">Conta de juros compostos com taxa fixa: não é promessa de rendimento. Patrimônio = Carteira, contas, caixinhas e investimentos.</p>
    </div>
  );
}

const SEM_BANCO = (
  <div className="convite">
    <p>Conecte um banco para ver quanto falta para o seu primeiro milhão.</p>
    <a className="btn btn-ghost retry" href={href("/conta/bancos")}>Conectar banco</a>
  </div>
);

export function MilhaoPainel() {
  const q = useQuery(milhaoQuery);
  const e = q.error instanceof ErroApi ? q.error : null;
  const e4 = e && e.status >= 400 && e.status < 500 ? e : null;
  const d = !e4 ? q.data : undefined;
  const antigo = q.isRefetchError && (
    <div className="prev-antigo" role="status">
      <p>Não deu para atualizar. Mostrando o que carregou antes.</p>
      <button type="button" className="btn btn-quiet" onClick={() => q.refetch()}>Tentar de novo</button>
    </div>
  );
  return (
    <div className="panel span-12">
      <Frame id="milhao" title={Number(d?.patrimonio) >= 1e6 ? "Rumo ao próximo milhão" : "Rumo ao primeiro milhão"} real>
        {e4 ? <Bloqueio e={e4} convite="O Progresso ao milhão é do Plus e do Pro." o="o progresso ao milhão" />
          : !d ? (q.isError && !q.isFetching
            ? (
              <div className="empty" role="alert">
                <p>Não deu para carregar o progresso.</p>
                <button type="button" className="btn btn-ghost retry" onClick={() => q.refetch()}>Tentar de novo</button>
              </div>
            )
            : <p role="status" className="faint">Carregando…</p>)
          : d.motivos.includes("sem_banco_conectado") ? <>{antigo}{SEM_BANCO}</>
          : <Corpo d={d} busy={q.isFetching} antigo={antigo} />}
      </Frame>
    </div>
  );
}
