// "Para onde vai" com backend (/api/v2/gastos): o que o servidor mandou. Dinheiro é texto
// (moneyText, com centavos, sem float); Number() só dá a proporção das barras e a variação.
import { useContext } from "react";
import { useQuery } from "@tanstack/react-query";
import type { GastoCategoria } from "../lib/api-v2.gen";
import { decimal, medida, moneyText, monthName } from "../lib/format.js";
import { useActions } from "../lib/actions";
import { mesDe } from "../lib/store.js";
import { gastosQuery } from "../lib/v2";
import type { DashState } from "../lib/types";
import { Frame, FrameLink } from "../parts/Frame";
import { AvisoMoeda, estadoGastos } from "../parts/GastosEstado";
import { IconeCategoria } from "../parts/IconeCategoria";
import { Selos } from "../parts/Selos";

// Texto decimal do servidor como número; malformado ou infinito = null (sem comparação, nunca "NaN%").
const num = (s: string | null) => (decimal(s) && Number.isFinite(Number(s)) ? Number(s) : null);
// Variação contra o anterior; null = sem base (o servidor não tem o mês anterior, o gasto de lá foi zero, um dos dois veio malformado ou a conta estoura o double).
const variacao = (atual: number | null, antes: number | null) => { const v = atual !== null && antes !== null && antes > 0 ? (atual - antes) / antes : NaN; return Number.isFinite(v) ? v : null; };
const pct = (v: number) => Math.round(Math.abs(v) * 100);

function delta(c: GastoCategoria, corte: number) {
  const [atual, antes] = [num(c.valor_ate_corte), num(c.anterior_ate_corte)];
  const v = variacao(atual, antes);
  if (v === null) {
    const fala = c.anterior_ate_corte === null ? "sem comparação: mês anterior fora do seu histórico"
      : atual !== null && antes !== null && antes <= 0 ? `sem gasto no mês anterior até o dia ${corte}` : "sem comparação";
    return { fala, tom: "faint", seta: null, texto: "—" };
  }
  if (Math.abs(v) < 0.005) return { fala: "igual ao mesmo dia do mês anterior", tom: "faint", seta: null, texto: "igual" };
  return { fala: `${pct(v)}% ${v > 0 ? "a mais" : "a menos"} que no mesmo dia do mês anterior`, tom: v > 0.15 ? "warn" : v < -0.05 ? "gain" : "faint", seta: v > 0 ? "ph-arrow-up" : "ph-arrow-down", texto: `${pct(v)}%` };
}

export function GastosCategorias({ s }: { s: DashState }) {
  const q = useQuery(gastosQuery(mesDe(s)));
  const { setFilter } = useActions();
  // No Resumo a lista rola por dentro (panels.css): o teclado tem de alcançá-la.
  const rola = useContext(FrameLink) !== null;
  const d = q.data;
  const active = s.filter.category;
  const estado = estadoGastos(q);
  const limpar = active && <button type="button" className="chip" aria-pressed="true" onClick={() => setFilter({ category: null })}>Limpar <i className="ph ph-x x" aria-hidden="true" /></button>;
  if (!d || estado) return <Frame id="categorias" title="Para onde vai" real aside={limpar}>{estado}</Frame>;

  const noMes = !!active && d.categorias.some((c) => c.chave === active); // categoria escolhida sem gasto neste mês: nada para atenuar
  const max = Math.max(...d.categorias.map((c) => Math.max(medida(c.valor), medida(c.anterior_ate_corte))), 1);
  const alvo = d.categorias.find((c) => c.chave !== active)?.chave; // a linha que o guia destaca (parts/Guia.tsx)
  // O total do mês contra o anterior até o mesmo dia; o servidor manda o total anterior (inclui categoria que só existe lá).
  const soma = d.categorias.reduce<number | null>((a, c) => { const n = num(c.valor_ate_corte); return a === null || n === null ? null : a + n; }, 0);
  const vt = variacao(soma, num(d.anterior_total_ate_corte));
  const [y, m] = d.mes.split("-").map(Number);
  const antes = monthName(new Date(y, m - 2, 1));
  const corrente = d.hoje.startsWith(d.mes);
  const frase = vt === null ? null : `${corrente ? `Até o dia ${d.dia_de_corte}` : "O mês inteiro"}: ${Math.abs(vt) < 0.005 ? `igual a ${antes}` : `${pct(vt)}% ${vt > 0 ? "a mais" : "a menos"} que em ${antes}`}${corrente ? " no mesmo dia" : ""}`;

  return (
    <Frame id="categorias" title="Para onde vai" real aside={limpar}>
      {d.categorias.length === 0 && !d.saidas_em_outra_moeda ? <p className="faint">Nenhum gasto neste mês.</p> : (
        <>
          <p className="w-lede"><span className="num">{moneyText(d.total) ?? "—"}</span> gastos no mês{frase && <span className="faint"> · {frase}</span>}</p>
          <AvisoMoeda n={d.saidas_em_outra_moeda} />
        </>
      )}
      <Selos motivos={d.motivos} />
      <ul className="cats" data-guia="categorias.lista" tabIndex={rola ? 0 : undefined} aria-label={rola ? "Categorias do mês" : undefined}>
        {d.categorias.map((c) => {
          const on = active === c.chave;
          const dl = delta(c, d.dia_de_corte);
          const antesN = medida(c.anterior_ate_corte);
          return (
            <li key={c.chave}>
              <button type="button" className="cat" aria-pressed={on} data-guia={c.chave === alvo ? "categorias.item" : undefined} data-dim={noMes && !on ? "true" : undefined}
                onClick={() => setFilter({ category: on ? null : c.chave })}
                aria-label={`${c.nome}: ${moneyText(c.valor)}, ${dl.fala}. Filtrar lançamentos.`}>
                <span className="cat-name"><IconeCategoria icone={c.icone} /><span className="cat-nome">{c.nome}</span></span>
                <span className="cat-val num">{moneyText(c.valor)}</span>
                <span className={`cat-delta num ${dl.tom}`}>{dl.seta && <i className={`ph ${dl.seta}`} aria-hidden="true" />}{dl.texto}</span>
                <span className="cat-track" aria-hidden="true">
                  <span className="cat-fill" style={{ background: c.cor, transform: `scaleX(${medida(c.valor) / max})` }} />
                  {antesN > 0 && <span className="cat-ghost" style={{ left: `${(antesN / max) * 100}%` }} />}
                </span>
              </button>
            </li>
          );
        })}
      </ul>
    </Frame>
  );
}
