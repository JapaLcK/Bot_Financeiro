import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { motion } from "framer-motion";
import { ArrowDown, ArrowLeft, ArrowUp, ArrowUpRight, House, LayoutDashboard, Plus, Send, ThumbsDown, ThumbsUp, UsersRound, X } from "lucide-react";
import { cn } from "@/lib/utils";
import type { ChatAction, ChatId, ChatMessage, ChatView } from "@/chat/types";
import { ChatContent } from "./chat-content";

export type { ChatMessage } from "@/chat/types";

function Actions({ actions }: { actions?: ChatAction[] }) {
  return actions?.map((action, index) => <button key={`${action.label}-${index}`} type="button"
    className="pc-chat-action agent-chat-action" onClick={action.onClick} disabled={action.disabled}>
    <span>{action.label}</span><ArrowUpRight size={16} aria-hidden="true" />
  </button>);
}

function TypingIndicator({ reduced }: { reduced: boolean }) {
  return <span className="pc-typing" aria-hidden="true">{[0, 1, 2].map(i =>
    <motion.span key={i} animate={reduced ? { opacity: 0.7 } : { opacity: [0.45, 1, 0.45], y: [0, -3, 0] }}
      transition={{ duration: 0.9, repeat: reduced ? 0 : Infinity, delay: i * 0.14 }} />
  )}</span>;
}

function MessageBubble({ message, view, id, reduced }: {
  message: ChatMessage; view: ChatView; id: ChatId; reduced: boolean;
}) {
  const user = message.role === "user";
  const pending = message.state === "pending";
  const author = message.author || (user ? "Você" : message.state === "error" ? "Resposta não concluída" : view.title);
  return <motion.div initial={reduced ? false : { opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }}
    transition={{ duration: reduced ? 0 : 0.22, ease: [0.22, 1, 0.36, 1] }}
    className={cn("pc-chat-message pc-flex pc-w-full", user ? "pc-justify-end" : "pc-justify-start",
      id === "agent" ? `agent-chat-message agent-chat-${message.role}` : `piggy-msg ${message.role}`)}
    data-state={message.state || "complete"} aria-busy={pending || undefined}>
    {!user && <img className="pc-message-avatar" src={view.avatar} alt="" width="30" height="36" />}
    <div className={cn("pc-message-bubble", user && "pc-message-user")}>
      <b className="pc-message-author">{author}</b>
      {pending ? <><span className="pc-sr-only">{message.content}</span><TypingIndicator reduced={reduced} /></>
        : <p className="pc-message-content"><ChatContent content={message.content} markdown={message.markdown} /></p>}
      <Actions actions={message.actions} />
      {id === "agent" && !user && message.state === "complete" && message.feedback !== "dismissed" &&
        <div className="pc-agent-response-feedback" role="group" aria-label="Avaliar resposta">
          <span>{message.feedback ? "Obrigado pelo retorno nesta conversa" : "Esta resposta ajudou?"}</span>
          <div className="pc-agent-response-feedback-actions">
            {!message.feedback && <>
              <button type="button" aria-label="Sim, ajudou" onClick={() => view.onFeedback?.(message.id, "up")}><ThumbsUp size={16} /></button>
              <button type="button" aria-label="Não ajudou" onClick={() => view.onFeedback?.(message.id, "down")}><ThumbsDown size={16} /></button>
            </>}
            <button type="button" aria-label="Dispensar avaliação" onClick={() => view.onFeedback?.(message.id, "dismissed")}><X size={16} /></button>
          </div>
        </div>}
    </div>
  </motion.div>;
}

