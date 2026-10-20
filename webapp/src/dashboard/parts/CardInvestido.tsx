import { moneyText } from "../lib/format.js";
import type { Investido } from "../lib/api-v2.gen";
import { fraseSemTotal } from "./InvestidoResposta";
import { Selos } from "./Selos";

// A cor e o ícone de cada tipo (as chaves são as de db/investido.py::TIPOS). Nada de verde ou âmbar: são de ganho e de aviso.
const TIPO: Record<string, { icone: string; cor: string }> = {
  renda_fixa: { icone: "ph ph-bank", cor: "var(--serie-3)" },
  tesouro: { icone: "ph ph-scroll", cor: "var(--serie-1)" },
  acoes: { icone: "ph ph-chart-line-up", cor: "var(--serie-2)" },
  fii: { icone: "ph ph-buildings", cor: "var(--serie-4)" },
  fundos: { icone: "ph ph-chart-pie", cor: "#9085e9" },
  etf: { icone: "ph ph-chart-bar", cor: "#2fa0c8" },
  outros: { icone: "ph ph-coins", cor: "var(--ink-3)" },
};
// Centavos exatos (BigInt, nunca float). Só o texto de 2 casas que o servidor manda; o resto é "não sei".
const centavos = (v: string | null) => (v !== null && /^-?\d+\.\d{2}$/.test(v) ? BigInt(v.replace(".", "")) : null);
const texto = (c: bigint) => `${c < BigInt(0) ? "-" : ""}${(c < BigInt(0) ? -c : c) / BigInt(100)}.${String((c < BigInt(0) ? -c : c) % BigInt(100)).padStart(2, "0")}`;

/** O total, o desenho por tipo e a lista, a partir do que o /api/v2/investido devolveu. `foco`: só renda fixa + Tesouro, com a
 * MESMA soma do `total_renda_fixa` do servidor (core/services/agent_chat_data.py): sem total ou com parte sem saldo, "—". */
export function CardInvestido({ d, foco }: { d: Investido; foco?: "renda_fixa" }) {
  const partes = foco ? d.por_tipo.filter((p) => p.tipo === "renda_fixa" || p.tipo === "tesouro") : d.por_tipo;
  const cents = partes.map((p) => centavos(p.valor));
  const soma = cents.reduce<bigint>((s, c) => s + (c !== null && c > BigInt(0) ? c : BigInt(0)), BigInt(0));
  const total = foco
    ? (d.total === null || cents.some((c) => c === null) ? null : moneyText(texto(cents.reduce<bigint>((s, c) => s + c!, BigInt(0)))))
    : moneyText(d.total);
  const pct = (c: bigint | null) => (c !== null && c > BigInt(0) && soma > BigInt(0) ? Math.round(Number((c * BigInt(10000)) / soma) / 100) : null);
  const rot = (n: number) => (n === 0 ? "<1%" : `${n}%`); // positivo que arredonda a 0 não vira "0%"
  const barra = partes.flatMap((p, i) => (pct(cents[i]) === null ? [] : [{ p, c: cents[i]!, n: pct(cents[i])! }]));
  const selos = d.motivos.filter((m) => m !== "sem_banco_conectado");
  return (
    <section className="card-agente" aria-label={foco ? "Renda fixa" : "Investimentos"}>
      <p className="card-agente-tit"><b>{foco ? "Renda fixa" : "Investimentos"}</b> <span className="faint">nos bancos conectados</span></p>
      {d.total === null
        ? <p className="card-agente-vazio">{fraseSemTotal(d.motivos)}</p>
        : <p className="card-agente-total num">{total ?? "—"}</p>}
      {d.total !== null && !partes.length && <p className="card-agente-vazio">{foco ? "Nada em renda fixa nos bancos conectados." : fraseSemTotal(["nenhum_investimento"])}</p>}
      {barra.length > 0 && (
        <div className="comp-bar" role="img" aria-label={barra.map((b) => `${b.p.rotulo} ${rot(b.n)}`).join(", ")}>
          {barra.map((b) => <span key={b.p.tipo} style={{ flexGrow: Number(b.c), background: (TIPO[b.p.tipo] ?? TIPO.outros).cor }} />)}
        </div>
      )}
      {partes.length > 0 && (
        <ul className="card-agente-lista">
          {partes.map((p, i) => {
            const t = TIPO[p.tipo] ?? TIPO.outros, n = pct(cents[i]);
            return (
              <li key={p.tipo}>
                <i className={t.icone} style={{ color: t.cor }} aria-hidden="true" />
                <span className="card-agente-nome">{p.rotulo}</span>
                <span className="faint num">{n === null ? "" : rot(n)}</span>
                {p.valor === null ? <span className="faint">saldo não informado</span> : <b className="num">{moneyText(p.valor) ?? "—"}</b>}
              </li>
            );
          })}
        </ul>
      )}
      <Selos motivos={selos} />
    </section>
  );
}
