import type { Patrimonio } from "../../lib/api-v2.gen";
import { moneyText } from "../../lib/format.js";
import { textos } from "../../lib/bancos";
import { BarraComposicao, Grafico, PARTES, motivosDaTela, pctDasPartes, pontosDe, variacaoPatrimonio } from "../../widgets/Patrimonio";
import { Selos } from "../Selos";

// Os cards `patrimonio` ("Patrimônio") e `wealth` ("Onde está o dinheiro") do chat: o `recursos` do bloco é `{ patrimonio: <GET /api/v2/patrimonio> }`.
// Desenhados da foto: o total, a variação e a linha são os do servidor; a tabela por dia do gráfico segue só para leitor de tela.
const obj = (x: unknown): x is Record<string, unknown> => !!x && typeof x === "object" && !Array.isArray(x);
export const contratoPatrimonio = (r: unknown) => {
  const d = (r as { patrimonio?: Patrimonio } | null)?.patrimonio;
  return obj(d) && moneyText(d.total) !== null && obj(d.partes) && Array.isArray(d.historico) && Array.isArray(d.trechos) && textos(d.motivos);
};

export function CardPatrimonio({ d }: { d: Patrimonio }) {
  const pts = pontosDe(d);
  return (
    <>
      <div className="ci-total">
        <span className="stat-value num">{moneyText(d.total) ?? "—"}</span>
        {variacaoPatrimonio(d, "ci-leg")}
      </div>
      {pts.length >= 2 && <Grafico pts={pts} d={d} />}
      <Selos motivos={motivosDaTela(d.motivos, ...d.historico.map((h) => h.motivos))} />
    </>
  );
}

export function CardWealth({ d }: { d: Patrimonio }) {
  const pct = pctDasPartes(d);
  return (
    <>
      <BarraComposicao d={d} />
      <ul className="ci-lista">
        {PARTES.map((p) => (
          <li key={p.key} className="ci-conta">
            <div className="ci-linha ci-parte">
              <span className="ci-bolinha" style={{ background: p.tone }} aria-hidden="true" />
              <span className="ci-nome">{p.label}</span>
              <span className="ci-pct num">{pct(d.partes[p.key])}</span>
              <span className="ci-saldo num">{moneyText(d.partes[p.key]) ?? "—"}</span>
            </div>
          </li>
        ))}
      </ul>
      <Selos motivos={motivosDaTela(d.motivos)} />
    </>
  );
}
