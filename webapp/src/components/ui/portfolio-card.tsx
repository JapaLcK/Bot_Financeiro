import type { ChatMessage } from "@/chat/types";

type Portfolio = NonNullable<ChatMessage["portfolio"]>;
const money = new Intl.NumberFormat("pt-BR", { style: "currency", currency: "BRL" });

export function PortfolioCard({ portfolio, onAsk, disabled }: {
  portfolio: Portfolio; onAsk: (question: string, portfolio: Portfolio) => void; disabled: boolean;
}) {
  return <section className="pc-portfolio" aria-label="Carteira compartilhada pelo Open Finance">
    <div className="pc-portfolio-heading"><span>{portfolio.count} {portfolio.count === 1 ? "ativo" : "ativos"} · Open Finance</span>
      <strong>{money.format(portfolio.amount)}</strong></div>
    <div className="pc-portfolio-bar" role="img" aria-label={portfolio.groups.map(group => `${group.title}: ${money.format(group.amount)}`).join("; ")}>
      {portfolio.groups.map((group, index) => <span key={group.title} className={`pc-portfolio-segment pc-portfolio-segment-${index}`}
        style={{ width: `${100 * group.amount / portfolio.amount}%` }} />)}
    </div>
    {portfolio.groups.map(group => <details className="pc-portfolio-group" key={group.title}>
      <summary><span>{group.title} · {group.items.length} {group.items.length === 1 ? "ativo" : "ativos"}</span>
        <b>{money.format(group.amount)}</b></summary>
      <div className="pc-portfolio-rows">{group.items.map((item, index) => <div className="pc-portfolio-row" key={index}>
        <span title={item.name}>{item.name}<small>{item.institution}</small></span><b>{money.format(item.amount)}</b>
      </div>)}</div>
    </details>)}
    <details className="pc-portfolio-help">
      <summary>Como ler estes dados</summary>
      <div>
        <p>O PigBank consulta os investimentos que você autorizou compartilhar pelo Open Finance. Esta visualização não movimenta seu dinheiro.</p>
        <ul>
          <li><b>Renda fixa</b> reúne produtos como CDBs e títulos do Tesouro.</li>
          <li><b>Ações e FIIs</b> reúne os ativos de renda variável informados pela instituição.</li>
          <li>Os saldos refletem a última sincronização e podem levar de 1 a 3 dias para mostrar movimentações recentes.</li>
          <li>As caixinhas do Nubank costumam chegar como CDBs da Nu Financeira, sem o nome personalizado de cada caixinha.</li>
        </ul>
      </div>
    </details>
    <p className="pc-portfolio-note">{portfolio.note}</p>
    <div className="pc-portfolio-followups">{["Quais são meus maiores CDBs?", "Como está minha renda variável?"].map(question =>
      <button key={question} type="button" disabled={disabled} onClick={() => onAsk(question, portfolio)}>{question}</button>)}</div>
  </section>;
}
