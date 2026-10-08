// Compromissos da /api/v2/previsao, agrupados como a API manda (nunca fundidos por nome).
// A tela não soma dinheiro: grupo repetido mostra "3 × −R$ 25,00" ou "valores diferentes".
import { useId, useState } from "react";
import type { GrupoCompromissos, OcorrenciaPrevisao } from "../lib/api-v2.gen";
import { isoDay, moneyText, weekday } from "../lib/format.js";
import { Frame } from "../parts/Frame";
import { MotivosPrevisao } from "../parts/Selos";

const FONTE: Record<GrupoCompromissos["fonte"], string> = {
  receita_recorrente: "Receita fixa", gasto_recorrente: "Gasto fixo", instancia: "Boleto", fatura: "Fatura do cartão",
  recorrencia_banco: "Detectado no banco",
};
export const ddmm = (iso: string) => `${iso.slice(8, 10)}/${iso.slice(5, 7)}`;
const plural = (n: number, um: string, varios: string) => `${n} ${n === 1 ? um : varios}`;

// "−R$ 2,675" / "+R$ 10,00", ou null se o valor não veio (ou não é decimal).
// Zero sai "R$ 0,00" sem sinal, como o moneyText: nunca "−R$ 0,00" nem "+R$ 0,00".
export function valorOc(o: OcorrenciaPrevisao) {
  const t = moneyText(o.valor);
  return t && (t === "R$ 0,00" ? t : `${o.direcao === "entrada" ? "+" : "−"}${t}`);
}

function resumo(oc: OcorrenciaPrevisao[]) {
  const valores = oc.map(valorOc);
  if (valores.some((v) => v === null)) return "valor desconhecido";
  if (valores.some((v) => v !== valores[0])) return "valores diferentes";
  return oc.length === 1 ? valores[0] : `${oc.length} × ${valores[0]}`;
}

// Convite ao Pro no lugar do detalhe que o plano não traz: sem número nem gráfico atrás.
export const ConvitePro = ({ texto }: { texto: string }) => (
  <div className="convite">
    <p>{texto}</p>
    <a className="btn btn-ghost retry" href="/precos">Ver planos</a>
  </div>
);

function Ocorrencia({ o, hoje }: { o: OcorrenciaPrevisao; hoje: string }) {
  const venceu = o.data !== null && o.data < hoje;
  const selos = [
    o.data === null ? "sem data" : venceu ? `venceu ${ddmm(o.data)}` : null,
    o.qualidade_valor === "estimado" ? "estimado" : null,
    o.qualidade_data === "presumida" ? "data presumida" : null,
    o.realizacao === "a_conferir" ? "a conferir" : "previsto",
    o.incluida_no_calculo ? null : "fora do cálculo",
  ].filter(Boolean);
  return (
    <li className="ocorrencia">
      <span className="num">{o.data === null ? "sem data" : ddmm(o.data)}</span>
      <span className={`num bill-amt ${o.direcao === "entrada" ? "gain" : ""}`}>{valorOc(o) ?? "valor desconhecido"}</span>
      <p className="selos">{selos.flatMap((s, i) => [i ? " " : null, <span key={s} className="selo">{s}</span>])}</p>
      <MotivosPrevisao motivos={o.motivos} />
    </li>
  );
}

function Grupo({ g, hoje }: { g: GrupoCompromissos; hoje: string }) {
  const [aberto, setAberto] = useState(false);
  const id = useId();
  const oc = g.ocorrencias;
  const fora = oc.filter((o) => !o.incluida_no_calculo).length;
  const venceu = oc.some((o) => o.data !== null && o.data < hoje);
  const datas = g.primeira_data === null ? "sem data"
    : g.primeira_data === g.ultima_data ? ddmm(g.primeira_data) : `${ddmm(g.primeira_data)} a ${ddmm(g.ultima_data!)}`;
  const entrada = oc.every((o) => o.direcao === "entrada");
  return (
    <li>
      <button type="button" className="bill grupo" aria-expanded={aberto} aria-controls={aberto ? id : undefined} onClick={() => setAberto(!aberto)}>
        <span className="bill-date">
          {g.primeira_data ? <><b>{Number(g.primeira_data.slice(8, 10))}</b><span>{weekday(isoDay(g.primeira_data))}</span></> : <b>—</b>}
        </span>
        <span className="bill-name">
          {g.nome}
          <span className={`bill-status ${venceu ? "warn" : "faint"}`}>
            <i className="ph ph-caret-right" aria-hidden="true" />
            {[FONTE[g.fonte] ?? g.fonte, plural(oc.length, "ocorrência", "ocorrências"), datas, venceu && "venceu", fora && `${fora} fora do cálculo`].filter(Boolean).join(" · ")}
          </span>
        </span>
        <span className={`bill-amt num ${entrada ? "gain" : ""}`}>{resumo(oc)}</span>
      </button>
      {aberto && <ul id={id} className="ocorrencias">{oc.map((o) => <Ocorrencia key={o.chave} o={o} hoje={hoje} />)}</ul>}
    </li>
  );
}

export function Compromissos({ grupos, dias, hoje }: { grupos: GrupoCompromissos[] | null; dias: number; hoje: string }) {
  return (
    <Frame id="previsao-compromissos" title="Compromissos" real>
      {grupos === null ? <ConvitePro texto="A lista de contas e receitas de cada dia é do Pro." />
        : !grupos.length ? <p className="w-lede">Nenhum compromisso conhecido nos próximos {dias} dias.</p>
        : <ol className="bills">{grupos.map((g) => <Grupo key={g.chave} g={g} hoje={hoje} />)}</ol>}
    </Frame>
  );
}
