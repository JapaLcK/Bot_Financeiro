import { useEffect, useRef, useState, type ReactNode } from "react";
import { useQuery } from "@tanstack/react-query";
import { ask, useConversation, type Msg } from "../lib/conversation";
import { BY_PROFILE, COMMON } from "../lib/prompts.js";
import type { TopicId } from "../lib/topics";
import { FrameScope } from "./Frame";
import { LiveAnswer } from "./LiveAnswer";
import { ICON } from "../lib/brand";
import { CHAT_REAL, DEMO, perfilQuery } from "../lib/v2";
import { Demonstracao } from "./Selos";

type Prompt = { key: string; ask: string | null; topic?: TopicId; cat?: string };

// Resposta do Piggy: texto, os blocos (vivos, com o estado próprio da resposta) e as
// sugestões de próxima pergunta.
function PiggySays({ m, live = true }: { m: Msg; live?: boolean }) {
  return (
    <FrameScope.Provider value={`m${m.id}-`}>
      <p className="msg-by"><img src={ICON} alt="" width={24} height={24} />Piggy</p>
      <p className="msg-text">{m.text}</p>
      {m.blocks?.length && m.s0 && m.page ? <LiveAnswer blocks={m.blocks} s0={m.s0} page={m.page} /> : null}
      {live && !!m.follow?.length && (
        <ul className="chat-follow" aria-label="Próximas perguntas">
          {m.follow.map((f) => <li key={f.label}><button type="button" className="chip" onClick={() => ask({ text: f.label, topic: f.topic, cat: f.cat, key: f.key })}>{f.label}</button></li>)}
        </ul>
      )}
    </FrameScope.Provider>
  );
}

export function PiggyChat() {
  const msgs = useConversation();
  // O perfil é o do Board: com backend, o do servidor; no protótipo, o que main.tsx semeou do navegador.
  const perfil = useQuery(perfilQuery);
  const end = useRef<HTMLLIElement>(null);
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

  const head = (
    <header className="page-head">
      <h1 id="page-title" tabIndex={-1}>Converse com o Piggy{!DEMO && !CHAT_REAL && <Demonstracao />}</h1>
      <p className="page-lede">Pergunte sobre gastos, fatura, metas e investimentos. A resposta vem com os seus números.</p>
    </header>
  );

  if (!msgs.length) {
    const mine = ((BY_PROFILE as Record<string, Prompt[]>)[perfil.data?.perfil ?? "padrao"] ?? []);
    // Enquanto o perfil carrega, sem sugestões: os chips não trocam embaixo do dedo.
    const ideas = perfil.isPending ? [] : [...mine, ...(COMMON as Prompt[])].filter((p) => p.ask && p.topic).slice(0, 5);
    // O chip que o guia destaca (parts/Guia.tsx): o do gasto do mês, se estiver entre as sugestões.
    const guia = Math.max(0, ideas.findIndex((p) => p.key === "normal"));
    return (
      <>
        {head}
        <section className="chat-empty">
          <img src={ICON} alt="" width={56} height={56} />
          <p className="chat-hello">Oi, eu sou o Piggy. Pergunta o que quiser sobre o seu dinheiro, ou começa por uma destas:</p>
          <ul className="chat-follow">
            {ideas.map((p, i) => <li key={p.key}><button type="button" className="chip" data-guia={i === guia ? "piggy.chip" : undefined} onClick={() => ask({ text: p.ask!, topic: p.topic, cat: p.cat, key: p.key })}>{p.ask}</button></li>)}
          </ul>
        </section>
      </>
    );
  }

  return (
    <>
      {head}
      {/* A resposta nova entra acima da barra, onde o foco fica: o leitor de tela a lê daqui. */}
      <p className="sr-only" role="status">{said}</p>
      <ol className="chat" aria-label="Conversa">
        {msgs.map((m, i) => (
          <li key={m.id} ref={i === msgs.length - 2 ? end : undefined} className={m.role === "user" ? "msg-user" : "msg-piggy"}>
            {m.role === "user" ? m.text : <PiggySays m={m} live={i === msgs.length - 1} />}
          </li>
        ))}
      </ol>
    </>
  );
}
