import type { Categoria, Conta, Lancamento } from "../lib/api-v2.gen";
import { dayMonth, isoDay, moneyIn, monthTitle } from "../lib/format.js";
import { IconeCategoria } from "./IconeCategoria";
import { Selos } from "./Selos";

export const ORIGENS = { carteira: "Carteira Piggy", banco: "Banco", cartao: "Cartão", registro_antigo: "Registro antigo" };
export function LancamentoContexto({ item, contas = [] }: { item: Lancamento; contas?: Conta[] }) {
  const conta = contas.find((c) => c.id === item.conta_id);
  return <>
    {!item.guardado && <p className="lanc-meta">{ORIGENS[item.origem]}{item.instituicao && ` · ${item.instituicao}`}{conta?.nome && ` · ${conta.nome}`}</p>}
    {item.fundido && <p className="lanc-meta">Junto com o banco · o banco informa data e valor.</p>}
    {item.interno && <p className="lanc-meta">{item.guardado ? "Guardado" : "Movimento interno"} · fora de Entrou/Saiu</p>}
    {item.parcela && <p className="lanc-meta">Parcela {item.parcela.n} de {item.parcela.total}</p>}
    {item.fatura && <p className="lanc-meta">Fatura de {monthTitle(item.fatura)}</p>}
    {item.mensagem && <p className="lanc-nota">Nota: {item.mensagem}</p>}
    <Selos motivos={item.motivos} />
  </>;
}
// Linha limpa (ícone, título, subtítulo, valor): o contexto (origem, fatura, parcela, nota) mora no formulário de detalhe.
// `semData`: o Extrato já diz o dia no cabeçalho do grupo; `GastosCompras` mantém a data.
export function LancamentoLinha({ item, categorias, abrir, historico = false, semData = false }: { item: Lancamento; historico?: boolean; semData?: boolean; categorias: Categoria[]; abrir?: () => void }) {
  const cat = categorias.find((c) => c.chave === item.categoria);
  const bruto = cat?.nome ?? item.categoria ?? "Sem categoria", nome = bruto.charAt(0).toUpperCase() + bruto.slice(1); // sem par no catálogo: a chave, capitalizada (como o app)
  const ganho = !item.interno && item.tipo === "entrada";
  const sub = [semData ? null : `${dayMonth(isoDay(item.data))}${historico ? ` ${item.data.slice(0, 4)}` : ""}${item.hora ? ` · ${item.hora}` : ""}`, item.guardado ? null : item.instituicao ?? ORIGENS[item.origem], nome].filter(Boolean).join(" · ");
  const miolo = <>
    <b>{item.descricao ?? "Sem descrição"}</b>
    <span className="faint" id={`lanc-sub-${item.id}`}>{sub}</span>
  </>;
  return <li className="lanc-linha" data-id={item.id}>
    <div className="lanc-principal">
      <span className={`lanc-ico ${ganho ? "gain" : ""}`} aria-hidden="true">{item.tipo === "entrada" ? <i className="ph ph-arrow-down-left" /> : <IconeCategoria icone={cat?.icone} />}</span>
      {/* Sem `abrir` (o card de compras de /gastos) a linha só mostra, sem botão que não faz nada, e leva os selos de aviso (não há detalhe onde lê-los). */}
      {abrir ? <button type="button" className="lanc-abrir" onClick={abrir} aria-label={`Detalhes: ${item.descricao ?? "Sem descrição"}`} aria-describedby={`lanc-sub-${item.id}`}>{miolo}</button> : <div className="lanc-abrir">{miolo}<Selos motivos={item.motivos} /></div>}
      <span className={`lanc-valor num ${ganho ? "gain" : ""}`}>
        {(item.guardado || item.interno) && <span>{item.guardado ? "Guardado" : "Movimento"}</span>}{ganho ? "+" : item.tipo === "saida" ? "−" : ""}{moneyIn(Math.abs(Number(item.valor)), item.moeda)}
      </span>
    </div>
  </li>;
}
