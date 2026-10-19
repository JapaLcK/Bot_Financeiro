import { useEffect, useRef, useState, type ReactNode } from "react";
import { focusManager, useQuery } from "@tanstack/react-query";
import { useQueryClient } from "@tanstack/react-query";
import { ask, ativarEResponder, perguntarAoAgente, tentarDeNovo, useConversation, usePerguntando, type Msg } from "../lib/conversation";
import { agenteDe, agentesQuery, mencoes } from "../lib/agentes";
import { BY_PROFILE, COMMON } from "../lib/prompts.js";
import type { TopicId } from "../lib/topics";
import { FrameScope } from "./Frame";
import { LiveAnswer } from "./LiveAnswer";
import { ICON, agenteImg } from "../lib/brand";
import { DEMO, contaQuery, perfilQuery } from "../lib/v2";
import { Demonstracao } from "./Selos";
import { Anuncio } from "./InvestidoResposta";

const TRAVA_MS = 500; // a fileira nova ignora o toque por esse tempo: mais que o intervalo de um duplo toque, menos que ler a resposta
type Prompt = { key: string; ask: string | null; topic?: TopicId; cat?: string };

// A bolha do usuário numa conversa com agente: o texto original, com cada menção válida em rosa.
function ComMencoes({ t }: { t: string }) {
  let i = 0;
  const partes: ReactNode[] = [];
  for (const m of mencoes(t)) {
    partes.push(t.slice(i, m.ini), <span key={m.ini} className="mencao">{t.slice(m.ini, m.fim)}</span>);
    i = m.fim;
  }
  return <>{partes}{t.slice(i)}</>;
}

const ETIQUETA = { inativo: "NÃO ATIVO", sem_energia: "SEM ENERGIA" } as const;
// O último estado anunciado de cada resposta, fora do componente: sair do /piggy e voltar não anuncia de novo a conversa
// inteira; uma resposta que terminou longe do chat é anunciada ao voltar (Codex no #1157).
const estados = new Map<number, string>();
// O que falta anunciar, com o id da resposta: também fora do componente, para não se perder se o chat desmonta antes dos 60 ms.
// Ao montar, sai o que não é mais da conversa (a sessão trocou de conta com o chat fechado: a resposta era da anterior; Codex no #1157).
const fila: { id: number; text: ReactNode }[] = [];

// Resposta do Piggy ou de um agente: quem fala (avatar, nome, etiqueta), o texto e os blocos (vivos, com o estado
// próprio da resposta). Só o exemplo leva a linha do selo; as próximas perguntas moram na fileira de cima da barra (PiggyChat).
function PiggySays({ m }: { m: Msg }) {
  const qc = useQueryClient();
  const a = m.agente ? agenteDe(m.agente) : undefined;
  const etiqueta = a && (ETIQUETA[m.estado as keyof typeof ETIQUETA] ?? "AGENTE");
  return (
    <FrameScope.Provider value={`m${m.id}-`}>
      <p className="msg-autor"><img src={a ? agenteImg(a.kind) : ICON} alt="" width={22} height={22} /><b>{a ? a.nome : "Piggy"}</b>{etiqueta && <span className="msg-tag">{etiqueta}</span>}</p>
      {!DEMO && !m.real && <p className="msg-by"><Demonstracao /></p>}
      <p className={a ? "msg-text msg-pre" : "msg-text"} aria-busy={m.estado === "enviando" || undefined}>{m.text}</p>
      {m.estado === "inativo" && (
        <div className="msg-acoes">
          <button type="button" className="btn btn-primary" disabled={m.ativando} onClick={() => void ativarEResponder(m.id, () => qc.invalidateQueries({ queryKey: agentesQuery(m.uid).queryKey }))}>{m.ativando ? "Ativando…" : "Ativar e responder"}</button>
          {m.falhaAtivar && <p className="msg-text" role="alert">Não foi possível ativar o agente. Tente novamente.</p>}
        </div>
      )}
      {m.estado === "erro" && m.repetir && <div className="msg-acoes"><button type="button" className="btn btn-ghost" onClick={() => tentarDeNovo(m.id)}>Tentar de novo</button></div>}
      {(m.estado === "sem_plano" || (m.estado === "sem_energia")) && <div className="msg-acoes"><a className="btn btn-primary" href="/precos">Ver planos</a></div>}
      {m.estado === "pronto" && !!m.redirects?.length && (
        <div className="msg-acoes">
          {m.redirects.map((r, i) => <button key={i} type="button" className="btn btn-ghost" onClick={() => perguntarAoAgente({ kind: r.kind, original: r.question, texto: r.question, uid: m.uid!, acesso: r.access === "ready" ? "ready" : r.access === "no_energy" ? "no_energy" : r.access === "upgrade" ? "upgrade" : "activate" })}>Perguntar ao {agenteDe(r.kind)!.nome}</button>)}
        </div>
      )}
      {m.card}
      {m.blocks?.length && m.s0 && m.page ? <LiveAnswer blocks={m.blocks} s0={m.s0} page={m.page} /> : null}
    </FrameScope.Provider>
  );
}

