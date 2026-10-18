import { FUSO, instante } from "../lib/v2";
import { haQuanto, moneyText } from "../lib/format.js";
import { corDoBanco, tomDoEstado } from "../lib/bancos";
import type { Banco } from "../lib/api-v2.gen";
import type { Conexao } from "./api";

const quando = new Intl.DateTimeFormat("pt-BR", { timeZone: FUSO, day: "2-digit", month: "2-digit", year: "numeric", hour: "2-digit", minute: "2-digit" }); // sem dateStyle: Safari 14.0
const plural = (n: number | null, um: string, varios: string) => (n && n > 0 ? [`${n} ${n === 1 ? um : varios}`] : []);

// Um banco conectado: o estado vem do snapshot e os valores de `/bancos` (juntados por id, nunca por nome). `v` é a linha do banco, ou
// o que a tela mostra no lugar dela: "…" (lendo) e "—" (sem linha, ou a leitura falhou). Null do servidor também é "—", nunca "R$ 0,00".
export function BancoCard({ c, i, v }: { c: Conexao; i: number; v: Banco | "…" | "—" }) {
  const nome = c.institution_name?.trim() || "Banco";
  const val = (x: string | null) => (typeof v === "string" ? v : moneyText(x) ?? "—");
  const ms = instante(c.last_sync_at);
  const quantos = typeof v === "string" ? [] : [...plural(v.contas, "conta", "contas"), ...plural(v.cartoes, "cartão", "cartões"), ...plural(v.investimentos, "investimento", "investimentos")];
  const linha = typeof v === "string" ? null : v;
  return (
    <li className="panel banco-card">
      <span className="banco-ini" aria-hidden="true" style={{ background: corDoBanco(c.institution_name ?? "", i) }}>{Array.from(nome)[0].toUpperCase()}</span>
      <div className="banco-id">
        <h2>{nome}</h2>
        <span className={`selo selo-${tomDoEstado(c.ui.state)}`}>{c.ui.label || "Pendente"}</span>
      </div>
      <b className="num banco-total">{val(linha?.total ?? null)}</b>
      <div className="banco-resto">
        {c.ui.detail && <p>{c.ui.detail}</p>}
        <p>Conta {val(linha?.saldo_conta ?? null)} · Investido {val(linha?.investido ?? null)}</p>
        {quantos.length > 0 && <p>{quantos.join(" · ")}</p>}
        {Number.isFinite(ms) && <time dateTime={c.last_sync_at!} title={quando.format(new Date(ms))}>{haQuanto(ms)}</time>}
      </div>
    </li>
  );
}
