// Patrimônio e "Onde está o dinheiro" sobre a /api/v2/patrimonio (par do app, features/painel/patrimonio.tsx).
// Os dois blocos leem a mesma chave. A linha QUEBRA entre trechos (a regra é do servidor: `trechos`);
// o dinheiro mostrado sai do texto da API (`moneyText`), o número só posiciona o ponto.
import { useState, type PointerEvent } from "react";
import { useQuery, type UseQueryResult } from "@tanstack/react-query";
import type { Patrimonio as Dado } from "../lib/api-v2.gen";
import { decimal, medida, moneyText } from "../lib/format.js";
import { patrimonioQuery } from "../lib/v2";
import { useSize } from "../useDash";
import { Frame } from "../parts/Frame";
import { Selos } from "../parts/Selos";
import { hideTip, showTip } from "../parts/Tip";

// Decisão do dono: a tela do patrimônio não mostra `carteira_nao_confirmada` (a API mantém, o chat lê a mesma função).
export const motivosDaTela = (...listas: readonly (readonly string[])[]) =>
  Array.from(new Set(listas.reduce((a: string[], l) => a.concat(l), []))).filter((m) => m !== "carteira_nao_confirmada");

const dm = (iso: string) => { const [, m, d] = iso.split("-"); return `${d}/${m}`; };
const dia = (iso: string) => { const [y, m, d] = iso.split("-").map(Number); return Date.UTC(y, m - 1, d) / 864e5; };
const positivo = (s: string) => (decimal(s)?.n ?? BigInt(0)) > BigInt(0);

type Ponto = { dia: string; total: string; partes: Dado["partes"]; trecho: number };
// Fotos antes de hoje + o ponto ao vivo; cada ponto no trecho que o contém.
export function pontosDe(d: Dado): Ponto[] {
  if (!d.trechos.length) return []; // o servidor promete trechos não vazio; se faltar, vira "sem histórico" em vez de derrubar a página
  const hoje = d.trechos[d.trechos.length - 1].ate;
  const base = [...d.historico.filter((h) => h.dia < hoje).map((h) => ({ dia: h.dia, total: h.total, partes: h.partes })), { dia: hoje, total: d.total, partes: d.partes }];
  return base.map((p) => ({ ...p, trecho: Math.max(0, d.trechos.filter((t) => t.desde <= p.dia).length - 1) }));
}

function semDado(q: UseQueryResult<Dado>, id: string, title: string, erro: string) {
  return (
    <Frame id={id} title={title} real>
      {q.isPending ? <p role="status" className="faint">Carregando…</p> : (
        <div className="empty" role="alert">
          <p>{erro}</p>
          <button type="button" className="btn retry btn-ghost" onClick={() => q.refetch()}>Tentar de novo</button>
        </div>
      )}
    </Frame>
  );
}

// Componente à parte: o `useSize` observa o div no 1º render, e ele só existe com dado.
export function Grafico({ pts, d }: { pts: Ponto[]; d: Dado }) {
  const [box, { width, height }] = useSize<HTMLDivElement>();
  const [hover, setHover] = useState<number | null>(null);
  const total = moneyText(d.total) ?? "—";
  const pad = 6, base = height - 16;
  const nums = pts.map((p) => Number(p.total) || 0);
  const lo = Math.min(...nums), span = Math.max(...nums) - lo || 1;
  const d0 = dia(pts[0].dia), dN = dia(pts[pts.length - 1].dia);
  const x = (i: number) => pad + ((dia(pts[i].dia) - d0) / (dN - d0 || 1)) * (width - pad * 2);
  const y = (i: number) => pad + (1 - (nums[i] - lo) / span) * (base - pad * 2);
  const trechos = d.trechos.map((_, t) => pts.map((p, i) => ({ p, i })).filter(({ p }) => p.trecho === t)).filter((g) => g.length);
  // `M` no começo de cada trecho: a linha nunca liga o último ponto de um ao primeiro do seguinte.
  const path = trechos.map((g) => g.map(({ i }, k) => `${k ? "L" : "M"}${x(i).toFixed(1)},${y(i).toFixed(1)}`).join("")).join("");

  const onMove = (e: PointerEvent) => {
    const px = e.clientX - box.current!.getBoundingClientRect().left;
    const i = pts.reduce((b, _, k) => (Math.abs(x(k) - px) < Math.abs(x(b) - px) ? k : b), 0);
    setHover(i);
    showTip(e.clientX, e.clientY, { title: dm(pts[i].dia), value: moneyText(pts[i].total) ?? "—", rows: PARTES.map((p) => ({ label: p.label, value: moneyText(pts[i].partes[p.key]) ?? "—" })) });
  };

  return (
    <div className="nw-chart" ref={box} onPointerMove={onMove} onPointerLeave={() => { setHover(null); hideTip(); }}>
      {width > 0 && (
        <svg width={width} height={height} role="img"
          aria-label={`Patrimônio de ${dm(pts[0].dia)} a ${dm(pts[pts.length - 1].dia)}: de ${moneyText(pts[0].total)} para ${total}.`}>
          <path d={path} className="line real" />
          {trechos.filter((g) => g.length === 1).map((g) => <circle key={g[0].i} cx={x(g[0].i)} cy={y(g[0].i)} r={3} className="cross-dot" />)}
          {hover != null && <line x1={x(hover)} x2={x(hover)} y1={pad} y2={base} className="cross-line" />}
          {hover != null && <circle cx={x(hover)} cy={y(hover)} r={4.5} className="cross-dot" />}
          <circle cx={x(pts.length - 1)} cy={y(pts.length - 1)} r={4} className="today-dot" />
          <text x={x(0)} y={height - 4} className="axis" textAnchor="start">{dm(pts[0].dia)}</text>
          <text x={x(pts.length - 1)} y={height - 4} className="axis" textAnchor="end">{dm(pts[pts.length - 1].dia)}</text>
        </svg>
      )}
      <div className="sr-only"><table>
        <caption>Patrimônio por dia</caption>
        <thead><tr><th scope="col">Dia</th>{PARTES.map((q) => <th key={q.key} scope="col">{q.label}</th>)}<th scope="col">Total</th></tr></thead>
        <tbody>{pts.map((p) => <tr key={p.dia}><th scope="row">{dm(p.dia)}</th>{PARTES.map((q) => <td key={q.key}>{moneyText(p.partes[q.key]) ?? "—"}</td>)}<td>{moneyText(p.total) ?? "—"}</td></tr>)}</tbody>
      </table></div>
    </div>
  );
}

