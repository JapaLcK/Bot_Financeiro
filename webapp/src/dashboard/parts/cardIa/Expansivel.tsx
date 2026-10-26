import { useId, useState, type ReactNode } from "react";
import { medida, moneyText } from "../../lib/format.js";

// As peças do card que abre o detalhe AQUI DENTRO (categorias, dias, período): uma linha que é botão de 44px (`aria-expanded`), a barra de composição e a
// lista de categorias. Quem usa guarda UMA linha aberta por vez (como o app). Sem `corpo` a linha é só leitura, sem botão que não faz nada.
export function Expansivel({ classe, miolo, corpo, aberta, alternar, regiao }: { classe: string; miolo: ReactNode; corpo?: ReactNode; aberta: boolean; alternar: () => void; regiao: string }) {
  return (
    <li className="ci-conta">
      {corpo
        ? <button type="button" className={`ci-linha ${classe}`} aria-expanded={aberta} aria-controls={aberta ? regiao : undefined} onClick={alternar}>{miolo}<i className="ph ph-caret-right ci-seta" aria-hidden="true" /></button>
        : <div className={`ci-linha ${classe}`}>{miolo}<span /></div>}
      {aberta && corpo && <div id={regiao} className="ci-aberta">{corpo}</div>}
    </li>
  );
}

export type ItemCat = { nome: string; valor: string; pct: number; cor: string; corpo?: ReactNode };
// "30%"; "<1%" quando o inteiro arredonda a 0 mas há valor (o rotuloPercentual do app).
const rotulo = (pct: number, valor: string) => `${pct === 0 && Number(valor) > 0 ? "<1" : pct}%`;

/** A barra de composição (cada fatia na cor da bolinha da sua linha). Decorativa: o valor de cada fatia está na linha. */
export const Composicao = ({ itens }: { itens: ItemCat[] }) => (
  <div className="comp-bar" aria-hidden="true">{itens.map((c, i) => <span key={i} style={{ flexGrow: medida(c.valor), background: c.cor }} />)}</div>
);

/** Uma linha por categoria: bolinha da cor, nome, %, valor e a seta de quem abre. */
export function LinhasCat({ itens }: { itens: readonly ItemCat[] }) {
  const [aberta, setAberta] = useState<number | null>(null), base = useId();
  return (
    <ul className="ci-lista">
      {itens.map((c, i) => (
        <Expansivel key={i} classe="ci-cat" regiao={`${base}-${i}`} aberta={aberta === i} alternar={() => setAberta(aberta === i ? null : i)} corpo={c.corpo}
          miolo={<><span className="ci-bolinha" style={{ background: c.cor }} aria-hidden="true" /><span className="ci-nome">{c.nome}</span><span className="ci-pct num">{rotulo(c.pct, c.valor)}</span><span className="ci-saldo num">{moneyText(c.valor) ?? "—"}</span></>} />
      ))}
    </ul>
  );
}
