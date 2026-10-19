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
// Serve ao banco conectado e ao conector do "Disponíveis": quem chama já deu `logo` (só da regra do CDN) e `cor` (já validada).
export function Ladrilho({ nome, logo: url, cor, grande }: { nome: string; logo: string | null; cor: string; grande?: boolean }) {
  const [falhou, setFalhou] = useState<string | null>(null);
  const logo = url && url !== falhou ? url : null;
  return (
    <span className={`banco-ini${logo ? " com-logo" : ""}${grande ? " grande" : ""}`} aria-hidden="true" style={logo ? undefined : { background: cor }}>
      {logo ? <img src={logo} alt="" referrerPolicy="no-referrer" decoding="async" onError={() => setFalhou(logo)} /> : Array.from(nome)[0].toUpperCase()}
    </span>
  );
}

// Pontinho no tom do estado + o rótulo do servidor: o rótulo diz o estado, a cor só acompanha.
export const Estado = ({ c }: { c: Conexao }) => (
  <span className={`banco-estado estado-${tomDoEstado(c.ui.state)}`}><span className="ponto" aria-hidden="true" />{c.ui.label || "Pendente"}</span>
);

// Um banco conectado, em linha: tocar abre o detalhe (BancoDetalhe), onde estão os valores (`/bancos`, juntados por id, nunca por nome).
// O botão que abriu entra no `onAbrir` para o foco voltar a ele quando o detalhe fechar.
export function BancoCard({ c, i, onAbrir }: { c: Conexao; i: number; onAbrir: (botao: HTMLButtonElement) => void }) {
  return (
    <li className="banco-card">
      <button type="button" className="banco-linha" aria-haspopup="dialog" onClick={(e) => onAbrir(e.currentTarget)}>
        <Ladrilho nome={nomeDoBanco(c)} logo={c.logo_url} cor={corDoBanco(c.institution_name ?? "", i)} />
        <span className="banco-id"><span className="banco-nome">{nomeDoBanco(c)}</span><Estado c={c} /></span>
        <i className="ph ph-caret-right" aria-hidden="true" />
      </button>
    </li>
  );
}
