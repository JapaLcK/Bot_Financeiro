import type { Assinaturas } from "../../lib/api-v2.gen";
import { moneyText } from "../../lib/format.js";
import { contratoAssinaturas } from "../CardAssinaturas";
import { useMais } from "./mais";

// O card `assinaturas` do chat: o `recursos` do bloco é `{ assinaturas: <GET /api/v2/assinaturas> }`. Só leitura (marcar e ignorar são na página): o total
// por mês (a soma exata das ativas, do servidor) e as ativas, da maior para a menor, como o servidor manda.
export const contratoAssinaturasBloco = (r: unknown) => contratoAssinaturas((r as { assinaturas?: unknown } | null)?.assinaturas);

export function CardAssinaturasIa({ d }: { d: Assinaturas }) {
  const ativas = d.servicos.filter((a) => a.status === "ativa");
  const [vistas, mais] = useMais(ativas, 5, ["assinatura", "assinaturas"]);
  if (!ativas.length) return <p className="ci-vazio">Nenhuma assinatura ativa encontrada nos bancos conectados.</p>;
  return (
    <>
      <div className="ci-total">
        <span className="stat-value num">{moneyText(d.total_mensal)}</span>
        <p className="ci-leg">por mês</p>
      </div>
      <ul className="ci-lista">
        {vistas.map((a) => (
          <li key={`${a.chave}-${a.nome}`} className="ci-conta">
            <div className="ci-linha ci-leitura"><span className="ci-nome">{a.nome}</span><span className="ci-saldo num">{moneyText(a.valor)}</span></div>
          </li>
        ))}
      </ul>
      {mais}
    </>
  );
}
