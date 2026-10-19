import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { ask, perguntarAoAgente } from "../lib/conversation";
import type { Path } from "../router";
import { ICON } from "../lib/brand";
import { AGENTES, acessoDe, agentesQuery, energiaDe, mencaoDe, semAcento, tokenEm } from "../lib/agentes";
import { uidValido } from "../conta/api";
import { CHAT_REAL, DEMO, contaQuery } from "../lib/v2";
import { GRUPO, Mencoes, type ItemMencao } from "./Mencoes";

const GRUPOS = ["Agentes ativos", "Ativar para conversar", "No PigBank+"];

// A barra de conversa com o Piggy. No desktop flutua em todas as páginas; no celular só
// aparece na página da conversa (lá o acesso é o botão do meio). Na #/piggy ela é a
// caixa de mensagem. O nome de transição a mantém no lugar quando a página troca.
// "@" (no começo ou depois de espaço) abre a lista de agentes: a menção escolhe com quem a mensagem vai.
export function AskBar({ path }: { path: Path }) {
  const [text, setText] = useState("");
  const [cursor, setCursor] = useState(0);
  const [ativo, setAtivo] = useState(0);
  const [fechada, setFechada] = useState(false); // Esc: fecha a lista e mantém o texto, até a próxima edição
  const qc = useQueryClient();
  const enviando = useRef(false); // o envio espera o catálogo: um segundo Enter nesse meio tempo não duplica
  const input = useRef<HTMLInputElement>(null);
  const depois = useRef<number | null>(null); // onde o cursor fica depois de inserir a menção
  const here = path === "/piggy";
  const avatar = <img src={ICON} alt="" width={28} height={28} />;

  // O catálogo só é lido quando há um "@" em edição: sem ele, nenhuma requisição a /auth nem a /agents.
  // Também com a menção já escrita fora do cursor (texto colado): quem digita o nome inteiro passa pelo token, mas o catálogo tem de estar lido no envio.
  const token = tokenEm(text, cursor), quer = /(^|\s)@/.test(text);
  const conta = useQuery({ ...contaQuery, enabled: contaQuery.enabled && quer }).data;
  const uid = conta?.user_id;
  const dados = useQuery(agentesQuery(uid, quer)).data; // falhou/404/beta desligado: sem dados, a lista não abre e tudo vai ao Piggy

  const itens: ItemMencao[] = [];
  if (token && dados) {
    for (const g of GRUPOS) for (const a of AGENTES) {
      const acesso = acessoDe(dados, a.kind);
      if (acesso && GRUPO[acesso] === g && semAcento(a.nome).startsWith(token.q)) itens.push({ ...a, acesso });
    }
  }
  const aberta = itens.length > 0 && !fechada && !document.querySelector(".guia-veu"); // com o véu do guia só o balão e o alvo respondem (como Command.tsx)
  const marcado = Math.min(ativo, itens.length - 1);
  useEffect(() => { // some a fileira de perguntas enquanto a lista ocupa o lugar dela
    if (aberta) document.body.dataset.mencoes = ""; else delete document.body.dataset.mencoes;
    return () => { delete document.body.dataset.mencoes; };
  }, [aberta]);
  useLayoutEffect(() => {
    if (depois.current == null) return;
    input.current?.setSelectionRange(depois.current, depois.current);
    setCursor(depois.current);
    depois.current = null;
  });
  useEffect(() => { document.getElementById(`mencao-${marcado}`)?.scrollIntoView({ block: "nearest" }); }, [marcado, aberta]);

  const escolher = (a: ItemMencao) => {
    if (!token) return;
    const nova = `${text.slice(0, token.ini)}@${a.nome} `;
    depois.current = nova.length;
    setText(nova + text.slice(cursor));
    setFechada(false);
    setAtivo(0);
  };
  const aoTeclar = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (!aberta || e.nativeEvent.isComposing || e.keyCode === 229) return; // IME: Enter/Tab/setas são da composição
    if (e.key === "ArrowDown") { e.preventDefault(); setAtivo(Math.min(itens.length - 1, marcado + 1)); }
    else if (e.key === "ArrowUp") { e.preventDefault(); setAtivo(Math.max(0, marcado - 1)); }
    else if (e.key === "Enter" || e.key === "Tab") { e.preventDefault(); escolher(itens[marcado]); }
    else if (e.key === "Escape") { e.preventDefault(); setFechada(true); }
  };

  return (<>
    {aberta && <Mencoes aqui={here} itens={itens} ativo={marcado} onAtivo={setAtivo} onEscolher={escolher} />}
    <form className="askbar" data-here={here || undefined} role="search" aria-label="Conversa com o Piggy"
      data-guia="piggy.pergunta" data-dado={CHAT_REAL ? "real" : "exemplo"}
      onSubmit={async (e) => {
        e.preventDefault();
        const t = text.trim();
        if (!t || enviando.current) return;
        enviando.current = true;
        try {
          // Com "@" no texto o catálogo decide o destino: se ainda carrega, espera (falhou/404: fica sem dados e vai ao Piggy).
          let d = dados, u = uid;
          if (quer && !d) {
            try {
              if (!DEMO && u == null) u = (await qc.fetchQuery(contaQuery)).user_id;
              if (DEMO || uidValido(u)) d = await qc.fetchQuery(agentesQuery(u));
            } catch { /* sem catálogo: vai ao Piggy */ }
          }
          const m = d && (DEMO || u != null) ? mencaoDe(t, d) : null;
          if (m) {
            const acesso = acessoDe(d!, m.kind)!;
            if (!m.texto || !perguntarAoAgente({ kind: m.kind, original: t, texto: m.texto, uid: u ?? 0, acesso, energia: energiaDe(d!, m.kind) })) return; // só a menção, ou já há pergunta em voo a ele: o texto fica
          } else ask({ text: t });
          setText("");
        } finally { enviando.current = false; }
      }}>
      {avatar}
      <input ref={input} id="askbar-input" value={text} maxLength={500}
        onChange={(e) => { setText(e.target.value); setCursor(e.target.selectionStart ?? e.target.value.length); setFechada(false); setAtivo(0); }}
        onSelect={(e) => setCursor(e.currentTarget.selectionStart ?? 0)} onKeyDown={aoTeclar}
        role="combobox" aria-expanded={aberta} aria-controls="mencoes-lista" aria-autocomplete="list" aria-activedescendant={aberta ? `mencao-${marcado}` : undefined}
        placeholder="Converse com o Piggy…" aria-label="Pergunte ao Piggy" autoComplete="off" enterKeyHint="send" />
      <button type="submit" className="askbar-send" aria-label="Enviar" disabled={!text.trim()}>
        <i className="ph ph-arrow-up" aria-hidden="true" />
      </button>
    </form>
  </>);
}