export function PiggyChat() {
  const msgs = useConversation();
  const perguntando = usePerguntando();
  // O perfil é o do Board: com backend, o do servidor; no protótipo, o que main.tsx semeou do navegador.
  const perfil = useQuery(perfilQuery);
  // Nome como no app (nomeDe): nome → parte do e-mail antes do @ → "Você". Sem a conta lida (carregando, erro, protótipo), só "Olá".
  // Relida também com a conversa aberta e na volta do foco: se a sessão virou outra conta noutra aba, a leitura chega ao
  // `contaDaSessao` (main.tsx) e a conversa da anterior some. O corpo vem do servidor: o que não é texto vira vazio (como conta/Ponte.tsx).
  const contaQ = useQuery(contaQuery), conta = contaQ.data;
  // Na volta do foco a conta é relida, e até chegar uma leitura mais nova que o foco a conversa fica escondida (sem sair do
  // DOM: a rolagem fica), sem leitor de tela e sem clique — a sessão pode ter virado outra conta noutra aba (Codex no #1157).
  // Só o foco esconde: as releituras do envio (`ask`, a menção) não piscam a conversa.
  const [relendo, setRelendo] = useState(false);
  const relendoRef = useRef(false);
  relendoRef.current = relendo;
  const { refetch } = contaQ;
  useEffect(() => DEMO ? undefined : focusManager.subscribe((focado) => {
    if (!focado) return;
    relendoRef.current = true; // já, antes do render: um anúncio que dispare neste meio tempo também espera
    setRelendo(true);
    void refetch({ cancelRefetch: false }).finally(() => setRelendo(false)); // reaproveita uma leitura em voo
  }), [refetch]);
  const reconferindo = !DEMO && msgs.length > 0 && relendo;
  // A conversa só aparece depois de uma leitura do perfil feita NESTA montagem e bem-sucedida: se a sessão virou outra conta
  // enquanto o chat estava fechado, o `contaDaSessao` a limpa antes de ela ser mostrada; sem conseguir conferir, não mostra (Codex no #1157).
  const conferida = DEMO || (contaQ.isFetchedAfterMount && contaQ.isSuccess);
  const txt = (v: unknown) => (typeof v === "string" ? v.trim() : "");
  const nome = conta && (txt(conta.display_name) || txt(conta.email).split("@")[0] || "Você");
  const end = useRef<HTMLLIElement>(null);
  // Quando as próximas perguntas entraram: elas aparecem no lugar das que acabaram de ser tocadas, e o 2º toque de um duplo toque cairia numa delas.
  const desde = useRef(-Infinity);
  useEffect(() => { if (msgs.length) desde.current = performance.now(); }, [msgs.length]);
  // A pergunta nova aparece no topo da tela (a resposta dela vem logo abaixo).
  useEffect(() => { end.current?.scrollIntoView({ block: "start" }); }, [msgs.length]);
  // Esvazia e preenche de novo a cada resposta: duas respostas iguais seguidas (o texto
  // livre no protótipo) também são anunciadas.
  const [said, setSaid] = useState<ReactNode>(null);
  // Anuncia a resposta que acabou de mudar de estado, não só a última: com dois agentes em voo, a mais
  // antiga pode terminar enquanto a mais nova ainda está "Preparando…" (Codex no #1157).
  const timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  useEffect(() => () => clearTimeout(timer.current), []);
  // Respostas que terminam juntas (menos de 60 ms) saem no MESMO anúncio, em ordem: uma não apaga a outra (Codex no #1157).
  // Durante a releitura da volta do foco nada é anunciado: a resposta pode ser da conta que a sessão deixou de ser (Codex no #1157).
  const anunciar = () => {
    setSaid(null);
    clearTimeout(timer.current);
    timer.current = setTimeout(() => {
      timer.current = undefined;
      if (relendoRef.current) return; // fica na fila até a conta ser conferida
      const textos = fila.splice(0);
      setSaid(<>{textos.map((t, i) => <span key={t.id}>{i ? " " : ""}{t.text}</span>)}</>);
    }, 60);
  };
  useEffect(() => {
    if (!msgs.length) { // conversa limpa (troca de conta): o anúncio pendente ou o último dito eram dela e não podem reaparecer (Codex no #1157)
      clearTimeout(timer.current);
      timer.current = undefined;
      fila.length = 0;
      setSaid(null);
      return;
    }
    for (let i = fila.length; i--;) if (!msgs.some((m) => m.id === fila[i].id)) fila.splice(i, 1);
    let novas = 0;
    for (const m of msgs) {
      if (m.role !== "piggy") continue;
      const agora = m.estado ?? "pronto";
      if (estados.get(m.id) === agora) continue;
      estados.set(m.id, agora);
      if (agora !== "enviando") { fila.push({ id: m.id, text: m.text ?? null }); novas++; }
    }
    // Sem cleanup aqui: uma bolha de outro agente entrando em "Preparando…" não cancela o anúncio pendente. Sem novidade, só sai
    // a fila que sobrou de uma desmontagem (a resposta terminou e o usuário saiu antes dos 60 ms).
    if (!novas && (timer.current !== undefined || !fila.length)) return;
    anunciar();
  }, [msgs]); // `anunciar` só lê refs e o setState
  // Terminada a releitura da volta do foco, sai o que esperou por ela (se a conta mudou, a limpeza já esvaziou a fila).
  useEffect(() => { if (!relendo && fila.length && timer.current === undefined) anunciar(); }, [relendo]);

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
    <ul key={msgs.length} className="chat-follow" aria-label={msgs.length ? "Próximas perguntas" : "Sugestões"} inert={reconferindo} data-reconferindo={reconferindo || undefined}>
      {chips.map((c) => <li key={c.label}><button type="button" className="chip" data-guia={c.guia ? "piggy.chip" : undefined} onClick={() => { if (performance.now() - desde.current >= TRAVA_MS) ask({ text: c.label, topic: c.topic, cat: c.cat, key: c.key }); }}>{c.label}</button></li>)}
    </ul>
  );

  // Esperando a conta: sem conversa ainda (1ª pergunta, de outra página), ou com ela ainda não conferida nesta montagem. Com a
  // conversa já conferida à vista, a pergunta nova não a esconde (piscaria a cada envio).
  if ((perguntando && !msgs.length) || (msgs.length && !conferida)) {
    return (
      <>
        {head}
        <section className="chat-espera" aria-busy={!contaQ.isError || undefined}>
          <p>{contaQ.isError && !perguntando ? "Não foi possível confirmar sua conta. Recarregue a página." : "Carregando a conversa…"}</p>
        </section>
      </>
    );
  }

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
      {/* Na releitura da volta do foco a região sai da árvore de acessibilidade sem mudar o conteúdo: voltar não relê a última resposta. */}
      <p className="sr-only" role="status" hidden={reconferindo}><Anuncio.Provider value>{said}</Anuncio.Provider></p>
      <ol className="chat" aria-label="Conversa" inert={reconferindo} data-reconferindo={reconferindo || undefined}>
        {msgs.map((m, i) => (
          <li key={m.id} ref={i === msgs.length - 2 ? end : undefined} className={m.role === "user" ? "msg-user" : "msg-piggy"}
            data-dado={m.role === "user" ? undefined : m.real && !DEMO ? "real" : "exemplo"}>
            {m.role === "piggy" ? <PiggySays m={m} /> : m.agente && typeof m.text === "string" ? <ComMencoes t={m.text} /> : m.text}
          </li>
        ))}
      </ol>
      {fileira}
    </>
  );
}
