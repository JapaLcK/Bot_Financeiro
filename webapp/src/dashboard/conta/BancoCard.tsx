import { useState, type ReactNode } from "react";
import { moneyText } from "../lib/format.js";
import { corDoBanco, quandoSync, tomDoEstado } from "../lib/bancos";
import type { Banco } from "../lib/api-v2.gen";
import type { Conexao } from "./api";

export const nomeDoBanco = (c: Conexao) => c.institution_name?.trim() || "Banco";
// Cópia da regra do app (app/src/features/openFinance/conexoes/BancoCard.tsx): o teste bancos_paridade compara as duas listas (§0.7).
export const SEM_RECONEXAO = ["removed", "item_missing", "paused"];
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

// Pontinho no tom do estado + o rótulo do servidor: o rótulo diz o estado, a cor só acompanha. Exceção (decisão do dono): `updated` diz "Conectado", como no desenho.
export const Estado = ({ c }: { c: Conexao }) => (
  <span className={`banco-estado estado-${tomDoEstado(c.ui.state)}`}><span className="ponto" aria-hidden="true" />{c.ui.state === "updated" ? "Conectado" : c.ui.label || "Pendente"}</span>
);

// Um banco conectado, em linha: ladrilho, nome, estado, última atualização e as ações. "Detalhes" abre o BancoDetalhe (os valores do `/bancos`, juntados por id, nunca por nome);
// o botão que abriu entra no `onAbrir` para o foco voltar a ele quando o detalhe fechar. "Sincronizar" atualiza TODOS os bancos (o servidor só sabe fazer todos), então todos
// os botões travam juntos (`sincronizando`). `bloqueado` (reconexão ou diálogo de Desconectar em curso) só PINTA o travamento: quem decide é o `sincronizar` do pai, que confere refs (o estado está velho no mesmo tick).
// `aria-disabled`, não `disabled`: o botão focado não pode perder o foco no meio da rodada. O menu "…" vem pronto (`menu`).
export function BancoCard({ c, i, sincronizando, bloqueado, onAbrir, onSincronizar, menu }: { c: Conexao; i: number; sincronizando: boolean; bloqueado: boolean; onAbrir: (botao: HTMLButtonElement) => void; onSincronizar: () => void; menu: ReactNode }) {
  const nome = nomeDoBanco(c);
  return (
    <li className="banco-card">
      <Ladrilho nome={nome} logo={c.logo_url} cor={corDoBanco(c.institution_name ?? "", i)} />
      <span className="banco-nome">{nome}</span>
      <Estado c={c} />
      <span className="banco-quando"><span className="banco-leg">Última atualização</span><span>{quandoSync(c.last_sync_at)}</span></span>
      <span className="banco-acoes">
        <button type="button" className="btn btn-ghost banco-acao banco-linha" aria-haspopup="dialog" aria-label={`Detalhes de ${nome}`} onClick={(e) => onAbrir(e.currentTarget)}>
          <i className="ph ph-eye" aria-hidden="true" /><span className="banco-bt">Detalhes</span>
        </button>
        <button type="button" className="btn btn-ghost banco-acao" aria-label={sincronizando ? "Sincronizando…" : "Sincronizar todos os bancos"} aria-busy={sincronizando} aria-disabled={sincronizando || bloqueado || undefined} onClick={() => { if (!sincronizando) onSincronizar(); }}>
          <i className="ph ph-arrows-clockwise" aria-hidden="true" /><span className="banco-bt">{sincronizando ? "Sincronizando…" : "Sincronizar"}</span>
        </button>
        {menu}
      </span>
    </li>
  );
}
