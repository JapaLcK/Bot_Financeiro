import { useId, type MouseEvent, type ReactNode } from "react";
import { corDoConector, type Conector } from "../lib/bancos";
import { OF } from "../parts/guia-falas";
import { Ladrilho } from "./BancoCard";

// O seletor de bancos é o módulo legado /open-finance-connect.js (o mesmo do /settings; ver `PBOpenFinance`). `pre` abre o seletor com esse banco já
// escolhido: quem confirma é o usuário, no seletor, e toda a regra de teto/plano/confirmação é a dele.
export type Pre = { id: number; name: string };
export type SeletorOF = { init: (o: Record<string, unknown>) => void; open: (pre?: Pre) => void; destroy: () => void; busy: () => boolean; reconnect: (itemId: string) => unknown };
export const seletorOF = () => (window as unknown as { PBOpenFinance?: SeletorOF }).PBOpenFinance;

// Link para o Open Finance do /settings; com o módulo carregado o clique abre o seletor aqui mesmo. Sem ele (o script não carregou), o href segue valendo.
// Clique com tecla (nova aba) ou botão do meio ficam com o navegador.
export function ConectarLink({ pre, className, label, aoAbrir, children }: { pre?: Pre; className: string; label?: string; aoAbrir?: () => void; children: ReactNode }) {
  const abrir = (e: MouseEvent) => {
    const seletor = seletorOF();
    if (!seletor || e.button || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
    e.preventDefault();
    aoAbrir?.(); // a mensagem da tentativa anterior sai
    seletor.open(pre);
  };
  return <a className={className} href={OF} aria-label={label} onClick={abrir}>{children}</a>;
}

// "Disponíveis para conectar": no máximo 5 populares (lib/bancos.ts::disponiveis) que a conta ainda não tem, mais o "Ver todos os bancos" (o seletor inteiro).
export function Disponiveis({ lista, aoAbrir }: { lista: Conector[]; aoAbrir: () => void }) {
  const id = useId();
  if (!lista.length) return null;
  return (
    <section className="panel banco-sec" aria-labelledby={id}>
      <header className="banco-sec-cab"><h2 id={id}>Disponíveis para conectar</h2><p>Conecte mais bancos via Open Finance.</p></header>
      <ul className="banco-lista">
        {lista.map((c, i) => (
          <li key={c.id} className="banco-item">
            <Ladrilho nome={c.name} logo={c.logo} cor={corDoConector(c.color, i)} />
            <span className="banco-id"><span className="banco-nome">{c.name}</span><span className="banco-sub">Conecte sua conta e tenha uma visão completa.</span></span>
            <ConectarLink pre={{ id: c.id, name: c.name }} className="btn btn-primary banco-conectar" label={`Conectar ${c.name}`} aoAbrir={aoAbrir}><i className="ph ph-plus" aria-hidden="true" />Conectar</ConectarLink>
          </li>
        ))}
        <li><ConectarLink className="banco-todos" aoAbrir={aoAbrir}>Ver todos os bancos<i className="ph ph-caret-right" aria-hidden="true" /></ConectarLink></li>
      </ul>
    </section>
  );
}
