import { useState } from "react";
import { ask } from "../lib/conversation";
import type { Path } from "../router";

// A barra de conversa com o Piggy. No desktop flutua em todas as páginas; no celular só
// aparece na página da conversa (lá o acesso é o botão do meio). Na #/piggy ela é a
// caixa de mensagem. O nome de transição a mantém no lugar quando a página troca.
export function AskBar({ path }: { path: Path }) {
  const [text, setText] = useState("");
  const here = path === "/piggy";
  const avatar = <img src="../frontend/brand/icon.png" alt="" width={28} height={28} />;

  return (
    <form className="askbar" data-here={here || undefined} role="search" aria-label="Conversa com o Piggy"
      onSubmit={(e) => {
        e.preventDefault();
        const t = text.trim();
        if (!t) return;
        setText("");
        ask({ text: t });
      }}>
      {avatar}
      <input id="askbar-input" value={text} onChange={(e) => setText(e.target.value)} maxLength={500}
        placeholder="Converse com o Piggy…" aria-label="Pergunte ao Piggy" autoComplete="off" enterKeyHint="send" />
      <button type="submit" className="askbar-send" aria-label="Enviar" disabled={!text.trim()}>
        <i className="ph ph-arrow-up" aria-hidden="true" />
      </button>
    </form>
  );
}
