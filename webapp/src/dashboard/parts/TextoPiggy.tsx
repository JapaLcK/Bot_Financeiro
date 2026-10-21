import type { ReactNode } from "react";

// O texto que a IA devolve, desenhado só como nós de texto do React: nada de HTML do modelo (sem innerHTML, sem link, sem imagem).
// O servidor usa o negrito do WhatsApp (`*x*`), não o markdown (`**x**`, que fica como veio). Cópia do `Markdown`/`ComNegrito` do app
// (features/painel/conversaFalas.tsx): parágrafos (linha em branco), itens `- `/`• ` e o negrito.
// O NEGRITO do app usa lookbehind, que o Safari 14 nem lê (o bundle inteiro cairia): aqui o caractere antes entra no casamento.
const NEGRITO = /(^|[^0-9*])\*([^\s*](?:[^*\n]*[^\s*])?)\*(?![0-9*])/g; // conteúdo colado nos asteriscos; dígito colado não vale (`5*3*4` é conta)
const PONTO = /^(?:- |• )(.*)$/;

function ComNegrito({ t }: { t: string }) {
  const nos: ReactNode[] = [];
  let i = 0;
  for (const m of t.matchAll(NEGRITO)) {
    const ini = m.index! + m[1].length;
    nos.push(t.slice(i, ini), <b key={ini}>{m[2]}</b>);
    i = m.index! + m[0].length;
  }
  nos.push(t.slice(i));
  return <>{nos}</>;
}

/** O mesmo texto sem os `*` do negrito, para o leitor de tela (o `role=status` do chat) não falar "asterisco". */
export const semNegrito = (t: string) => t.replace(NEGRITO, "$1$2");

type Grupo = { item: boolean; linhas: string[] };
export function TextoPiggy({ texto }: { texto: string }) {
  if (!texto) return null;
  return (
    <div className="msg-text msg-ia">
      {texto.replace(/\r\n?/g, "\n").split(/\n\s*\n/).flatMap((par, i) => {
        const grupos: Grupo[] = [];
        for (const linha of par.split("\n")) {
          const m = PONTO.exec(linha), ult = grupos[grupos.length - 1];
          if (m) { if (!m[1].trim()) continue; if (ult?.item) ult.linhas.push(m[1]); else grupos.push({ item: true, linhas: [m[1]] }); }
          else if (ult && !ult.item) ult.linhas.push(linha); else grupos.push({ item: false, linhas: [linha] });
        }
        return grupos.map((g, j) => g.item
          ? <ul key={`${i}-${j}`}>{g.linhas.map((l, k) => <li key={k}><ComNegrito t={l} /></li>)}</ul>
          : <p key={`${i}-${j}`}><ComNegrito t={g.linhas.join("\n")} /></p>);
      })}
    </div>
  );
}
