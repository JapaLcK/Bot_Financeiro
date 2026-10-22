import { useId, useState, type ReactNode } from "react";
import type { Previsao } from "../../lib/api-v2.gen";
import { isoDay, longDate, moneyText } from "../../lib/format.js";
import { Grupo, ddmm, saida, valorOc } from "../../widgets/Compromissos";
import { ESTADO, GASTOS_DIA_A_DIA, Grafico } from "../../widgets/Previsao";
import { useMais } from "./mais";

// Os cards `hero` ("Saldo previsto") e `compromissos` do chat: o `recursos` do bloco é `{ previsao: <GET /api/v2/previsao> }`. Desenhados da foto,
// sem consulta. O saldo, o gráfico e os marcos vêm do que o servidor mandou; o menor saldo e o gasto do dia a dia abrem aqui mesmo (nada vai a outra tela).
const obj = (x: unknown): x is Record<string, unknown> => !!x && typeof x === "object" && !Array.isArray(x);
const prev = (r: unknown) => (r as { previsao?: unknown } | null)?.previsao;
export const contratoHero = (r: unknown) => {
  const p = prev(r);
  return obj(p) && Array.isArray(p.marcos) && Array.isArray(p.capacidades) && obj(p.base) && typeof p.estado === "string" && Number.isInteger(p.dias) && typeof p.hoje === "string";
};
export const contratoCompromissos = (r: unknown) => {
  const p = prev(r);
  return obj(p) && (p.compromissos === null || Array.isArray(p.compromissos)) && Number.isInteger(p.dias) && typeof p.hoje === "string";
};
export const diasDoBloco = (r: unknown) => (prev(r) as Previsao).dias;

// Uma linha de dado que, com `filhos`, abre aqui dentro (botão de 44px, `aria-expanded`); sem eles é só leitura.
function Dado({ titulo, valor, aviso, filhos }: { titulo: string; valor: string; aviso?: boolean; filhos?: ReactNode }) {
  const [aberta, setAberta] = useState(false), id = useId();
  const miolo = <><span className="ci-nome">{titulo}</span><span className={`ci-saldo num${aviso ? " warn" : ""}`}>{valor}</span></>;
  return (
    <li className="ci-conta">
      {filhos
        ? <button type="button" className="ci-linha ci-dado" aria-expanded={aberta} aria-controls={aberta ? id : undefined} onClick={() => setAberta(!aberta)}>{miolo}<i className="ph ph-caret-right ci-seta" aria-hidden="true" /></button>
        : <div className="ci-linha ci-dado">{miolo}<span /></div>}
      {aberta && <div id={id} className="ci-aberta">{filhos}</div>}
    </li>
  );
}
const Sub = ({ nome, valor }: { nome: string; valor: string }) => <li className="ci-sub"><span>{nome}</span><span className="num">{valor}</span></li>;

export function CardHero({ d }: { d: Previsao }) {
  const marco = d.marcos.find((m) => m.dias === d.dias) ?? d.marcos[d.marcos.length - 1];
  const indisponivel = d.estado === "indisponivel", valor = marco && !indisponivel ? moneyText(marco.saldo) : null;
  const pior = d.pior_dia, piorTxt = pior && moneyText(pior.saldo), est = d.estimativa_variavel, mensal = est && saida(est.despesa_mensal);
  const causas = pior ? pior.causas.length > 0 || pior.despesa_variavel !== null : false;
  return (
    <>
      <div className="ci-total">
        <span className={`stat-value num${valor ? "" : " ci-na"}`}>{valor ?? "Indisponível"}</span>
        <p className="ci-leg">{marco && <>em {longDate(isoDay(marco.data))} · </>}{ESTADO[d.estado] ?? d.estado}</p>
      </div>
      {d.base.bancos_excluidos && <p className="ci-aviso">Bancos conectados fora do cálculo</p>}
      {!indisponivel && d.capacidades.includes("trajetoria") && <Grafico d={d} />}
      {d.marcos.length > 1 && <ul className="marcos">{d.marcos.filter((m) => m !== marco).map((m) => <li key={m.dias}>Em {m.dias} dias <b className="num">{moneyText(m.saldo) ?? "indisponível"}</b></li>)}</ul>}
      <ul className="ci-lista">
        {est && <Dado titulo={GASTOS_DIA_A_DIA} valor={mensal ? `${mensal}/mês` : "indisponível"}
          filhos={est.categorias.length > 0 && <ul className="ci-subs">{est.categorias.map((c) => <Sub key={c.chave} nome={c.nome} valor={saida(c.despesa_mensal) ?? "indisponível"} />)}</ul>} />}
        {pior && d.capacidades.includes("pior_dia") && <Dado titulo={`Menor saldo · ${ddmm(pior.data)}`} valor={piorTxt ?? "indisponível"} aviso={!!piorTxt?.startsWith("−")}
          filhos={causas && <>{pior.desde && <p className="ci-cap">desde {ddmm(pior.desde)}</p>}<ul className="ci-subs">
            {pior.causas.map((o) => <Sub key={o.chave + o.ciclo} nome={o.nome} valor={valorOc(o) ?? "valor desconhecido"} />)}
            {pior.despesa_variavel !== null && <Sub nome={GASTOS_DIA_A_DIA} valor={saida(pior.despesa_variavel) ?? "valor desconhecido"} />}
          </ul></>} />}
      </ul>
    </>
  );
}

export function CardCompromissos({ d }: { d: Previsao }) {
  const grupos = d.compromissos ?? [];
  const [vistos, mais] = useMais(grupos, 5, ["compromisso", "compromissos"]);
  if (d.compromissos === null) return <p className="ci-vazio">Indisponível no seu plano.</p>;
  if (!grupos.length) return <p className="ci-vazio">Nenhum compromisso conhecido nos próximos {d.dias} dias.</p>;
  return <><ol className="bills">{vistos.map((g) => <Grupo key={g.chave} g={g} hoje={d.hoje} />)}</ol>{mais}</>;
}