// "+R$ 120,00 desde 03/10" (verde só se positivo), ou undefined; igual ao app: sem trechos, sem variação. O card da IA reusa.
export function variacaoPatrimonio(d: Dado, className = "w-aside-num") {
  const v = d.trechos.length ? d.variacao : null;
  const vt = v && moneyText(v.valor);
  return vt && v ? <span className={`${className} num${positivo(v.valor) ? " gain" : ""}`}>{positivo(v.valor) ? "+" : ""}{vt} desde {dm(v.desde)}</span> : undefined;
}

export function PatrimonioReal({ title = "Patrimônio" }: { title?: string }) {
  const q = useQuery(patrimonioQuery);
  const d = q.data;
  if (!d) return semDado(q, "patrimonio", title, "Não deu para carregar o patrimônio.");
  const pts = pontosDe(d);
  return (
    <Frame id="patrimonio" title={title} className="w-nw" real aside={variacaoPatrimonio(d)}>
      <div className="nw-top"><span className="stat-value num">{moneyText(d.total) ?? "—"}</span></div>
      {pts.length < 2 ? <p className="faint">Ainda sem histórico: a linha aparece a partir do 2º dia.</p> : <Grafico pts={pts} d={d} />}
      <Selos motivos={motivosDaTela(d.motivos, ...d.historico.map((h) => h.motivos))} />
    </Frame>
  );
}

// Fonte única dos rótulos (iguais aos do app) e das cores: paleta categórica, sem verde (só ganho) e sem rosa.
export const PARTES = [
  { key: "carteira", label: "Carteira", tone: "#3987e5" },
  { key: "bancos", label: "Contas bancárias", tone: "#2fa0c8" },
  { key: "investimentos_banco", label: "Investimentos do banco", tone: "#9085e9" },
  { key: "caixinhas", label: "Caixinhas", tone: "#b98ae0" },
  { key: "investimentos_manuais", label: "Investimentos manuais", tone: "#7a8aa8" },
] as const;

// `medida`: texto malformado, zero ou negativo fica fora da barra (nunca NaN); o valor da lista é sempre o texto.
const vivas = (d: Dado) => PARTES.filter((p) => medida(d.partes[p.key]) > 0);
export const pctDasPartes = (d: Dado) => {
  const soma = PARTES.reduce((a, p) => a + medida(d.partes[p.key]), 0);
  return (s: string) => (soma > 0 && medida(s) > 0 ? `${Math.round((medida(s) / soma) * 100)}%` : "");
};
// A barra de "Onde está o dinheiro" (o card da IA reusa); sem parte positiva, nada.
export function BarraComposicao({ d }: { d: Dado }) {
  const pct = pctDasPartes(d);
  return vivas(d).length === 0 ? null : (
    <div className="comp-bar" role="img" aria-label={vivas(d).map((p) => `${p.label} ${pct(d.partes[p.key])}`).join(", ")}>
      {vivas(d).map((p) => <span key={p.key} style={{ flexGrow: medida(d.partes[p.key]), background: p.tone }} />)}
    </div>
  );
}

export function ComposicaoReal() {
  const q = useQuery(patrimonioQuery);
  const d = q.data;
  if (!d) return semDado(q, "composicao", "Onde está o dinheiro", "Não deu para carregar a composição.");
  const pct = pctDasPartes(d);
  return (
    <Frame id="composicao" title="Onde está o dinheiro" real>
      <BarraComposicao d={d} />
      {vivas(d).length > 0 && (
        <ul className="legend comp-legend">
          {vivas(d).map((p) => (
            <li key={p.key}><span className="key" style={{ background: p.tone, height: 8, width: 8, borderRadius: 2 }} />{p.label} <b className="num">{pct(d.partes[p.key])}</b></li>
          ))}
        </ul>
      )}
      <ul className="wealth-rows">
        {PARTES.map((p) => (
          <li key={p.key}>
            <span className="tip-key" style={{ background: p.tone, width: 8, height: 8 }} aria-hidden="true" />
            <span className="wealth-name">{p.label}</span>
            <span className="faint num">{pct(d.partes[p.key])}</span>
            <b className="num">{moneyText(d.partes[p.key]) ?? "—"}</b>
          </li>
        ))}
      </ul>
      <Selos motivos={motivosDaTela(d.motivos)} />
    </Frame>
  );
}
