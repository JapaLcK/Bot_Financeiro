import { useId, useState } from "react";
import { obj } from "../../lib/ia-cards";
import { Expansivel } from "./Expansivel";
import { dataPtBr, lista, nomeLegivel, num, reais, textoOuNull, type Dinheiro } from "./legado";
import { useMais } from "./mais";
import { Sub } from "./previsao";

// Os cards `fatura` ("Fatura do cartão", `{ cartoes: { ok, cards[] } }`) e `parcelas` ("Parcelas futuras", `{ parcelas: { ok, installments[] } }`): formato legado, dinheiro em
// float (parts/cardIa/legado.ts). Só leitura do bloco. Parcelas, como no app: uma linha por COMPRA ("n de N pagas" e o trilho; tocar abre as parcelas aqui dentro,
// 5 compras e "+N"); compra quitada sai do card (dono, 2026-10-24).
type Cartao = { name: string; last4: string | null; credit_limit: number | string | null; due_day: number; closing_day: number; credit_used: number | string; open_bill: { paid_amount: number | string; due_amount: number | string } };
type Compra = { name: string; card_name: string | null; installments_total: number; manual?: boolean; parcelas: { installment_no: number; valor: number | string; is_paid: boolean; is_next: boolean; due_date: string }[] };

const cartaoOk = (din: Dinheiro) => (c: unknown) => obj(c) && typeof c.name === "string" && textoOuNull(c.last4) && (c.credit_limit === null || din(c.credit_limit)) && Number.isInteger(c.due_day) && Number.isInteger(c.closing_day)
  && din(c.credit_used) && obj(c.open_bill) && din(c.open_bill.paid_amount) && din(c.open_bill.due_amount);
const parcelaOk = (din: Dinheiro) => (p: unknown) => obj(p) && Number.isInteger(p.installment_no) && din(p.valor) && typeof p.is_paid === "boolean" && typeof p.is_next === "boolean" && typeof p.due_date === "string";
const compraOk = (din: Dinheiro) => (g: unknown) => obj(g) && typeof g.name === "string" && textoOuNull(g.card_name) && Number.isInteger(g.installments_total) && lista(g.parcelas, parcelaOk(din));
// O corpo (`cartoesOk`/`parcelasOk`) vale para o chat (legado: `ok`, float) e para o bloco do Resumo (a /api/v2: sem `ok`, dinheiro `texto`).
export const cartoesOk = (d: unknown, din: Dinheiro = num) => obj(d) && lista(d.cards, cartaoOk(din));
export const parcelasOk = (d: unknown, din: Dinheiro = num) => obj(d) && lista(d.installments, compraOk(din));
export const contratoFatura = (r: unknown) => { const d = (r as { cartoes?: unknown } | null)?.cartoes; return obj(d) && d.ok === true && cartoesOk(d); };
export const contratoParcelas = (r: unknown) => { const d = (r as { parcelas?: unknown } | null)?.parcelas; return obj(d) && d.ok === true && parcelasOk(d); };

const trilho = (razao: number) => <span className="ci-trilho" aria-hidden="true"><i style={{ background: "var(--ink-2)", transform: `scaleX(${Math.min(1, Math.max(0, razao))})` }} /></span>;

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
          {c.credit_limit !== null && Number(c.credit_limit) > 0 && trilho(Number(c.credit_used) / Number(c.credit_limit))}
          <ul className="ci-subs"><Sub nome="Já pago nesta fatura" valor={reais(c.open_bill.paid_amount)} /></ul>
        </li>
      ))}
    </ul>
  );
}

// "n de N pagas" pelo total do banco: as parcelas antes da 1ª que o PigBank viu (o Open Finance só traz as faturas lidas) contam como pagas.
// Cópia de `progresso` em app/src/features/painel/complementos.tsx, executada contra ela por tests/frontend/card_ia_paridade.test.mjs.
export function progresso(g: { installments_total: number; manual?: boolean; parcelas: { installment_no: number; is_paid: boolean }[] }) {
  const ultima = Math.max(0, ...g.parcelas.map((p) => p.installment_no)); // N nunca abaixo da maior parcela vista (total torto ou ausente)
  const n = Math.max(g.installments_total > 0 ? g.installments_total : g.parcelas.length, ultima);
  const antes = g.manual ? Math.max(0, n - g.parcelas.length) : g.parcelas.length ? Math.max(0, Math.min(...g.parcelas.map((p) => p.installment_no)) - 1) : 0; // manual: a que falta foi antecipada
  const pagas = Math.min(n, antes + g.parcelas.filter((p) => p.is_paid).length);
  return { pagas, n, quitada: pagas >= n && g.parcelas.every((p) => p.is_paid) }; // parcela vista em aberto nunca deixa a compra quitada
}

export function CardParcelas({ d }: { d: { installments: Compra[] } }) {
  const [aberta, setAberta] = useState<number | null>(null), base = useId();
  const abertas = d.installments.filter((g) => !progresso(g).quitada);
  const [vistas, mais] = useMais(abertas, 5, ["compra", "compras"]);
  if (!abertas.length) return <p className="ci-vazio">Não há parcelamentos registrados.</p>;
  return (
    <>
      <ul className="ci-lista">
        {vistas.map((g, i) => {
          const { pagas, n } = progresso(g);
          return (
            <Expansivel key={i} classe="ci-compra" regiao={`${base}-${i}`} aberta={aberta === i} alternar={() => setAberta(aberta === i ? null : i)}
              miolo={<><span className="ci-dupla"><span className="ci-nome">{g.name}</span>{g.card_name && <span className="ci-cap">{nomeLegivel(g.card_name)}</span>}</span><span className="ci-saldo num">{pagas} de {n} pagas</span>{trilho(pagas / n)}</>}
              corpo={g.parcelas.length > 0 ? <ul className="ci-subs">{g.parcelas.map((p, j) => (
                <li key={j} className="ci-sub ci-duas"><span className="ci-dupla"><span>Parcela {p.installment_no} de {n}</span><span className="faint">{dataPtBr(p.due_date)} · {p.is_paid ? "paga" : p.is_next ? "próxima" : "pendente"}</span></span><span className="num">{reais(p.valor)}</span></li>
              ))}</ul> : undefined} />
          );
        })}
      </ul>
      {mais}
    </>
  );
}
