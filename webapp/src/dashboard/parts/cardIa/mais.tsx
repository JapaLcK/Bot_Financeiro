import { useState } from "react";

// "+N": mostra os primeiros `visiveis` e abre o resto aqui mesmo (igual ao app: 5 linhas). Devolve a lista a desenhar e o botão (ou `false`).
export function useMais<T>(itens: readonly T[], visiveis: number, nome: [um: string, varios: string]) {
  const [aberto, setAberto] = useState(false), resto = itens.length - visiveis;
  const botao = resto > 0 && (
    <button type="button" className="btn btn-ghost ci-mais" aria-expanded={aberto} onClick={() => setAberto(!aberto)}
      aria-label={aberto ? `Mostrar menos ${nome[1]}` : `+${resto}, mostrar mais ${resto} ${resto === 1 ? nome[0] : nome[1]}`}>
      {aberto ? "Mostrar menos" : `+${resto}`}
    </button>
  );
  return [aberto ? itens : itens.slice(0, visiveis), botao] as const;
}
