import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { copiar } from "../lib/copiar";
import { money } from "../lib/format.js";
import { ErroApi, useAfiliado } from "../lib/v2";
import { href, route } from "../router";
import { useDonoDaTela } from "../familia/escrita";
import { afiliadoQuery, falhaLeitura, type Afiliado as Dados } from "./api";
import { Comissoes, Saques } from "./Historico";
import { Saque } from "./Saque";

const Cabeca = () => <header className="page-head"><h1 id="page-title" tabIndex={-1}>{route("/afiliado").title}</h1></header>;
const plural = (n: number, um: string, varios: string) => `${n} ${n === 1 ? um : varios}`;

// No /painel: a rota existe para todos (nasce antes do /me), então é aqui que a tela decide. Sem cadastro a API nem é chamada; quem protege de verdade é o 404 dela.
export function PaginaAfiliado() {
  return useAfiliado() ? <AreaAfiliado /> : <SoParaAfiliados />;
}

function SoParaAfiliados() {
  return <>
    <Cabeca />
    <section className="panel afiliado afil-fora"><p>Esta área é só para afiliados.</p><a className="btn btn-ghost" href={href("/")}>Voltar ao Resumo</a></section>
  </>;
}

// `useDonoDaTela` relê o perfil ao voltar à aba: é essa leitura que, com a conta trocada, faz o main.tsx zerar a query. Sem dado, `Area` desmonta e a chave Pix digitada vai junto.
export function AreaAfiliado() {
  useDonoDaTela();
  const q = useQuery(afiliadoQuery);
  if (q.data === null) return <SoParaAfiliados />;
  if (q.data) return <Area d={q.data} falhou={q.isError} tentar={() => void q.refetch()} sessao={q.error instanceof ErroApi && q.error.status === 401} />;
  return <>
    <Cabeca />
    <section className="panel afiliado">
      {q.isPending ? <p role="status" className="faint">Carregando…</p> : <div className="empty" role="alert">
        <p>{falhaLeitura(q.error)}</p>
        <button type="button" className="btn btn-ghost retry" onClick={() => void q.refetch()}>Tentar de novo</button>
      </div>}
    </section>
  </>;
}

function Area({ d, falhou, tentar, sessao }: { d: Dados; falhou: boolean; tentar: () => void; sessao: boolean }) {
  const s = d.stats;
  const semComissao = d.commission_percent === 0;
  const quadros: [string, string, string, string?][] = [
    ["Cadastros indicados", String(s.referrals), "pelo seu link"],
    ["Clientes que pagaram", String(s.paying_clients), s.refunded_clients ? plural(s.refunded_clients, "reembolsado", "reembolsados") : "pagamento confirmado"],
    ["Comissões geradas", money(s.generated), "sem as estornadas"],
    ["Em carência", money(s.held), `libera ${d.hold_days} dias após a cobrança`],
    ["Em revisão", money(s.review), "reembolso de cliente em análise"],
    ["Disponível", money(s.available), `saque mínimo de ${money(d.min_payout)}`, s.available > 0 ? "gain" : undefined],
    ["Em saque", money(s.requested), "pedido em análise"],
    ["Já pago", money(s.paid), "enviado por Pix"],
  ];
  return <>
    <Cabeca />
    {d.status === "disabled" && <p role="status" className="afil-aviso warn"><i className="ph ph-warning" aria-hidden="true" /> Seu cadastro de afiliado está desativado. O link não gera novas comissões, mas seus saldos, o histórico e o saque continuam aqui.</p>}
    {falhou && <div role="alert" className="afil-aviso warn"><p>{sessao ? "Sua sessão terminou." : "Não foi possível atualizar. Os valores abaixo podem estar velhos."}</p>
      {sessao ? <button type="button" className="btn btn-ghost" onClick={() => location.reload()}>Recarregar</button> : <button type="button" className="btn btn-ghost" onClick={tentar}>Tentar de novo</button>}</div>}
    <div className="page-grid afiliado">
      <section className="panel span-12" aria-label="Seus números">
        <dl className="afil-quadros">
          {quadros.map(([rotulo, numero, nota, tom]) => <div key={rotulo}><dt>{rotulo}</dt><dd className={tom}>{numero}</dd><dd className="faint afil-nota">{nota}</dd></div>)}
        </dl>
        <p className="afil-reembolsos faint">Reembolsos: {plural(s.refunds.total, "total", "totais")} · {plural(s.refunds.partial, "parcial", "parciais")}</p>
      </section>
      <Divulgar d={d} semComissao={semComissao} />
      {(!semComissao || s.available > 0 || s.requested > 0) && <Saque d={d} velho={falhou} />}
      <Comissoes lista={d.commissions} semComissao={semComissao} />
      <Saques lista={d.payouts} />
    </div>
  </>;
}

function Divulgar({ d, semComissao }: { d: Dados; semComissao: boolean }) {
  const [aviso, setAviso] = useState("");
  const copia = (valor: string, feito: string) => {
    copiar(valor).then(() => setAviso(feito), () => setAviso("Não foi possível copiar. Selecione o texto e copie à mão."));
  };
  return <section className="panel span-7" aria-labelledby="afil-divulgar">
    <h2 id="afil-divulgar">Seu link de divulgação</h2>
    <p className="faint">{semComissao ? "Você participa sem comissão neste programa." : `Você ganha ${d.commission_percent.toLocaleString("pt-BR")}% da primeira cobrança do plano no cartão. Pagamento por Pix não gera comissão.`}</p>
    <div className="afil-copia">
      <span>Código</span><code>{d.code}</code>
      <button type="button" className="btn btn-ghost" aria-label="Copiar código" onClick={() => copia(d.code, "Código copiado")}><i className="ph ph-clipboard-text" aria-hidden="true" />Copiar</button>
    </div>
    <div className="afil-copia">
      <span>Link</span><code>{d.link}</code>
      <button type="button" className="btn btn-ghost" aria-label="Copiar link" onClick={() => copia(d.link, "Link copiado")}><i className="ph ph-clipboard-text" aria-hidden="true" />Copiar</button>
    </div>
    <p role="status" className="faint afil-status">{aviso}</p>
  </section>;
}
