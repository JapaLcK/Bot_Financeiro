// "Dia a dia" com backend: os `dias` de /gastos (ou de /gastos/categoria, com uma categoria
// escolhida). Só a escala de cor é calculada aqui (a média e o dia aberto moram em lib/gastos-dia.ts); o resto é o que o servidor mandou.
// Tocar num dia abre o card de compras do dia (GastosCompras) na própria /gastos; no Resumo, que não tem lugar para ele, leva à /gastos com o card aberto.
import { useContext } from "react";
import { useQuery } from "@tanstack/react-query";
import type { PointerEvent } from "react";
import { isoDay, longDate, money, moneyText } from "../lib/format.js";
import { useActions } from "../lib/actions";
import { diaAbrivel, mediaDoPeriodo } from "../lib/gastos-dia";
import { mesDe } from "../lib/store.js";
import { DEMO, gastosCategoriaQuery, gastosQuery } from "../lib/v2";
import type { DashState } from "../lib/types";
import { Frame, FrameLink } from "../parts/Frame";
import { estadoGastos } from "../parts/GastosEstado";
import { Selos } from "../parts/Selos";
import { hideTip, showTip } from "../parts/Tip";

const WEEK = ["D", "S", "T", "Q", "Q", "S", "S"];
const STEPS = 6;
const pad = (n: number) => String(n).padStart(2, "0");

export function GastosCalendario({ s }: { s: DashState }) {
  const { set, go } = useActions();
  const resumo = useContext(FrameLink) !== null;
  const mes = mesDe(s);
  const cat = s.filter.category;
  const geral = useQuery({ ...gastosQuery(mes), enabled: !cat && !DEMO });
  const daCat = useQuery({ ...gastosCategoriaQuery(mes, cat ?? ""), enabled: !!cat && !DEMO });
  const q = cat ? daCat : geral;
  const d = q.data;
  const nomeCat = daCat.data?.categoria.nome;
  const aside = cat && nomeCat && <span className="tag-demo">{nomeCat}</span>;
  const estado = estadoGastos(q);
  if (!d || estado) return <Frame id="calendario" title="Dia a dia" real aside={aside}>{estado}</Frame>;

  const [y, m] = d.mes.split("-").map(Number);
  const dias = new Date(y, m, 0).getDate();
  const { de, vividos, nDias, media } = mediaDoPeriodo(d);
  const porDia = new Map(d.dias.map((g) => [g.dia, g]));
  const totais = vividos.map((g) => Number(g.total));
  const passo = (t: number) => Math.ceil((totais.filter((v) => v <= t).length / totais.length) * STEPS); // percentil: 1 a 6
  const caro = vividos.reduce<typeof vividos[number] | null>((a, g) => (!a || Number(g.total) > Number(a.total) ? g : a), null);
  const cor = (chave: string) => (cat ? daCat.data?.categoria.cor : geral.data?.categorias.find((c) => c.chave === chave)?.cor);
  const aberto = s.gastosAberto && "dia" in s.gastosAberto ? s.gastosAberto.dia : null;
  // No Resumo só abre (e navega); na /gastos, tocar no dia aberto fecha, e o "Dia mais caro" nunca fecha.
  const abrir = (dia: string, soAbre = false) => {
    set({ gastosAberto: aberto === dia && !soAbre && !resumo ? null : { dia } });
    if (resumo) go("/gastos");
  };
  const fora = d.fora_da_grade;

  const cells = [
    ...Array.from({ length: new Date(y, m - 1, 1).getDay() }, (_, i) => <span key={`pad-${i}`} className="cal-pad" />),
    ...Array.from({ length: dias }, (_, i) => {
      const k = `${d.mes}-${pad(i + 1)}`;
      const date = isoDay(k);
      const g = porDia.get(k);
      const antes = k < de;
      const futuro = k > d.hoje;
      const off = !diaAbrivel(k, d);
      const step = !off && !futuro && g && Number(g.total) > 0 ? passo(Number(g.total)) : 0;
      const on = !resumo && aberto === k;
      // Tooltip e nome acessível leem a mesma lista: teclado e leitor de tela não têm hover.
      const rows = (g?.maiores ?? []).map((r) => ({ label: r.descricao, value: moneyText(r.valor) ?? "", color: cor(r.categoria) }));
      const valor = antes ? "fora do seu histórico" : g ? `${futuro ? "agendado · " : ""}${moneyText(g.total)}` : futuro ? "ainda não aconteceu" : "sem gasto";
      const tip = (e: PointerEvent) => showTip(e.clientX, e.clientY, { title: longDate(date), value: valor, rows });
      return (
        <button key={k} type="button" className="cal-day" data-step={step} data-future={(futuro && !antes) || undefined} data-fora={antes || undefined} data-agendado={(futuro && !!g) || undefined}
          data-today={k === d.hoje ? "true" : undefined} aria-expanded={resumo ? undefined : on} aria-controls={on ? "gastos-compras" : undefined} disabled={off}
          aria-label={`${longDate(date)}: ${valor}${rows.length ? `. Maiores: ${rows.map((r) => `${r.label} ${r.value}`).join(", ")}` : ""}`}
          onPointerMove={off ? undefined : tip} onPointerLeave={hideTip} onClick={() => abrir(k)}>
          <span>{i + 1}</span>
        </button>
      );
    }),
  ];

  return (
    <Frame id="calendario" title="Dia a dia" real aside={aside}>
      {media !== null && <p className="w-lede">{cat ? "Só esta categoria · " : ""}média <span className="num">{money(media)}</span>/dia · {nDias - vividos.length} {nDias - vividos.length === 1 ? "dia" : "dias"} sem gasto</p>}
      <Selos motivos={d.motivos} />
      <div className="cal" role="group" aria-label="Gasto por dia do mês">
        {WEEK.map((w, i) => <span key={i} className="cal-wd" aria-hidden="true">{w}</span>)}
        {cells}
      </div>
      {fora.quantidade > 0 && <p className="cal-note faint">{fora.quantidade} {fora.quantidade === 1 ? "saída" : "saídas"} com data fora do período mostrado: <span className="num">{moneyText(fora.valor)}</span></p>}
      <div className="cal-foot">
        <span className="ramp" aria-hidden="true">
          <span className="faint">menos</span>
          {Array.from({ length: STEPS }, (_, i) => <i key={i} data-step={i + 1} />)}
          <span className="faint">mais</span>
        </span>
        {caro && (
          <p className="cal-note">
            <span className="faint">Dia mais caro</span>{" "}
            <button type="button" className="link" onClick={() => abrir(caro.dia, true)}>{Number(caro.dia.slice(8))} · <span className="num">{moneyText(caro.total)}</span></button>
          </p>
        )}
      </div>
    </Frame>
  );
}
