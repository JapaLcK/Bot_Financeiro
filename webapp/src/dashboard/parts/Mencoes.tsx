import { agenteImg } from "../lib/brand";
import type { Acesso } from "../lib/agentes";

export interface ItemMencao { kind: string; nome: string; tema: string; acesso: Acesso }
export const GRUPO: Record<Acesso, string> = { ready: "Agentes ativos", activate: "Ativar para conversar", no_energy: "Ativar para conversar", upgrade: "No PigBank+" };

// A lista do @ da barra (parts/AskBar.tsx), no molde da busca (Command.tsx): o foco fica no campo, o item marcado é o aria-activedescendant dele.
export function Mencoes({ aqui, itens, ativo, onAtivo, onEscolher }: { aqui: boolean; itens: ItemMencao[]; ativo: number; onAtivo: (i: number) => void; onEscolher: (i: ItemMencao) => void }) {
  let ultimo = "";
  return (
    <div className="mencoes" data-here={aqui || undefined} onMouseDown={(e) => e.preventDefault() /* clicar num item não tira o foco do campo */}>
      <ul className="cmdk-list" id="mencoes-lista" role="listbox" aria-label="Agentes">
        {itens.map((a, i) => {
          const grupo = GRUPO[a.acesso], cabeca = grupo !== ultimo ? (ultimo = grupo) : null;
          return (
            <li key={a.kind} role="presentation">
              {cabeca && <p className="cmdk-group" aria-hidden="true">{cabeca}</p>}
              <div id={`mencao-${i}`} role="option" aria-selected={i === ativo} className="cmdk-item" data-i={i}
                onPointerMove={() => onAtivo(i)} onClick={() => onEscolher(a)}>
                <img src={agenteImg(a.kind)} alt="" width={22} height={22} />
                <span>{a.nome}</span>
                <span className="faint cmdk-hint">{a.tema}</span>
              </div>
            </li>
          );
        })}
      </ul>
      <p className="cmdk-foot faint"><kbd>↑</kbd><kbd>↓</kbd> navegar <kbd>↵</kbd> chamar <kbd>esc</kbd> fechar</p>
    </div>
  );
}
