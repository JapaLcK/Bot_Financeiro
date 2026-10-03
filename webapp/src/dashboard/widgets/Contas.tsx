// Bloco de contas do Resumo, sobre a /api/v2/contas: o saldo de hoje no total, a carteira
// e cada conta do Open Finance. O que fica fora do total (outra moeda, conexão pausada,
// saldo ausente) só aparece no "ver contas fora do total". Saldo ausente é "—", nunca R$ 0,00.
import { useId, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import type { Conta } from "../lib/api-v2.gen";
import { moneyIn } from "../lib/format.js";
import { contasQuery } from "../lib/v2";
import { Frame } from "../parts/Frame";
import { Selos, type Motivo } from "../parts/Selos";

// Só texto decimal finito é número ("1234.56", "-5", "1E+2"); null, "", "NaN", "Infinity" e o resto viram "—".
const valor = (s: string | null) => (typeof s === "string" && /^-?\d+(\.\d+)?(e[+-]?\d+)?$/i.test(s) && Number.isFinite(Number(s)) ? Number(s) : null);
const saldoOu = (s: string | null, moeda: string) => { const n = valor(s); return n === null ? "—" : moneyIn(n, moeda); };

function Linha({ nome, saldo, moeda, motivos }: { nome: string; saldo: string | null; moeda: string; motivos: Motivo[] }) {
  return (
    <li className="conta">
      <span className="conta-nome">{nome}</span>
      <span className="conta-saldo num">{saldoOu(saldo, moeda)}</span>
      <Selos motivos={motivos} />
    </li>
  );
}

const linha = (c: Conta) => (
  <Linha key={c.id} nome={[c.instituicao, c.nome].filter(Boolean).join(" · ") || "Conta"} saldo={c.saldo} moeda={c.moeda} motivos={c.motivos} />
);

export function Contas() {
  const q = useQuery(contasQuery);
  const [aberto, setAberto] = useState(false);
  const foraId = useId();
  const d = q.data;
  if (!d) {
    return (
      <Frame id="contas" title="Contas" real>
        {q.isPending ? <p role="status" className="faint">Carregando…</p> : (
          <div className="empty" role="alert">
            <p>Não deu para carregar as contas.</p>
            <button type="button" className="btn retry btn-ghost" onClick={() => q.refetch()}>Tentar de novo</button>
          </div>
        )}
      </Frame>
    );
  }
  // A nota e o botão contam a lista que a tela abre, não o `fora_do_total` declarado.
  const fora = d.contas.filter((c) => !c.no_total);
  const n = fora.length;
  const notas = [
    d.carteira.motivos.includes("carteira_nao_confirmada") && "carteira a confirmar",
    n > 0 && `${n} ${n === 1 ? "conta" : "contas"} fora do total`,
  ].filter(Boolean);
  return (
    <Frame id="contas" title="Contas" real>
      <div className="contas-total">
        <span className="stat-value num">{saldoOu(d.total, "BRL")}</span>
        <p className="w-lede">Saldo de hoje{notas.map((t) => ` · ${t}`).join("")}</p>
      </div>
      <ul className="contas">
        <Linha nome="Carteira Piggy" saldo={d.carteira.saldo} moeda="BRL" motivos={d.carteira.motivos} />
        {d.contas.filter((c) => c.no_total).map(linha)}
      </ul>
      {n > 0 && (
        <>
          <button type="button" className="btn btn-quiet contas-mais" aria-expanded={aberto} aria-controls={aberto ? foraId : undefined} onClick={() => setAberto(!aberto)}>
            <i className="ph ph-caret-right" aria-hidden="true" />ver contas fora do total ({n})
          </button>
          {aberto && <ul id={foraId} className="contas">{fora.map(linha)}</ul>}
        </>
      )}
    </Frame>
  );
}
