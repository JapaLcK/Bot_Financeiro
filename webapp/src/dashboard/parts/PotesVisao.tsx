// "Visão geral do mês" do /orcamento: donut + legenda, em Gasto (o gasto de cada pote) ou Orçado (o teto).
// O servidor faz as contas; aqui o Number() só reparte o anel e a legenda.
import { useState } from "react";
import { useMedia } from "../useMedia";
import type { OrcamentoDomestico } from "../lib/api-v2.gen";
import { moneyText as dinheiro } from "../lib/format.js";
import { visualDoPote } from "../lib/potes";
import { Frame } from "./Frame";
import { Seg } from "./Seg";
import { mostra } from "./OrcamentoForm";

type Visao = "gasto" | "orcado";
const OPCOES: { value: Visao; label: string }[] = [{ value: "gasto", label: "Gasto" }, { value: "orcado", label: "Orçado" }];
const R = 84, C = 2 * Math.PI * R;
const moneyText = (v: string) => dinheiro(v) ?? "—";
const rotuloPct = (v: number, total: number) => total > 0 ? `${Math.round(v / total * 100)}%` : "0%";
// O texto do centro é <text> do SVG: escala com o viewBox em qualquer navegador (sem container query) e cabe no
// diâmetro interno do anel (raio 74 de 100) por maior que seja o valor. Cada linha encolhe até a largura que
// sobra na altura dela (corda do círculo, com folga) a 0,68em por caractere; o bbox é o que o teste mede.
const INTERNO = 72, MAX_VALOR = 19;
function centro(linhas: [string, number][]) {
  const alturas = linhas.map(([, max]) => max * 1.2);
  let y = 100 - (alturas.reduce((a, h) => a + h, 0) + 3 * (linhas.length - 1)) / 2;
  return linhas.map(([txt, max], i) => {
    const yc = y + alturas[i] / 2;
    y += alturas[i] + 3;
    const meia = Math.sqrt(Math.max(INTERNO ** 2 - (Math.abs(yc - 100) + alturas[i] / 2) ** 2, 0));
    return { txt, y: yc, tam: Math.min(max, 2 * meia / (txt.length * 0.68)) };
  });
}

export function PotesVisao({ d }: { d: OrcamentoDomestico }) {
  const [visao, setVisao] = useState<Visao>("gasto");
  const pequeno = useMedia("(max-width: 760px)"); // o donut encolhe de 200 para 132px: as linhas pequenas crescem em unidades do SVG
  const valor = (p: OrcamentoDomestico["potes"][number]) => Number(visao === "gasto" ? p.gasto : p.teto);
  const total = d.potes.reduce((a, p) => a + Math.max(valor(p), 0), 0);
  const fatias = total > 0 ? d.potes.filter((p) => valor(p) > 0) : [];
  const distribuida = mostra(d.potes.reduce((a, p) => a + Number(p.pct), 0).toFixed(2));
  const [principal, rotulo, extra] = visao === "gasto"
    ? [moneyText(d.total.gasto), "gasto no mês", d.total.usado_pct === null ? "—" : `${Math.round(Number(d.total.usado_pct))}% da renda`]
    : [moneyText(d.renda.valor), "renda do mês", `${distribuida}% distribuída`];
  const aria = `${visao === "gasto" ? "Gasto" : "Orçado"} por pote, ${principal} ${rotulo}: ${d.potes.map((p) => `${p.nome} ${rotuloPct(Math.max(valor(p), 0), total)} (${moneyText(visao === "gasto" ? p.gasto : p.teto)})`).join("; ")}`;
  const sub = pequeno ? 15 : 12;
  const linhas = centro([[principal, MAX_VALOR], [rotulo, sub], [extra, sub]]);
  let acc = 0;
  return (
    <div className="panel span-7">
      <Frame id="orcamento-visao" title="Visão geral do mês" className="orc-w" real aside={<Seg label="Visão do donut" options={OPCOES} value={visao} onChange={setVisao} />}>
        <div className="orc-visao">
          <div className="orc-donut">
            <svg viewBox="0 0 200 200" role="img" aria-label={aria}>
              <circle cx="100" cy="100" r={R} fill="none" stroke="var(--plane-3)" strokeWidth="20" />
              {fatias.map((p) => {
                const len = valor(p) / total * C;
                const el = <circle key={p.chave} className="orc-fatia" cx="100" cy="100" r={R} fill="none" stroke={visualDoPote(p).cor} strokeWidth="20"
                  strokeDasharray={`${Math.max(len - (fatias.length > 1 ? 2.5 : 0), 0.01)} ${C}`} strokeDashoffset={-acc} transform="rotate(-90 100 100)" />;
                acc += len;
                return el;
              })}
              <g className="orc-donut-centro" aria-hidden="true" textAnchor="middle" dominantBaseline="central">
                {linhas.map((l, i) => <text key={i} x="100" y={l.y.toFixed(1)} fontSize={l.tam.toFixed(2)} className={i === 1 ? undefined : "num"}>{l.txt}</text>)}
              </g>
            </svg>
          </div>
          <ul className="orc-leg">
            {d.potes.map((p) => (
              <li key={p.chave}>
                <i className="orc-ponto" style={{ background: visualDoPote(p).cor }} aria-hidden="true" />
                <span className="orc-leg-nome">{p.nome}</span>
                <span className="num faint">{rotuloPct(Math.max(valor(p), 0), total)}</span>
                <span className="num orc-leg-valor">{moneyText(visao === "gasto" ? p.gasto : p.teto)}</span>
              </li>
            ))}
          </ul>
        </div>
      </Frame>
    </div>
  );
}
