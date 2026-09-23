import { useState } from "react";
import { BANK_CDB } from "../lib/api";
import { money } from "../lib/format.js";
import { Frame } from "../parts/Frame";
import { PARTS } from "./Wealth";

// O banco manda cada caixinha como CDBs de mesmo nome e emissor, sem dizer qual
// posição é de qual caixinha: mostramos um bloco só, com as posições na ordem de chegada.
const SHOWN = 4;

export function BankCdb() {
  const [all, setAll] = useState(false);
  const { bank, via, name, positions } = BANK_CDB;
  const total = positions.reduce((a, b) => a + b, 0);
  const rest = positions.length - SHOWN;
  const tone = PARTS[2].tone; // é a cor de Investimentos em "Onde está o dinheiro"

  return (
    <Frame id="cdb-banco" title={`Guardado no ${bank}`} aside={<span className="tag-demo">via {via}</span>}>
      <p className="stat-value num">{money(total)}</p>
      <p className="faint stat-note">
        O {bank} manda cada depósito como um CDB separado, com o mesmo nome. Por isso não dá para saber de qual caixinha é cada um, e mostramos tudo junto.
      </p>
      <ul className="places" id="cdb-banco-lista">
        {(all ? positions : positions.slice(0, SHOWN)).map((v, i) => (
          <li key={i}>
            <span className="place-name">Posição {i + 1} <span className="faint">· {name}</span></span>
            <span className="faint num">{Math.round((v / total) * 100)}%</span>
            <b className="num">{money(v)}</b>
            <span className="place-bar" aria-hidden="true"><i style={{ background: tone, transform: `scaleX(${v / total})` }} /></span>
          </li>
        ))}
      </ul>
      {rest > 0 && (
        <button type="button" className="btn btn-quiet" aria-expanded={all} aria-controls="cdb-banco-lista" onClick={() => setAll(!all)}>
          {all ? "Mostrar menos" : `Mostrar as outras ${rest}`}
        </button>
      )}
    </Frame>
  );
}
