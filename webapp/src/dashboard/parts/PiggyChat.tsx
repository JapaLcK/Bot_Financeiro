import { useEffect, useRef, useState, type ReactNode } from "react";
import { useQuery } from "@tanstack/react-query";
import { ask, useConversation, type Msg } from "../lib/conversation";
import { BY_PROFILE, COMMON } from "../lib/prompts.js";
import type { TopicId } from "../lib/topics";
import { FrameScope } from "./Frame";
import { LiveAnswer } from "./LiveAnswer";
import { ICON } from "../lib/brand";
import { DEMO, contaQuery, perfilQuery } from "../lib/v2";
import { Demonstracao } from "./Selos";
import { Anuncio } from "./InvestidoResposta";

const TRAVA_MS = 500; // a fileira nova ignora o toque por esse tempo: mais que o intervalo de um duplo toque, menos que ler a resposta
type Prompt = { key: string; ask: string | null; topic?: TopicId; cat?: string };

// Resposta do Piggy: texto e os blocos (vivos, com o estado próprio da resposta). Só o exemplo leva a
// linha do selo; as próximas perguntas moram na fileira de cima da barra (PiggyChat).
function PiggySays({ m }: { m: Msg }) {
  return (
    <FrameScope.Provider value={`m${m.id}-`}>
      <span className="sr-only">Piggy: </span>
      {!DEMO && !m.real && <p className="msg-by"><Demonstracao /></p>}
      <p className="msg-text">{m.text}</p>
      {m.card}
      {m.blocks?.length && m.s0 && m.page ? <LiveAnswer blocks={m.blocks} s0={m.s0} page={m.page} /> : null}
    </FrameScope.Provider>
  );
}

export function PiggyChat() {
  const msgs = useConversation();
  // O perfil é o do Board: com backend, o do servidor; no protótipo, o que main.tsx semeou do navegador.
  const perfil = useQuery(perfilQuery);
  // Nome como no app (nomeDe): nome → parte do e-mail antes do @ → "Você". Sem a conta lida (carregando, erro, protótipo), só "Olá".
  // Só no estado vazio: com a conversa aberta o nome não aparece, e a leitura iria à toa. O corpo vem do servidor: o que não é texto vira vazio (como conta/Ponte.tsx).
  const conta = useQuery({ ...contaQuery, enabled: contaQuery.enabled && !msgs.length }).data;
  const txt = (v: unknown) => (typeof v === "string" ? v.trim() : "");
  const nome = conta && (txt(conta.display_name) || txt(conta.email).split("@")[0] || "Você");
  const end = useRef<HTMLLIElement>(null);
  // Quando as próximas perguntas entraram: elas aparecem no lugar das que acabaram de ser tocadas, e o 2º toque de um duplo toque cairia numa delas.
  const desde = useRef(-Infinity);
  useEffect(() => { if (msgs.length) desde.current = performance.now(); }, [msgs.length]);
  // A pergunta nova aparece no topo da tela (a resposta dela vem logo abaixo).
  useEffect(() => { end.current?.scrollIntoView({ block: "start" }); }, [msgs.length]);
  const last = [...msgs].reverse().find((m) => m.role === "piggy");
  // Esvazia e preenche de novo a cada resposta: duas respostas iguais seguidas (o texto
  // livre no protótipo) também são anunciadas.
  const [said, setSaid] = useState<ReactNode>(null);
  useEffect(() => {
    setSaid(null);
    const t = setTimeout(() => setSaid(last?.text ?? null), 60);
    return () => clearTimeout(t);
  }, [msgs.length]);

  // Título só para o leitor de tela e o foco da rota (App.tsx); o que se vê é a conversa.
  const head = <h1 id="page-title" tabIndex={-1} className="sr-only">Converse com o Piggy</h1>;

  const mine = ((BY_PROFILE as Record<string, Prompt[]>)[perfil.data?.perfil ?? "padrao"] ?? []);
  // Enquanto o perfil carrega, sem sugestões: os chips não trocam embaixo do dedo.
  const ideas = perfil.isPending ? [] : [...mine, ...(COMMON as Prompt[])].filter((p) => p.ask && p.topic).slice(0, 5);
  // O chip que o guia destaca (parts/Guia.tsx): o do gasto do mês, se estiver entre as sugestões.
  const guia = Math.max(0, ideas.findIndex((p) => p.key === "normal"));
  const lastMsg = msgs[msgs.length - 1];
  const chips = !msgs.length
    ? ideas.map((p, i) => ({ key: p.key, label: p.ask!, topic: p.topic, cat: p.cat, guia: i === guia }))
    : (lastMsg.follow ?? []).map((f) => ({ key: f.key, label: f.label, topic: f.topic, cat: f.cat, guia: false }));
  // Uma fileira fixa acima da barra (chat.css), com as sugestões do chat vazio ou as próximas perguntas da última resposta.
  // `key` por resposta: a fileira nova começa do início, sem herdar a rolagem lateral da anterior (Codex no #1136).
  const fileira = !!chips.length && (
    <ul key={msgs.length} className="chat-follow" aria-label={msgs.length ? "Próximas perguntas" : "Sugestões"}>
      {chips.map((c) => <li key={c.label}><button type="button" className="chip" data-guia={c.guia ? "piggy.chip" : undefined} onClick={() => { if (performance.now() - desde.current >= TRAVA_MS) ask({ text: c.label, topic: c.topic, cat: c.cat, key: c.key }); }}>{c.label}</button></li>)}
    </ul>
  );

  if (!msgs.length) {
    return (
      <>
        {head}
        <section className="chat-empty">
          <img src={ICON} alt="" width={56} height={56} />
          <h2>{nome ? `Olá, ${nome}` : "Olá"}</h2>
          <p>Como posso te ajudar?</p>
        </section>
        {fileira}
      </>
    );
  }

  return (
    <>
      {head}
      {/* A resposta nova entra acima da barra, onde o foco fica: o leitor de tela a lê daqui. */}
      <p className="sr-only" role="status"><Anuncio.Provider value>{said}</Anuncio.Provider></p>
      <ol className="chat" aria-label="Conversa">
        {msgs.map((m, i) => (
          <li key={m.id} ref={i === msgs.length - 2 ? end : undefined} className={m.role === "user" ? "msg-user" : "msg-piggy"}
            data-dado={m.role === "user" ? undefined : m.real && !DEMO ? "real" : "exemplo"}>
            {m.role === "user" ? m.text : <PiggySays m={m} />}
          </li>
        ))}
      </ol>
      {fileira}
    </>
  );
}