export function ChatMessages({ id, view, active, onClose }: {
  id: ChatId; view: ChatView; active: boolean; onClose: () => void;
}) {
  const prefix = id === "agent" ? "agent-chat" : "piggy";
  const [reduced, setReduced] = useState(() => window.matchMedia("(prefers-reduced-motion: reduce)").matches);
  const panelRef = useRef<HTMLElement>(null);
  const scrollRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const follow = useRef(true);
  const [scrolledAway, setScrolledAway] = useState(false);
  const [mobile, setMobile] = useState(() => window.matchMedia("(max-width: 600px), (max-width: 960px) and (pointer: coarse) and (orientation: landscape)").matches);
  const last = view.messages[view.messages.length - 1];
  const signature = `${view.messages.length}:${last?.id}:${last?.state}:${last?.content.length}`;

  useEffect(() => {
    const query = window.matchMedia("(max-width: 600px), (max-width: 960px) and (pointer: coarse) and (orientation: landscape)");
    const motionQuery = window.matchMedia("(prefers-reduced-motion: reduce)");
    const changed = () => setMobile(query.matches);
    const motionChanged = () => setReduced(motionQuery.matches);
    query.addEventListener("change", changed);
    motionQuery.addEventListener("change", motionChanged);
    return () => {
      query.removeEventListener("change", changed);
      motionQuery.removeEventListener("change", motionChanged);
    };
  }, []);

  useEffect(() => {
    if (!active || !mobile) return;
    const viewport = window.visualViewport;
    const resize = () => {
      const panel = panelRef.current;
      panel?.style.setProperty("--pc-viewport-height", `${viewport?.height || window.innerHeight}px`);
      panel?.style.setProperty("--pc-viewport-top", `${viewport?.offsetTop || 0}px`);
    };
    resize();
    viewport?.addEventListener("resize", resize);
    viewport?.addEventListener("scroll", resize);
    window.addEventListener("resize", resize);
    return () => {
      viewport?.removeEventListener("resize", resize);
      viewport?.removeEventListener("scroll", resize);
      window.removeEventListener("resize", resize);
    };
  }, [active, mobile]);

  useLayoutEffect(() => {
    if (!active || !inputRef.current) return;
    inputRef.current.style.height = "auto";
    inputRef.current.style.height = `${Math.min(id === "agent" ? 132 : 120, inputRef.current.scrollHeight)}px`;
  }, [active, id, view.draft]);

  useLayoutEffect(() => {
    if (active && follow.current && scrollRef.current) scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
  }, [active, signature]);

  function toBottom() {
    follow.current = true;
    setScrolledAway(false);
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: reduced ? "auto" : "smooth" });
  }

  function submit() {
    if (view.disabled || !view.draft.trim()) return;
    follow.current = true;
    setScrolledAway(false);
    view.onSend();
  }

  const input = <textarea ref={inputRef} id={`${prefix}-input`} value={view.draft} rows={1} maxLength={2000}
    placeholder={id === "agent" ? "Pergunte ao seu agente…" : `Converse com ${view.title}…`} disabled={view.disabled}
    onChange={event => view.onDraftChange(event.target.value)}
    onKeyDown={event => {
      if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) {
        event.preventDefault(); submit();
      }
    }} />;

  return <section ref={panelRef} id={`${prefix}-panel`} hidden={!active} role={id === "agent" ? "main" : "dialog"}
    aria-modal={id === "piggy" ? active && mobile : undefined} aria-labelledby={`${prefix}-title`}
    className={cn("pc-chat-panel pc-flex pc-flex-col pc-overflow-hidden", active && "open")}
    onKeyDown={event => {
      if (event.key === "Escape") { event.stopPropagation(); onClose(); }
      if (event.key !== "Tab" || !mobile) return;
      const controls = Array.from(panelRef.current?.querySelectorAll<HTMLElement>(
        'button:not(:disabled), textarea:not(:disabled), a[href]') || []).filter(el => el.getClientRects().length);
      const first = controls[0], final = controls[controls.length - 1];
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); final?.focus(); }
      else if (!event.shiftKey && document.activeElement === final) { event.preventDefault(); first?.focus(); }
    }}>
    {id === "agent" && <nav className="pc-agent-rail" aria-label="Navegação do chat">
      <a className="pc-agent-rail-brand" href="/home" aria-label="Início do PigBank"><img src="/brand/icon.png?v=2" alt="" /></a>
      <a className="pc-agent-rail-link" href="/home" aria-label="Início"><House size={20} /></a>
      <a className="pc-agent-rail-link" href="/app?view=overview" aria-label="Dashboard"><LayoutDashboard size={20} /></a>
      <button className="pc-agent-rail-link active" type="button" onClick={onClose} aria-label="Voltar aos agentes"><UsersRound size={20} /></button>
    </nav>}
    <div className={id === "agent" ? "pc-agent-page pc-flex pc-flex-col pc-min-h-0" : "pc-flex pc-flex-col pc-min-h-0 pc-flex-1"}>
    <div className="pc-chat-head pc-flex pc-items-center pc-gap-3">
      <img id={`${prefix}-avatar`} className="pc-head-avatar" src={view.avatar} alt="" width="44" height="50" />
      <div className="pc-min-w-0 pc-flex-1">
        <h2 id={`${prefix}-title`}>{view.title}</h2>
        <p id={`${prefix}-subtitle`}>{view.subtitle}</p>
      </div>
      <button id={`${prefix}-close`} className="pc-close" type="button" onClick={onClose} data-chat-close
        aria-label={id === "agent" ? "Voltar aos agentes" : "Fechar conversa"}>{id === "agent" ? <><ArrowLeft size={18} aria-hidden="true" /><span>Agentes</span></> : <X size={20} aria-hidden="true" />}</button>
    </div>
    <div className="pc-chat-history pc-relative pc-flex pc-min-h-0 pc-flex-1 pc-flex-col">
      <div ref={scrollRef} id={id === "piggy" ? "piggy-body" : "agent-chat-log"}
        className="pc-chat-log" role="log" aria-label="Mensagens da conversa" aria-live="polite" aria-relevant="additions text"
        onScroll={() => {
          const log = scrollRef.current!;
          follow.current = log.scrollHeight - log.clientHeight - log.scrollTop < 64;
          setScrolledAway(!follow.current);
        }}>
        {!view.messages.length && <div className="pc-chat-empty" id={id === "piggy" ? "piggy-empty" : undefined}>
          <img src={id === "agent" ? "/brand/icon.png?v=2" : view.avatar} alt="" width="76" height="84" />
          <h3>{id === "agent" ? view.greeting || "Olá!" : "Converse com o Piggy"}</h3>
          <p>{id === "agent" ? "Como eu posso te ajudar hoje?" : view.emptyText}</p>
          {id === "agent" && <small>{view.emptyText}</small>}
          {id === "piggy" && <div className="pc-suggestions"><Actions actions={view.suggestions} /></div>}
        </div>}
        {view.messages.map(message => <MessageBubble key={message.id} message={message} view={view} id={id} reduced={reduced} />)}
      </div>
      {scrolledAway && <button type="button" className="pc-latest" onClick={toBottom} aria-label="Ir para as mensagens recentes">
        <ArrowDown size={16} aria-hidden="true" /><span>Mensagens recentes</span>
      </button>}
    </div>
    {id === "agent" && !view.messages.length && <div className="pc-agent-suggestions" aria-label="Perguntas dos agentes"><Actions actions={view.suggestions} /></div>}
    <div className="pc-chat-feedback">
      <p id={`${prefix}-status`} role="status">{view.status}</p>
      <div id={`${prefix}-actions`}><Actions actions={view.actions} /></div>
    </div>
    <form id={`${prefix}-form`} className="pc-chat-foot" onSubmit={event => { event.preventDefault(); submit(); }}>
      <label className="pc-sr-only" htmlFor={`${prefix}-input`}>Sua mensagem para {view.title}</label>
      <div className={cn("pc-chat-compose", id === "agent" ? "pc-agent-compose" : "pc-flex pc-items-end pc-gap-2")}>
        {input}
        {id === "agent" ? <div className="pc-agent-compose-actions">
          <button className="pc-agent-add" type="button" disabled title="Anexos em breve" aria-label="Adicionar anexo (em breve)"><Plus size={20} /></button>
          <motion.button id={`${prefix}-send`} type="submit" className="pc-chat-send" aria-label="Enviar mensagem"
            disabled={view.disabled || !view.draft.trim()} whileTap={reduced ? undefined : { scale: 0.94 }}><ArrowUp size={21} aria-hidden="true" /></motion.button>
        </div> : <motion.button id={`${prefix}-send`} type="submit" className="pc-chat-send" aria-label="Enviar mensagem"
          disabled={view.disabled || !view.draft.trim()} whileTap={reduced ? undefined : { scale: 0.94 }}><Send size={19} aria-hidden="true" /></motion.button>}
      </div>
      <p id={`${prefix}-usage`} className="pc-chat-usage" data-tone={view.usageTone || "normal"}>{view.usage}</p>
    </form>
    </div>
  </section>;
}

export default ChatMessages;
