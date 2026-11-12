import { ICON } from "../lib/brand";
import { money } from "../lib/format.js";
import { ROTULO_REEMBOLSO, dia, situacao, type Comissao, type Pedido } from "./api";

const NOME_SAQUE: Record<Pedido["status"], { rotulo: string; classe?: string }> = { requested: { rotulo: "em análise" }, paid: { rotulo: "pago", classe: "gain" }, rejected: { rotulo: "rejeitado", classe: "warn" } };
const Vazio = ({ texto }: { texto: string }) => <p className="afil-vazio faint"><img src={ICON} alt="" width={28} height={28} />{texto}</p>;

// O corte da API (`frontend/routes/affiliates.py`; tests/test_pagina_afiliado.py compara): só no limite há o que dizer, e os totais dos quadros contam tudo.
const LIMITE_COMISSOES = 50;
const LIMITE_SAQUES = 20;

export function Comissoes({ lista, semComissao }: { lista: Comissao[]; semComissao: boolean }) {
  return <section className="panel span-7" aria-labelledby="afil-comissoes">
    <h2 id="afil-comissoes">Comissões</h2>
    {lista.length === 0 ? <Vazio texto={semComissao ? "Nenhuma comissão." : "Nenhuma comissão ainda. Divulgue seu link."} /> : <>
      <ul className="afil-lista">{lista.map((c) => {
        const st = situacao(c);
        const reembolso = ROTULO_REEMBOLSO[c.payment_refund];
        return <li key={c.id}>
          <div><b className="num">{money(c.amount)}</b><p className="faint">{c.paid_months === 12 ? `plano anual de ${money(c.invoice_amount)} (base 1/12)` : `fatura de ${money(c.invoice_amount)}`} · {dia(c.created_at)}{c.original_amount !== null && ` · ajustada de ${money(c.original_amount)}`}</p></div>
          <div className="afil-selos"><span className={`selo ${st.classe ?? ""}`}>{st.rotulo}</span>{reembolso && <span className="selo">{reembolso}</span>}</div>
        </li>;
      })}</ul>
      {lista.length >= LIMITE_COMISSOES && <p className="faint afil-nota">Mostrando as mais recentes.</p>}
    </>}
  </section>;
}

export function Saques({ lista }: { lista: Pedido[] }) {
  return <section className="panel span-5" aria-labelledby="afil-saques">
    <h2 id="afil-saques">Saques</h2>
    {lista.length === 0 ? <Vazio texto="Nenhum saque solicitado." /> : <>
      <ul className="afil-lista">{lista.map((p) => {
        const st = NOME_SAQUE[p.status];
        return <li key={p.id}>
          <div>
            <b className="num">{money(p.amount)}</b><p className="faint">pedido em {dia(p.requested_at)}</p>
            {p.note && <p className={p.status === "rejected" ? "warn afil-motivo" : "faint afil-motivo"}>{p.status === "rejected" ? "Motivo da rejeição: " : ""}<span>{p.note}</span></p>}
          </div>
          <div className="afil-selos"><span className={`selo ${st.classe ?? ""}`}>{st.rotulo}</span></div>
        </li>;
      })}</ul>
      {lista.length >= LIMITE_SAQUES && <p className="faint afil-nota">Mostrando os mais recentes.</p>}
    </>}
  </section>;
}
