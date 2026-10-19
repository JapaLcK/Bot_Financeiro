import { useState } from "react";
import { moneyText } from "../lib/format.js";
import { corDoBanco, tomDoEstado } from "../lib/bancos";
import type { Banco } from "../lib/api-v2.gen";
import type { Conexao } from "./api";

export const nomeDoBanco = (c: Conexao) => c.institution_name?.trim() || "Banco";
export const plural = (n: number | null, um: string, varios: string) => (n && n > 0 ? [`${n} ${n === 1 ? um : varios}`] : []);
// `v` é a linha do banco, ou o que a tela mostra no lugar dela: "…" (lendo) e "—" (sem linha, ou a leitura falhou). Null do servidor também é "—", nunca "R$ 0,00".
export type Linha = Banco | "…" | "—";
export const dinheiro = (v: Linha, x: string | null) => (typeof v === "string" ? v : moneyText(x) ?? "—");

// O ladrilho do banco: o logo (quadrado de fundo claro neutro) ou a inicial na cor do banco. A URL que não carregou fica anotada: uma URL nova é tentada de novo.
export function Ladrilho({ c, i, grande }: { c: Conexao; i: number; grande?: boolean }) {
  const nome = nomeDoBanco(c);
  const [falhou, setFalhou] = useState<string | null>(null);
  const logo = c.logo_url && c.logo_url !== falhou ? c.logo_url : null;
  return (
    <span className={`banco-ini${logo ? " com-logo" : ""}${grande ? " grande" : ""}`} aria-hidden="true" style={logo ? undefined : { background: corDoBanco(c.institution_name ?? "", i) }}>
      {logo ? <img src={logo} alt="" referrerPolicy="no-referrer" decoding="async" onError={() => setFalhou(logo)} /> : Array.from(nome)[0].toUpperCase()}
    </span>
  );
}

// Pontinho no tom do estado + o rótulo do servidor: o rótulo diz o estado, a cor só acompanha.
export const Estado = ({ c }: { c: Conexao }) => (
  <span className={`banco-estado estado-${tomDoEstado(c.ui.state)}`}><span className="ponto" aria-hidden="true" />{c.ui.label || "Pendente"}</span>
);

// Conta × investido em %, só com os dois números exatos e sem ressalva do servidor (a barra do topo recusa pelo mesmo motivo).
function composicao(v: Linha) {
  if (typeof v === "string" || v.motivos.length || v.saldo_conta == null || v.investido == null) return null;
  const a = Number(v.saldo_conta), b = Number(v.investido);
  if (!Number.isFinite(a) || !Number.isFinite(b) || a < 0 || b < 0 || a + b <= 0) return null;
  const pct = Math.round((a / (a + b)) * 100);
  return { a, b, rotulo: `Em conta ${pct}%, investido ${100 - pct}%` };
}

// Um banco conectado, em linha: tocar abre o detalhe (BancoDetalhe). O estado vem do snapshot e os valores de `/bancos`, juntados por id, nunca por nome.
// O botão que abriu entra no `onAbrir` para o foco voltar a ele quando o detalhe fechar.
export function BancoCard({ c, i, v, onAbrir }: { c: Conexao; i: number; v: Linha; onAbrir: (botao: HTMLButtonElement) => void }) {
  const barra = composicao(v);
  return (
    <li className="banco-card">
      <button type="button" className="panel banco-linha" aria-haspopup="dialog" onClick={(e) => onAbrir(e.currentTarget)}>
        <Ladrilho c={c} i={i} />
        <span className="banco-id"><span className="banco-nome">{nomeDoBanco(c)}</span><Estado c={c} /></span>
        <b className="num banco-total">{dinheiro(v, typeof v === "string" ? null : v.total)}</b>
        <i className="ph ph-caret-right" aria-hidden="true" />
        {barra && (
          <span className="comp-bar banco-barra" role="img" aria-label={barra.rotulo}>
            {barra.a > 0 && <span className="cor-conta" style={{ flexGrow: barra.a }} />}
            {barra.b > 0 && <span className="cor-inv" style={{ flexGrow: barra.b }} />}
          </span>
        )}
      </button>
    </li>
  );
}
