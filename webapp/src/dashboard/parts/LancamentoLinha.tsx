import type { Categoria, Conta, Lancamento } from "../lib/api-v2.gen";
import { dayMonth, isoDay, moneyIn, monthTitle } from "../lib/format.js";
import { Selos } from "./Selos";

export const ORIGENS = { carteira: "Carteira Piggy", banco: "Banco", cartao: "Cartão", registro_antigo: "Registro antigo" };
export function LancamentoContexto({ item, contas = [] }: { item: Lancamento; contas?: Conta[] }) {
  const conta = contas.find((c) => c.id === item.conta_id);
  return <>
    <p className="lanc-meta">{ORIGENS[item.origem]}{item.instituicao && ` · ${item.instituicao}`}{conta?.nome && ` · ${conta.nome}`}</p>
    {item.fundido && <p className="lanc-meta">Junto com o banco · o banco informa data e valor.</p>}
    {item.interno && <p className="lanc-meta">Movimento interno · fora de Entrou/Saiu</p>}
    {item.parcela && <p className="lanc-meta">Parcela {item.parcela.n} de {item.parcela.total}</p>}
    {item.fatura && <p className="lanc-meta">Fatura de {monthTitle(item.fatura)}</p>}
    {item.mensagem && <p className="lanc-nota">Nota: {item.mensagem}</p>}
    <Selos motivos={item.motivos} />
  </>;
}
export function LancamentoLinha({ item, categorias, contas, abrir, historico = false }: { item: Lancamento; historico?: boolean; categorias: Categoria[]; contas: Conta[]; abrir: () => void }) {
  const nome = categorias.find((c) => c.chave === item.categoria)?.nome ?? item.categoria ?? "Sem categoria";
  return <li className="lanc-linha" data-id={item.id}>
    <div className="lanc-principal">
      <button type="button" className="lanc-abrir" onClick={abrir} aria-label={`Detalhes: ${item.descricao ?? "Sem descrição"}`}>
        <b>{item.descricao ?? "Sem descrição"}</b>
        <span className="faint">{dayMonth(isoDay(item.data))}{historico && ` ${item.data.slice(0, 4)}`}{item.hora && ` · ${item.hora}`} · {nome}</span>
      </button>
      <span className={`lanc-valor num ${!item.interno && item.tipo === "entrada" ? "gain" : ""}`}>
        <span>{item.interno ? "Movimento" : item.tipo === "entrada" ? "Entrada" : "Saída"}</span>{moneyIn(Number(item.valor), item.moeda)}
      </span>
    </div>
    <LancamentoContexto item={item} contas={contas} />
  </li>;
}
