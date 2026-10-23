import { useId, useState } from "react";
import { obj } from "../../lib/ia-cards";
import { Expansivel } from "./Expansivel";
import { dataPtBr, lista, nomeLegivel, num, numOuNull, reais, textoOuNull } from "./legado";
import { useMais } from "./mais";
import { Sub } from "./previsao";

// Os cards `fatura` ("Fatura do cartão", `{ cartoes: { ok, cards[] } }`) e `parcelas` ("Parcelas futuras", `{ parcelas: { ok, installments[] } }`): formato legado, dinheiro em
// float (parts/cardIa/legado.ts). Só leitura do bloco. Diferença do app nas parcelas: uma linha por COMPRA ("n de N pagas" e o trilho; tocar abre as parcelas aqui dentro,
// 5 compras e "+N"), no lugar de todas as parcelas de todas as compras empilhadas.
type Cartao = { name: string; last4: string | null; credit_limit: number | null; due_day: number; closing_day: number; credit_used: number; open_bill: { paid_amount: number; due_amount: number } };
type Compra = { name: string; card_name: string | null; installments_total: number; parcelas: { installment_no: number; valor: number; is_paid: boolean; is_next: boolean; due_date: string }[] };

const cartaoOk = (c: unknown) => obj(c) && typeof c.name === "string" && textoOuNull(c.last4) && numOuNull(c.credit_limit) && Number.isInteger(c.due_day) && Number.isInteger(c.closing_day)
  && num(c.credit_used) && obj(c.open_bill) && num(c.open_bill.paid_amount) && num(c.open_bill.due_amount);
const parcelaOk = (p: unknown) => obj(p) && Number.isInteger(p.installment_no) && num(p.valor) && typeof p.is_paid === "boolean" && typeof p.is_next === "boolean" && typeof p.due_date === "string";
const compraOk = (g: unknown) => obj(g) && typeof g.name === "string" && textoOuNull(g.card_name) && Number.isInteger(g.installments_total) && lista(g.parcelas, parcelaOk);
export const contratoFatura = (r: unknown) => { const d = (r as { cartoes?: unknown } | null)?.cartoes; return obj(d) && d.ok === true && lista(d.cards, cartaoOk); };
export const contratoParcelas = (r: unknown) => { const d = (r as { parcelas?: unknown } | null)?.parcelas; return obj(d) && d.ok === true && lista(d.installments, compraOk); };

const trilho = (razao: number, pronto: boolean) => <span className="ci-trilho" aria-hidden="true"><i style={{ background: pronto ? "var(--gain)" : "var(--ink-2)", transform: `scaleX(${Math.min(1, Math.max(0, razao))})` }} /></span>;

export function CardFatura({ d }: { d: { cards: Cartao[] } }) {
  if (!d.cards.length) return <p className="ci-vazio">Nenhum cartão cadastrado.</p>;
  return (
    <ul className="ci-lista">
      {d.cards.map((c, i) => (
        <li key={i} className="ci-conta ci-grupo">
          <div className="ci-linha ci-leitura"><span className="ci-nome">{nomeLegivel(c.name)}{c.last4 && ` · ${c.last4}`}</span><span className="ci-saldo num">{reais(c.open_bill.due_amount)}</span></div>
          <p className="ci-cap">Vencimento dia {c.due_day} · fechamento dia {c.closing_day}</p>
          <ul className="ci-subs">
            <Sub nome="Limite" valor={reais(c.credit_limit)} />
            <Sub nome="Crédito utilizado" valor={reais(c.credit_used)} />
          </ul>
          {c.credit_limit !== null && c.credit_limit > 0 && trilho(c.credit_used / c.credit_limit, false)}
          <ul className="ci-subs"><Sub nome="Já pago nesta fatura" valor={reais(c.open_bill.paid_amount)} /></ul>
        </li>
      ))}
    </ul>
  );
}

export function CardParcelas({ d }: { d: { installments: Compra[] } }) {
  const [aberta, setAberta] = useState<number | null>(null), base = useId();
  const [vistas, mais] = useMais(d.installments, 5, ["compra", "compras"]);
  if (!d.installments.length) return <p className="ci-vazio">Não há parcelamentos registrados.</p>;
  return (
    <>
      <ul className="ci-lista">
        {vistas.map((g, i) => {
          const pagas = g.parcelas.filter((p) => p.is_paid).length, n = g.parcelas.length;
          return (
            <Expansivel key={i} classe="ci-compra" regiao={`${base}-${i}`} aberta={aberta === i} alternar={() => setAberta(aberta === i ? null : i)}
              miolo={<><span className="ci-dupla"><span className="ci-nome">{g.name}</span>{g.card_name && <span className="ci-cap">{nomeLegivel(g.card_name)}</span>}</span><span className="ci-saldo num">{pagas} de {n} pagas</span>{trilho(pagas / Math.max(1, n), pagas === n && n > 0)}</>}
              corpo={n > 0 ? <ul className="ci-subs">{g.parcelas.map((p, j) => (
                <li key={j} className="ci-sub ci-duas"><span className="ci-dupla"><span>Parcela {p.installment_no} de {g.installments_total}</span><span className="faint">{dataPtBr(p.due_date)} · {p.is_paid ? "paga" : p.is_next ? "próxima" : "pendente"}</span></span><span className="num">{reais(p.valor)}</span></li>
              ))}</ul> : undefined} />
          );
        })}
      </ul>
      {mais}
    </>
  );
}
