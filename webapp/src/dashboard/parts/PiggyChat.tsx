import { useEffect, useRef, useState, type ReactNode } from "react";
import { PLAN } from "../lib/api";
import { ask, useConversation, type Msg } from "../lib/conversation";
import { locked, readProfile } from "../lib/profiles.js";
import { BY_PROFILE, COMMON } from "../lib/prompts.js";
import { answer, type TopicId } from "../lib/topics";
import { FrameScope } from "./Frame";

type Prompt = { key: string; ask: string | null; topic?: TopicId; cat?: string };
const AVATAR = "../frontend/brand/icon.png";

// Bloco estático da resposta. `inert` tira tudo; onde ele não existe (Safari < 15.5), cada
// caminho que aciona um controle tem a sua trava:
//   ponteiro (clique, toque, hover)       → CSS .msg-block > * { pointer-events: none }
//   Tab                                   → tabindex=-1 nos controles
//   clique do leitor de tela (VoiceOver)  → onClickCapture
//   foco programático (dispara onFocus)   → onFocusCapture
//   tecla com o foco dentro               → onKeyDownCapture
// O PR dos blocos que expandem troca isto por blocos vivos.
const FOCUSABLE = "a[href], button, input, select, textarea, [tabindex]";
function Snapshot({ children }: { children: ReactNode }) {
  const box = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if ("inert" in HTMLElement.prototype) return;
    box.current?.querySelectorAll<HTMLElement>(FOCUSABLE).forEach((el) => el.setAttribute("tabindex", "-1"));
  });
  const stop = (e: { stopPropagation: () => void; preventDefault?: () => void }) => { e.preventDefault?.(); e.stopPropagation(); };
  return (
    <div className="panel msg-block" inert ref={box} onClickCapture={stop} onKeyDownCapture={stop}
      onFocusCapture={(e) => e.stopPropagation()}>{children}</div>
  );
}

// Resposta do Piggy: texto, os blocos (uma foto do estado na hora da pergunta; ficam
// estáticos até o PR dos blocos que expandem) e as sugestões de próxima pergunta.
function PiggySays({ m, live = true }: { m: Msg; live?: boolean }) {
  return (
    <FrameScope.Provider value={`m${m.id}-`}>
      <p className="msg-by"><img src={AVATAR} alt="" width={24} height={24} />Piggy</p>
      <p className="msg-text">{m.text}</p>
      {m.blocks?.map((b, i) => <Snapshot key={i}>{b}</Snapshot>)}
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
      <h1 id="page-title" tabIndex={-1}>Converse com o Piggy</h1>
      <p className="page-lede">Pergunte sobre gastos, fatura, metas e investimentos. A resposta vem com os seus números.</p>
    </header>
  );

  if (locked("piggy", PLAN)) {
    const sample: Msg = { id: 0, role: "piggy", ...answer("categoria", "delivery") };
    return (
      <>
        {head}
        <section className="chat-plus" aria-label="Exemplo de conversa">
          <p className="msg-user">Quanto eu gastei com delivery?</p>
          <div className="msg-piggy"><PiggySays m={sample} live={false} /></div>
          <div className="chat-plus-cta">
            <p><b>Disponível no Plus.</b> O Piggy responde com os seus números e mostra os gráficos na conversa.</p>
            <a className="btn btn-primary" href="../frontend/precos.html">Conhecer o Plus</a>
          </div>
        </section>
      </>
    );
  }

  if (!msgs.length) {
    const profile = readProfile() ?? "padrao";
    const mine = ((BY_PROFILE as Record<string, Prompt[]>)[profile] ?? []);
    const ideas = [...mine, ...(COMMON as Prompt[])].filter((p) => p.ask && p.topic).slice(0, 5);
    return (
      <>
        {head}
        <section className="chat-empty">
          <img src={AVATAR} alt="" width={56} height={56} />
          <p className="chat-hello">Oi, eu sou o Piggy. Pergunta o que quiser sobre o seu dinheiro, ou começa por uma destas:</p>
          <ul className="chat-follow">
            {ideas.map((p) => <li key={p.key}><button type="button" className="chip" onClick={() => ask({ text: p.ask!, topic: p.topic, cat: p.cat, key: p.key })}>{p.ask}</button></li>)}
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
