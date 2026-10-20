import type { Assinaturas } from "../lib/api-v2.gen";
import { moneyText } from "../lib/format.js";
import { href } from "../router";

const VISIVEIS = 3;
// Corpo fora do contrato não desenha meio número.
export const contratoAssinaturas = (d: unknown) => {
  const x = d as Assinaturas | null;
  return !!x && typeof x.total_mensal === "string" && moneyText(x.total_mensal) !== null && Array.isArray(x.servicos)
    && x.servicos.every((a) => a && typeof a.nome === "string" && typeof a.status === "string" && moneyText(a.valor) !== null);
};

/** O total mensal e as maiores assinaturas ativas (`servicos` já vem do maior valor; o `total_mensal` do servidor soma exatamente as ativas). */
export function CardAssinaturas({ d }: { d: Assinaturas }) {
  const ativas = d.servicos.filter((a) => a.status === "ativa");
  return (
    <section className="card-agente" aria-label="Assinaturas">
      <p className="card-agente-tit"><b>Assinaturas</b> <span className="faint">por mês</span></p>
      {ativas.length === 0
        ? <p className="card-agente-vazio">Nenhuma assinatura ativa encontrada nos bancos conectados.</p>
        : <p className="card-agente-total num">{moneyText(d.total_mensal)}</p>}
      {ativas.length > 0 && (
        <ul className="card-agente-lista card-agente-lista-2">
          {ativas.slice(0, VISIVEIS).map((a) => (
            <li key={`${a.chave}-${a.nome}`}><span className="card-agente-nome">{a.nome}</span><b className="num">{moneyText(a.valor)}</b></li>
          ))}
        </ul>
      )}
      <a className="link msg-open" href={href("/assinaturas")}>{ativas.length > VISIVEIS ? `Ver as ${ativas.length} assinaturas` : "Ver assinaturas"}<i className="ph ph-arrow-right" aria-hidden="true" /></a>
    </section>
  );
}
