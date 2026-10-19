import { useState } from "react";
import { FUSO, instante } from "../lib/v2";
import { haQuanto, moneyText } from "../lib/format.js";
import { corDoBanco, tomDoEstado } from "../lib/bancos";
import type { Banco } from "../lib/api-v2.gen";
import type { Conexao } from "./api";

const quando = new Intl.DateTimeFormat("pt-BR", { timeZone: FUSO, day: "2-digit", month: "2-digit", year: "numeric", hour: "2-digit", minute: "2-digit" }); // sem dateStyle: Safari 14.0
const plural = (n: number | null, um: string, varios: string) => (n && n > 0 ? [`${n} ${n === 1 ? um : varios}`] : []);

export const nomeDoBanco = (c: Conexao) => c.institution_name?.trim() || "Banco";

// Um banco conectado: o estado vem do snapshot e os valores de `/bancos` (juntados por id, nunca por nome). `v` é a linha do banco, ou
// o que a tela mostra no lugar dela: "…" (lendo) e "—" (sem linha, ou a leitura falhou). Null do servidor também é "—", nunca "R$ 0,00".
// `bloqueado`: já há um diálogo/DELETE em andamento (só um banco por vez); o botão que abriu entra no `onDesconectar` para devolver o foco.
export function BancoCard({ c, i, v, onDesconectar, bloqueado }: { c: Conexao; i: number; v: Banco | "…" | "—"; onDesconectar: (botao: HTMLButtonElement) => void; bloqueado: boolean }) {
  const nome = nomeDoBanco(c);
  const val = (x: string | null) => (typeof v === "string" ? v : moneyText(x) ?? "—");
  const ms = instante(c.last_sync_at);
  const quantos = typeof v === "string" ? [] : [...plural(v.contas, "conta", "contas"), ...plural(v.cartoes, "cartão", "cartões"), ...plural(v.investimentos, "investimento", "investimentos")];
  const linha = typeof v === "string" ? null : v;
  const [falhou, setFalhou] = useState<string | null>(null); // a URL que não carregou: uma URL nova é tentada de novo
  const logo = c.logo_url && c.logo_url !== falhou ? c.logo_url : null;
  return (
    <li className="panel banco-card">
      <span className={logo ? "banco-ini com-logo" : "banco-ini"} aria-hidden="true" style={logo ? undefined : { background: corDoBanco(c.institution_name ?? "", i) }}>
        {logo ? <img src={logo} alt="" referrerPolicy="no-referrer" decoding="async" onError={() => setFalhou(logo)} /> : Array.from(nome)[0].toUpperCase()}
      </span>
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
      <details className="banco-acoes">
        <summary>Gerenciar<span className="sr-only"> {nome}</span></summary>
        <button type="button" className="btn btn-ghost" disabled={bloqueado} onClick={(e) => onDesconectar(e.currentTarget)}>Desconectar {nome}</button>
      </details>
    </li>
  );
}
