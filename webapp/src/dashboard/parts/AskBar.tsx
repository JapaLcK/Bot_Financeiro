import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { ask, perguntarAoAgente } from "../lib/conversation";
import type { Path } from "../router";
import { ICON } from "../lib/brand";
import { AGENTES, acessoDe, agentesQuery, energiaDe, mencaoDe, mencoes, semAcento, tokenEm } from "../lib/agentes";
import { uidValido } from "../conta/api";
import { DEMO, contaQuery, lerContaFresca, useChatIa } from "../lib/v2";
import { pararIa, useIa } from "../lib/conversaIa";
import { GRUPO, GRUPOS, Mencoes, type ItemMencao } from "./Mencoes";

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
  const chatIa = useChatIa(), ia = useIa(), parando = chatIa && ia.ocupado; // com um turno da IA em voo o botão para a resposta; com a cota cheia o Enviar desliga (aviso em PiggyChat); o `@agente` divide a mesma cota (agent_chat.py), então também fica desligado
  // Trocar de página fecha a lista (como o Esc): no celular a barra some fora do /piggy e a lista ficaria solta sobre a página nova.
  const [rota, setRota] = useState(path);
  if (rota !== path) { setRota(path); setFechada(true); }
  const avatar = <img src={ICON} alt="" width={28} height={28} />;

  // O catálogo só é lido quando há um "@" em edição: sem ele, nenhuma requisição a /auth nem a /agents.
  // Também com a menção já escrita fora do cursor (texto colado): quem digita o nome inteiro passa pelo token, mas o catálogo tem de estar lido no envio.
  const token = tokenEm(text, cursor), quer = /(^|\s)@/.test(text);
  const conta = useQuery({ ...contaQuery, enabled: contaQuery.enabled && quer });
  // Enquanto a conta é relida, nada do cache: se a sessão virou outra noutra aba, a lista mostraria os agentes da anterior.
  // Nem depois de uma releitura que falhou: o dado guardado ainda seria o da conta anterior (Codex no #1157).
  const uid = conta.isFetching || conta.isError ? undefined : conta.data?.user_id;
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
  // Tocar fora da lista e do campo a fecha, como o Esc (Codex no #1157). Tocar num item não chega aqui como "fora", e o campo não perde o foco (Mencoes.tsx).
  useEffect(() => {
    if (!aberta) return;
    const fora = (e: PointerEvent) => { const t = e.target as Element | null; if (t !== input.current && !t?.closest?.(".mencoes")) setFechada(true); };
    document.addEventListener("pointerdown", fora);
    return () => document.removeEventListener("pointerdown", fora);
  }, [aberta]);
  useLayoutEffect(() => {
    if (depois.current == null) return;
    input.current?.setSelectionRange(depois.current, depois.current);
    setCursor(depois.current);
    depois.current = null;
  });
  useEffect(() => { document.getElementById(`mencao-${marcado}`)?.scrollIntoView({ block: "nearest" }); }, [marcado, aberta]);

  // Troca o token inteiro, também o que está depois do cursor ("@Det|etive"): só as letras do nome (as mesmas do `tokenEm`), não a
  // pontuação colada, mais as palavras seguintes de um nome composto que já estejam lá ("@Far|ia Limer"), e não dobra o espaço (Codex no #1157).
  const trocaToken = (nome: string, t: string, c: number, ini: number) => {
    let fim = c + /^[\p{L}\p{M}]*/u.exec(t.slice(c))![0].length;
    for (const w of nome.split(" ").slice(1)) {
      const p = /^\s([\p{L}\p{M}]+)/u.exec(t.slice(fim));
      if (!p || semAcento(p[1]) !== semAcento(w)) break;
      fim += p[0].length;
    }
    return `${t.slice(0, ini)}@${nome} ${t.slice(fim).replace(/^\s/, "")}`;
  };
  const escolher = (a: ItemMencao) => {
    if (!token) return;
    depois.current = token.ini + a.nome.length + 2;
    setText(trocaToken(a.nome, text, cursor, token.ini));
    setFechada(false);
    setAtivo(0);
  };
  // Um clique no painel lateral (parts/PainelChat.tsx) chama o agente: troca o token em edição; senão a menção do começo do texto; senão
  // põe "@Nome " antes. Nada que o usuário escreveu se apaga. Lê o texto por ref: o listener é um só e o updater do setText não tem efeito colateral.
  const est = useRef({ text, cursor });
  est.current = { text, cursor };
  useEffect(() => {
    const aoMencionar = (e: Event) => {
      const nome = (e as CustomEvent<{ nome?: unknown }>).detail?.nome;
      if (!AGENTES.some((a) => a.nome === nome)) return;
      const { text: t, cursor: c } = est.current, tk = tokenEm(t, c), m = mencoes(t)[0];
      const nova = tk ? trocaToken(nome as string, t, c, tk.ini)
        : m?.ini === 0 ? `@${nome}${t.slice(m.fim) || " "}`
        : `@${nome} ${t}`;
      depois.current = nova.length;
      setText(nova);
      setFechada(false);
      setAtivo(0);
      input.current?.focus();
    };
    window.addEventListener("dash:mencao", aoMencionar);
    return () => window.removeEventListener("dash:mencao", aoMencionar);
  }, []);
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
      data-guia="piggy.pergunta" data-dado={chatIa ? "real" : "exemplo"}
      onSubmit={async (e) => {
        e.preventDefault();
        const t = text.trim();
        if (!t || enviando.current) return;
        enviando.current = true;
        try {
          // Com "@" no texto o catálogo decide o destino: se ainda carrega, espera (falhou/404: fica sem dados e vai ao Piggy).
          // A conta pode ter mudado noutra aba com a anterior ainda no cache: o envio com menção relê o perfil (um fetch em voo é
          // reaproveitado) e só usa o catálogo dessa conta (Codex no #1157). A releitura passa pelo `contaDaSessao` antes da bolha nova.
          let d = dados, u = uid;
          if (quer) {
            try {
              if (!DEMO) u = (await lerContaFresca(qc)).user_id;
              // O catálogo também é relido: o plano ou um agente pode ter mudado noutra aba com a mesma conta (Codex no #1157). Uma leitura
              // em voo pode ter saído antes da mudança (o painel lateral a mantém viva): cancelada, para o envio sempre ler de novo.
              if (DEMO || uidValido(u)) await qc.cancelQueries({ queryKey: agentesQuery(u).queryKey, exact: true });
              d = DEMO || uidValido(u) ? await qc.fetchQuery(agentesQuery(u)) : undefined;
            } catch { d = undefined; /* sem conta conferida ou sem catálogo: vai ao Piggy */ }
          }
          const m = d && (DEMO || u != null) ? mencaoDe(t, d) : null;
          if (m) {
            const acesso = acessoDe(d!, m.kind)!;
            if (!m.texto || !perguntarAoAgente({ kind: m.kind, original: t, texto: m.texto, uid: u ?? 0, acesso, energia: energiaDe(d!, m.kind) })) return; // só a menção, ou já há pergunta em voo a ele: o texto fica
          } else if (!ask({ text: t })) return; // outra pergunta ainda confere a conta: o texto fica
          setText((agora) => (agora === text ? "" : agora)); // o que foi digitado durante a conferência fica (Codex no #1157)
        } finally { enviando.current = false; }
      }}>
      {avatar}
      <input ref={input} id="askbar-input" value={text} maxLength={500}
        onChange={(e) => { setText(e.target.value); setCursor(e.target.selectionStart ?? e.target.value.length); setFechada(false); setAtivo(0); }}
        onSelect={(e) => setCursor(e.currentTarget.selectionStart ?? 0)} onKeyDown={aoTeclar}
        onBlur={(e) => { if (e.relatedTarget) setFechada(true); }} // o foco foi a outro controle (teclado, leitor de tela); sem alvo é a janela ou um toque, que o pointerdown cobre
        role="combobox" aria-expanded={aberta} aria-controls="mencoes-lista" aria-autocomplete="list" aria-activedescendant={aberta ? `mencao-${marcado}` : undefined}
        placeholder="Converse com o Piggy…" aria-label="Pergunte ao Piggy" autoComplete="off" enterKeyHint="send" />
      {/* O mesmo botão nos dois papéis (o foco não se perde na troca). Sem ícone de "parar" no subset do Phosphor: o quadrado é CSS.
          Parar cancela o padrão do clique: o abort libera o turno antes do clique acabar, o botão já é "submit" e enviaria o texto do campo.
          `detail >= 2` é o 2º toque de um duplo clique no Enviar, que cai no Parar recém-trocado; teclado (Enter/Espaço) dá 0. */}
      <button type={parando ? "button" : "submit"} className="askbar-send" aria-label={parando ? "Parar resposta" : "Enviar"} disabled={!parando && (!text.trim() || (chatIa && ia.cheia))}
        onClick={parando ? (e) => { e.preventDefault(); if (e.detail < 2) pararIa(); } : undefined}>
        {parando ? <span className="askbar-stop" aria-hidden="true" /> : <i className="ph ph-arrow-up" aria-hidden="true" />}
      </button>
    </form>
  </>);
}
