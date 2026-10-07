// Previsão real (/api/v2/previsao): o que a API mandou, sem recalcular. Âncora = hoje, a
// série começa amanhã; dinheiro é o texto da API (moneyText) e Number() só desenha o SVG.
// 4xx nunca mostra dado guardado; rede/5xx com dado mostra o antigo, com aviso.
import { useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import type { Previsao } from "../lib/api-v2.gen";
import { dayMonth, isoDay, longDate, moneyText } from "../lib/format.js";
import type { Point } from "../lib/types";
import { ErroApi, previsaoQuery } from "../lib/v2";
import { CRIAR_SENHA } from "../parts/Entrada";
import { Frame } from "../parts/Frame";
import { Seg } from "../parts/Seg";
import { MotivosPrevisao, Selos } from "../parts/Selos";
import { Compromissos, ConvitePro, ddmm, valorOc } from "./Compromissos";
import { TrajectoryChart } from "./TrajectoryChart";

type Dias = 30 | 60 | 90;
const ESTADO: Record<Previsao["estado"], string> = { condicional: "Previsão condicional", a_conferir: "A conferir", indisponivel: "Indisponível" };
const hora = (d: Previsao) => d.calculado_em.slice(11, 16); // hora do servidor, no fuso do app

function usePrevisao() {
  const [dias, setDias] = useState<Dias | null>(null);
  const [aviso, setAviso] = useState("");
  const q = useQuery(previsaoQuery(dias));
  // Os horizontes da última resposta boa: o Seg não some enquanto o novo carrega.
  const permitidos = useRef<number[]>([]);
  const e4 = q.error instanceof ErroApi && q.error.status >= 400 && q.error.status < 500 ? q.error : null;
  // Horizonte fora do plano (ex.: downgrade com 90 dias): volta ao padrão, que recarrega.
  if (e4?.code === "forecast_horizon_not_allowed" && dias !== null) {
    setDias(null);
    setAviso("Esse horizonte não está no seu plano.");
  }
  // A guarda do horizonte: a resposta de outro pedido nunca passa pela deste.
  const d = !e4 && q.data && (dias === null || q.data.dias === dias) ? q.data : undefined;
  if (e4) permitidos.current = [];
  if (d) permitidos.current = d.dias_permitidos;
  return { q, d, e4, dias, aviso, permitidos: permitidos.current, escolher: (n: Dias) => { setAviso(""); setDias(n); } };
}
type Estado = ReturnType<typeof usePrevisao>;

function Bloqueio({ e }: { e: ErroApi }) {
  if (e.code === "pro_required") return <ConvitePro texto="A previsão é do Plus e do Pro." />;
  if (e.code === "password_required") {
    return (
      <div className="empty" role="alert">
        <p>Crie sua senha para ver a previsão. Depois de criar, volte para o painel novo.</p>
        <a className="btn btn-ghost retry" href={CRIAR_SENHA.href}>{CRIAR_SENHA.texto}</a>
      </div>
    );
  }
  return (
    <div className="empty" role="alert">
      <p>{e.status === 401 ? "Sua sessão terminou." : "Não deu para abrir a previsão."}</p>
      <button type="button" className="btn btn-ghost retry" onClick={() => location.reload()}>Recarregar</button>
    </div>
  );
}

function Grafico({ d }: { d: Previsao }) {
  const serie = d.ancora ? [{ ...d.ancora, compromissos: [] }, ...(d.trajetoria ?? [])] : [];
  const pts: Point[] = serie.map((p) => ({
    date: isoDay(p.data), value: Number(p.saldo), real: false, events: [], texto: moneyText(p.saldo) ?? undefined,
    rows: p.compromissos.map((o) => ({
      label: o.incluida_no_calculo ? o.nome : `${o.nome} (fora do cálculo)`,
      value: valorOc(o) ?? "valor desconhecido", color: o.direcao === "entrada" ? "var(--gain)" : "var(--ink-3)",
    })),
  }));
  if (!pts.length || pts.some((p) => p.texto === undefined)) {
    return <p className="w-lede">Sem saldo de partida, não dá para desenhar o saldo dia a dia.</p>;
  }
  return (
    <>
      <ul className="legend" aria-label="Legenda do gráfico">
        <li><span className="key forecast" />Previsão</li>
        {d.periodo && <li className="faint">de {dayMonth(isoDay(d.periodo.inicio))} a {dayMonth(isoDay(d.periodo.fim))}</li>}
      </ul>
      <TrajectoryChart traj={{ points: pts, end: pts[pts.length - 1], start: pts[0].value }} simOn={false} highlight={null} drawKey={`previsao-${d.dias}`} />
    </>
  );
}

function Conteudo({ d }: { d: Previsao }) {
  const marco = d.marcos.find((m) => m.dias === d.dias) ?? d.marcos[d.marcos.length - 1];
  const valor = marco && moneyText(marco.saldo);
  const pior = d.pior_dia;
  const piorTxt = pior && moneyText(pior.saldo);
  const negativo = !!piorTxt?.startsWith("−");
  return (
    <>
      <div className="hero-figure">
        <span className={`hero-value ${valor ? "" : "hero-na"}`}>{valor ?? "indisponível"}</span>
        <p className="hero-sub">{marco && <>em {longDate(isoDay(marco.data))} </>}<span className="faint">· {ESTADO[d.estado] ?? d.estado}</span></p>
      </div>
      <dl className="hero-facts prev-facts">
        <div><dt>Saldo de partida</dt><dd>{moneyText(d.base.saldo) ?? "indisponível"}</dd></div>
        {d.ancora && <div><dt>Hoje, com as premissas</dt><dd>{moneyText(d.ancora.saldo) ?? "indisponível"}</dd></div>}
        {d.capacidades.includes("pior_dia") && (
          <div><dt>Menor saldo</dt><dd className={negativo ? "warn" : ""}>{pior ? `${piorTxt ?? "indisponível"} em ${ddmm(pior.data)}` : "indisponível"}</dd></div>
        )}
      </dl>
      {d.marcos.length > 1 && (
        <ul className="marcos">{d.marcos.filter((m) => m !== marco).map((m) => <li key={m.dias}>Em {m.dias} dias <b className="num">{moneyText(m.saldo) ?? "indisponível"}</b></li>)}</ul>
      )}
      {pior && (
        <p className={`w-lede ${negativo ? "warn" : ""}`}>
          {negativo ? `Fica negativo em ${ddmm(pior.data)}` : `Menor saldo em ${ddmm(pior.data)}`}
          {pior.desde && pior.causas.length > 0 && <>, desde {ddmm(pior.desde)}: {pior.causas.map((c) => `${c.nome} ${valorOc(c) ?? "valor desconhecido"}`).join(", ")}</>}
        </p>
      )}
      {d.capacidades.includes("trajetoria") ? <Grafico d={d} /> : <ConvitePro texto="O saldo dia a dia, o menor saldo e os compromissos de cada dia são do Pro." />}
    </>
  );
}

export function PrevisaoHero({ p, id }: { p: Estado; id: string }) {
  const { q, d, e4, dias, aviso } = p;
  const opcoes = d?.dias_permitidos ?? p.permitidos;
  const aside = e4 ? null : opcoes.length > 1
    ? <Seg label="Horizonte da previsão" value={String(dias ?? d?.dias ?? opcoes[0])} onChange={(v) => p.escolher(Number(v) as Dias)}
        options={opcoes.map((n) => ({ value: String(n), label: `${n} dias` }))} />
    : opcoes.length === 1 ? <span className="w-lede">{opcoes[0]} dias</span> : null;
  return (
    <Frame id={id} title="Saldo previsto" className="w-hero" real aside={aside}>
      {aviso && <p role="status" className="w-lede">{aviso}</p>}
      {e4 ? <Bloqueio e={e4} />
        : !d ? (q.isError && !q.isFetching
          ? (
            <div className="empty" role="alert">
              <p>Não deu para carregar a previsão.</p>
              <button type="button" className="btn btn-ghost retry" onClick={() => q.refetch()}>Tentar de novo</button>
            </div>
          )
          : <p role="status" className="faint">Carregando a previsão…</p>)
        : (
          <div className="prev-corpo" aria-busy={q.isFetching}>
            {q.isRefetchError && (
              <div className="prev-antigo" role="status">
                <p>Mostrando a previsão calculada às {hora(d)}. Não deu para atualizar.</p>
                <button type="button" className="btn btn-quiet" onClick={() => q.refetch()}>Tentar de novo</button>
              </div>
            )}
            <Conteudo d={d} />
            <p className="faint prev-hora" aria-live="polite">{q.isFetching ? "Atualizando…" : `Calculada às ${hora(d)}`}</p>
          </div>
        )}
    </Frame>
  );
}

// O card "Saldo previsto" do Resumo: o mesmo bloco da página.
export const PrevisaoCard = () => <PrevisaoHero p={usePrevisao()} id="hero" />;

function ComoFoiFeita({ d }: { d: Previsao }) {
  const b = d.base;
  const origem = b.origem === "manual" ? "Carteira"
    : b.origem === "consolidated" ? `Carteira + ${b.contas_bancarias} ${b.contas_bancarias === 1 ? "conta" : "contas"} do Open Finance`
    : "sem saldo de partida";
  return (
    <Frame id="previsao-qualidade" title="Como esta previsão foi feita" real>
      <div className="prev-base">
        <p><span className="selo">{ESTADO[d.estado] ?? d.estado}</span></p>
        <p className="w-lede">Saldo de partida: {origem}{b.bancos_excluidos && " · bancos conectados fora do cálculo"}</p>
        <Selos motivos={b.motivos} />
      </div>
      {d.motivos.length > 0 && <><h3 className="prev-h">Por que é condicional</h3><MotivosPrevisao motivos={d.motivos} /></>}
      <h3 className="prev-h">Premissas</h3>
      <ul className="premissas">{d.premissas.map((t) => <li key={t}>{t}</li>)}</ul>
      {d.cobertura.janela_conferencia_inicio && <p className="w-lede">Conferência desde {ddmm(d.cobertura.janela_conferencia_inicio)}</p>}
    </Frame>
  );
}

// A página /previsao com backend. Sem resposta boa (carregando, erro, plano), um painel só.
export function PrevisaoPainel() {
  const p = usePrevisao();
  const d = p.d;
  return (
    <>
      <div className="panel span-12"><PrevisaoHero p={p} id="previsao" /></div>
      {d && (
        <>
          <div className="panel span-7"><Compromissos grupos={d.compromissos} dias={d.dias} hoje={d.hoje} /></div>
          <div className="panel span-5"><ComoFoiFeita d={d} /></div>
        </>
      )}
    </>
  );
}
