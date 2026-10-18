// O topo do card "Seu dia" de /gastos: setas de dia, o total em destaque com a pílula contra a média, os fatos e a barra por
// categoria. Tudo vem do dia que o servidor mandou (`quantidade`, `maiores`, `categorias`); a única conta daqui é a comparação
// com a média do calendário (lib/gastos-dia.ts).
import type { ReactNode } from "react";
import { isoDay, longDate, money, moneyText } from "../lib/format.js";
import type { GastoCategoria, Gastos, GastosDaCategoria } from "../lib/api-v2.gen";
import { contraMedia, mediaDoPeriodo, vizinho } from "../lib/gastos-dia";
import { IconeCategoria } from "./IconeCategoria";
import { Selos } from "./Selos";

const Seta = ({ k, nome, icone, ir }: { k: string | null; nome: string; icone: string; ir: (k: string) => void }) => (
  <button type="button" className="dia-seta" disabled={!k} aria-label={k ? `${nome}, ${longDate(isoDay(k))}` : nome} onClick={() => k && ir(k)}>
    <i className={icone} aria-hidden="true" />
  </button>
);

export function GastosDiaTopo({ d, dia, catalogo, porCategoria, ir }: {
  d: Gastos | GastosDaCategoria;
  dia: string;
  catalogo: GastoCategoria[]; // vazio com categoria escolhida: o dia só tem uma
  porCategoria: boolean;
  ir: (k: string) => void;
}) {
  const entrada = d.dias.find((x) => x.dia === dia);
  const agendado = dia > d.hoje;
  const rotulo = agendado ? "Agendado para o dia" : "Gasto no dia";
  const total = entrada ? moneyText(entrada.total) ?? "—" : null;
  const { media } = mediaDoPeriodo(d);
  // Dia em curso e dia agendado ainda não fecharam, e dia de R$ 0,00 contra a média seria "100% abaixo": sem comparação.
  const cmp = entrada && dia < d.hoje && Number(entrada.total) > 0 ? contraMedia(Number(entrada.total), media) : null;
  const meta = (chave: string) => {
    const c = catalogo.find((x) => x.chave === chave);
    return { nome: c?.nome ?? chave, icone: c?.icone, cor: c?.cor ?? "var(--ink-3)" };
  };
  const fatias = (entrada?.categorias ?? []).filter((c) => Number(c.valor) > 0); // fatia ≤ 0 não desenha
  const maior = entrada?.maiores[0];
  const principal = porCategoria ? undefined : fatias[0];
  const fatos: Array<[string, ReactNode]> = [];
  if (entrada && entrada.quantidade > 0) fatos.push(["Compras", String(entrada.quantidade)]);
  if (maior) fatos.push(["Maior compra", moneyText(maior.valor) ?? "—"]);
  if (principal) fatos.push(["Mais em", <><IconeCategoria icone={meta(principal.chave).icone} /> {meta(principal.chave).nome}</>]);

  return (
    <>
      <div className="dia-nav">
        <Seta k={vizinho(dia, -1, d)} nome="Dia anterior" icone="ph ph-arrow-left" ir={ir} />
        <span className="dia-data" id="w-compras-data"><i className="ph ph-calendar-dots" aria-hidden="true" />{longDate(isoDay(dia))}</span>
        <Seta k={vizinho(dia, 1, d)} nome="Próximo dia" icone="ph ph-arrow-right" ir={ir} />
        <span className="sr-only" role="status">{longDate(isoDay(dia))}. {total ? `${rotulo}: ${total}` : "Nenhum gasto neste dia."}</span>
      </div>
      {entrada ? (
        <div className="dia-faixa">
          <p className="dia-rotulo">{rotulo}</p>
          <p className="hero-value dia-total">{total}</p>
          {media !== null && media > 0 && (
            <p className="dia-media">
              {cmp && (cmp.pct === 0
                ? <span className="dia-pilula">na média</span>
                : <span className="dia-pilula" data-tom={cmp.tom || undefined}><span aria-hidden="true">{cmp.pct > 0 ? "↑" : "↓"}</span> {Math.abs(cmp.pct)}% {cmp.pct > 0 ? "acima" : "abaixo"} da média</span>)}
              <span className="faint">média do mês: <span className="num">{money(media)}</span>/dia</span>
            </p>
          )}
        </div>
      ) : <p className="faint">Nenhum gasto neste dia.</p>}
      <Selos motivos={d.motivos} />
      {fatos.length > 0 && (
        <dl className="hero-facts dia-facts">
          {fatos.map(([t, v]) => <div key={t}><dt>{t}</dt><dd>{v}</dd></div>)}
        </dl>
      )}
      {!porCategoria && fatias.length > 1 && (
        <>
          <div className="comp-bar" aria-hidden="true">
            {fatias.map((f) => <span key={f.chave} style={{ flexGrow: Number(f.valor), background: meta(f.chave).cor }} />)}
          </div>
          <ul className="legend comp-legend dia-legend">
            {fatias.map((f) => (
              <li key={f.chave}><span className="key" style={{ background: meta(f.chave).cor, height: 8, width: 8, borderRadius: 2 }} /><span className="dia-leg-nome"><IconeCategoria icone={meta(f.chave).icone} /> {meta(f.chave).nome} · <b className="num">{moneyText(f.valor)}</b></span></li>
            ))}
          </ul>
        </>
      )}
    </>
  );
}
