import { createElement, useSyncExternalStore, type ReactNode } from "react";
import { go } from "../router";
import { DEMO, answer, type Answer, type TopicId } from "./topics";
import { agenteDe, ativar, cardDe, conversar, type Acesso, type CardAgente as Cartao, type Encaminhada, type Redirect } from "./agentes";
import { ErroApi, detalheDe, escrever, semSenha, sessaoDe } from "./v2";
import { PREPARANDO, SESSAO, abortarIa, enviarIa, iaBloqueada, type CamposIa, type Motivo } from "./conversaIa";
import { CardAgente } from "../parts/CardAgente";

// A conversa com o Piggy: vive enquanto a aba está aberta (troca de página não apaga;
// recarregar começa de novo). Guardar histórico é fase posterior, com backend.
// `parou` e `cota` são só da IA (lib/conversaIa.ts).
export type EstadoAgente = "enviando" | "pronto" | "erro" | "inativo" | "sem_energia" | "sem_plano" | "parou" | "cota";
export interface Msg extends Partial<Omit<Answer, "text">>, CamposIa {
  id: number; role: "user" | "piggy"; text: ReactNode;
  // Conversa com um agente (@): `agente` é o kind (na bolha do usuário, o que ela destaca); o resto é o estado da resposta.
  agente?: string; estado?: EstadoAgente; redirects?: Redirect[]; pergunta?: string; repetir?: boolean;
  cartao?: Cartao; // o descritor do card (card é o elemento): o índice do painel lateral lê o título dele
  uid?: number; custo?: number; livre?: number; ativando?: boolean; falhaAtivar?: boolean; encaminhada?: Encaminhada;
}

let msgs: Msg[] = [];
let seq = 0;
const subs = new Set<() => void>();

export const useConversation = () => useSyncExternalStore((f) => { subs.add(f); return () => subs.delete(f); }, () => msgs);

// Texto livre que pergunta do total investido ("quanto tenho aplicado?") ou das metas ("como
// estão minhas metas?", só leitura) vai para a resposta real do servidor; o resto, sem IA no painel,
// recebe os atalhos.
const semAcento = (t: string) => t.normalize("NFD").replace(/[\u0300-\u036f]/g, "").toLowerCase();
const investido = (t: string) => /investid|aplicad/.test(semAcento(t));
// Meta só LEITURA: com verbo de escrita ("cria uma meta", "apaga a meta", "quero uma meta nova") a escrita é no painel
// (ou na IA, Etapa 7), então a frase segue para os atalhos. Referência: `_VERBO_ESCRITA` de core/intent_classifier.py.
const ESCRITA = /\b(?:(?:cri|cadastr|registr|apag|delet|remov|mud|alter|edit|troc|aument|atualiz|mont|defin)(?:a|e|ar|er|ue|ir)|exclu(?:i|a|ir)|diminu(?:i|a|ir)|renome(?:ia|ie|ar)|faz(?:er)?)\b|\bquero (?:uma|outra) /;
const metas = (t: string) => { const n = semAcento(t); return /\bmetas?\b/.test(n) && !ESCRITA.test(n); };

// Confere a conta da sessão antes de uma pergunta nova entrar na conversa: main.tsx liga a uma leitura de /auth/dashboard-profile
// (que passa pelo `contaDaSessao`); no protótipo, nada a conferir.
let conferirConta: () => Promise<unknown> = () => Promise.resolve();
export const aoConferirConta = (f: () => Promise<unknown>) => { conferirConta = f; };
// O freio do chat com a IA (`chat_ia` do /me): main.tsx liga a leitura do cache e a releitura do /me. Desligado, `ask` é o de sempre (protótipo, assuntos reais, atalhos).
// A conta pode ter mudado na conferência, ou a troca ter sido percebida antes (foco) com o /me novo ainda em voo: o do cache é da anterior,
// então o freio que vale é o relido (falhou = desligado). `velho` diz que o /me do cache está nesse intervalo.
let chatIa: () => boolean = () => false;
let reler: () => Promise<boolean> = () => Promise.resolve(false);
let velho: () => boolean = () => false;
export const aoSaberChatIa = (f: () => boolean, releitura: () => Promise<boolean>, desatualizado: () => boolean) => { chatIa = f; reler = releitura; velho = desatualizado; };
let perguntando = false; // a conferência em voo segura um 2º toque (chip, faixa do Piggy) até a pergunta entrar
/** Há uma pergunta esperando a conta ser conferida: o chat mostra "Carregando a conversa…" em vez de nada (Codex no #1157). */
export const usePerguntando = () => useSyncExternalStore((f) => { subs.add(f); return () => subs.delete(f); }, () => perguntando);

/** Envia a pergunta e abre a conversa. Sem `topic`, o Piggy responde com os atalhos. A pergunta entra depois de conferir a conta:
 * se a sessão virou outra noutra aba, a conversa anterior some ANTES e a pergunta nova fica (Codex no #1157). Sem conseguir
 * conferir, entra assim mesmo: o PiggyChat não mostra a conversa sem conta conferida. */
export function ask(q: { text: string; topic?: TopicId | null; cat?: string | null; key?: string }) {
  if (perguntando) return false; // falso = a pergunta não entrou (o texto fica na barra)
  if (chatIa() && iaBloqueada()) { go("/piggy"); return false; } // um turno por vez, e sem cota não há o que enviar
  perguntando = true;
  const antes = dono;
  const conferindo = conferirConta().catch(() => {});
  subs.forEach((f) => f());
  go("/piggy"); // já: com a conta lenta, o chat mostra que a pergunta está a caminho
  void conferindo.then(async () => {
    // Ligado, TUDO vai à IA: texto livre, chip, faixa do Piggy; `topic`, `key` e os assuntos reais ficam de fora. O freio é lido DEPOIS da
    // conferência: se a conta mudou, o do cache era da anterior e mandaria a conta nova à IA sem ela estar na lista.
    const ia = chatIa() && (dono !== antes || velho()) ? await reler().catch(() => false) : chatIa(); // desligado no cache: o lado seguro, sem esperar o /me
    perguntando = false;
    if (ia) return perguntarIa(q.text);
    const topic = q.topic ?? (investido(q.text) ? "investido" : metas(q.text) ? "metas" : null);
    const a = topic ? answer(topic, q.cat ?? null, q.key) : DEMO;
    msgs = [...msgs, { id: ++seq, role: "user", text: q.text }, { id: ++seq, role: "piggy", ...a }];
    subs.forEach((f) => f());
  });
  return true;
}

/** O turno da IA: a bolha do usuário e a do Piggy entram já, a do Piggy se atualiza por id enquanto o stream chega (lib/conversaIa.ts).
 * Sem conta conferida (`dono` vazio) nada sai: o POST lê o usuário da sessão, e a conversa é de uma conta só. */
function perguntarIa(texto: string) {
  const bolha = ++seq, resp = ++seq;
  msgs = [...msgs, { id: bolha, role: "user", text: texto },
    { id: resp, role: "piggy", ia: true, real: true, estado: "enviando", text: PREPARANDO, passos: [], parcial: "", blocos: [] }];
  notifica();
  if (dono == null) { patch(resp, { estado: "erro", text: SESSAO }); return; }
  void enviarIa(texto, (p) => { if (naConversa(resp)) patch(resp, p); });
}

// --- Conversa com um agente (@Nome na barra) ---
// O context de cada agente (chave `uid:kind`: o servidor amarra o context ao usuário e ao agente, e a conta pode mudar noutra aba) e os que estão em voo vivem aqui, fora do React, com o `msgs`: a troca de página não os apaga.
const contextos = new Map<string, string | null>();
// chave `uid:kind` → id da bolha que segura: uma pergunta da conta anterior ainda em voo não trava a conta nova.
// A troca de conta solta tudo (A→B→A não deixa a trava velha de A), e cada envio só solta a própria trava (Codex no #1157).
const emVoo = new Map<string, number>();
const solta = (k: string, id: number) => { if (emVoo.get(k) === id) emVoo.delete(k); };
const notifica = () => subs.forEach((f) => f());
const patch = (id: number, p: Partial<Msg>) => { msgs = msgs.map((m) => (m.id === id ? { ...m, ...p } : m)); notifica(); };

// A conta dona da conversa. Se a sessão muda de conta noutra aba (login sem logout), a conversa e os
// contexts da anterior somem: as perguntas e respostas dela nunca aparecem para a conta nova (Codex no #1157).
let dono: number | undefined;
export function contaDaSessao(uid: number | undefined) {
  if (uid == null) return;
  if (dono != null && dono !== uid) { msgs = []; contextos.clear(); emVoo.clear(); abortarIa(); notifica(); }
  dono = uid;
}

/** A resposta ainda está na conversa: depois de um `await`, a troca de conta pode tê-la limpado. */
export const naConversa = (id: number) => msgs.some((x) => x.id === id);

interface Pedido { nota: 1 | -1 | null; motivo?: Motivo; texto?: string }
const estadoDe = (p: Pedido) => ({ feedback: p.nota, motivo: p.nota === -1 ? p.motivo ?? null : null });
const pendentes = new Map<number, Pedido>(); // o toque que chegou com o POST da bolha em voo: só o último vale (o servidor grava o estado inteiro)
const PRAZO_AVALIAR_MS = 15_000; // o `comLimite` do app

/** 👍👎 e motivo de uma resposta da IA (o `gravarAvaliacao` do app). A tela muda na hora. Com um POST desta bolha em voo, o toque fica guardado e sai
 * quando ele termina (null: ainda não gravou); só o resultado do último vale: falhou = volta ao último estado que o servidor confirmou, com aviso.
 * true = gravou; false = falhou (revertido); null = descartado (sem dono ou messageId) ou guardado. Cada POST tem prazo de 15 s. Troca de conta: `msgs`
 * zera e o `seq` não volta, então `naConversa` falha e nada mais sai nem chega à conta nova. O `estado` não é tocado: o anunciador do leitor de tela o lê. */
export async function avaliarIa(id: number, nota: 1 | -1 | null, motivo?: Motivo, texto?: string): Promise<boolean | null> {
  const m = msgs.find((x) => x.id === id);
  if (dono == null || !m?.ia || typeof m.messageId !== "number") return null;
  let atual: Pedido = { nota, motivo, texto };
  patch(id, { avaliando: true, avisoAvaliar: undefined, ...estadoDe(atual) });
  if (m.avaliando) { pendentes.set(id, atual); return null; }
  let confirmado = { feedback: m.feedback ?? null, motivo: m.motivo ?? null }, ok = false;
  for (;;) {
    const ctrl = new AbortController(), timer = setTimeout(() => ctrl.abort(), PRAZO_AVALIAR_MS);
    try { await escrever("POST", `/ai/messages/${m.messageId}/feedback`, atual, ctrl.signal); ok = true; confirmado = estadoDe(atual); } catch { ok = false; } finally { clearTimeout(timer); }
    if (!naConversa(id)) { pendentes.delete(id); return null; }
    const prox = pendentes.get(id);
    if (!prox) break;
    pendentes.delete(id);
    atual = prox;
  }
  patch(id, ok ? { avaliando: false, avisoAvaliar: atual.texto ? "obrigado" : undefined } : { avaliando: false, ...confirmado, avisoAvaliar: "falha" });
  return ok;
}

const nomeDe = (kind: string) => agenteDe(kind)?.nome ?? "agente";
const SEM_ENERGIA = (kind: string, e?: { custo: number; livre: number }) => e
  ? `O ${nomeDe(kind)} usa ⚡${e.custo} de energia e você tem ${e.livre} livre.` : `Você está sem energia livre para ativar o ${nomeDe(kind)}.`;
const INATIVO = (kind: string, e?: { custo: number; livre: number }) => e
  ? `Ativar o ${nomeDe(kind)} usa ⚡${e.custo} de energia. Você tem ${e.livre} livre.` : `O ${nomeDe(kind)} ainda não está ativo.`;
const estadoSemChat = (kind: string, acesso: Exclude<Acesso, "ready">, e?: { custo: number; livre: number }): Partial<Msg> =>
  acesso === "upgrade" ? { estado: "sem_plano", text: `Conversar com o ${nomeDe(kind)} faz parte do PigBank+.` }
  : acesso === "no_energy" ? { estado: "sem_energia", text: SEM_ENERGIA(kind, e) }
  : { estado: "inativo", text: INATIVO(kind, e) };

const CODIGO_ACESSO: Record<string, Exclude<Acesso, "ready">> = { activate: "activate", upgrade: "upgrade", no_energy: "no_energy" };
const SEM_SENHA = "Crie sua senha para conversar com os agentes. Depois de criar, volte para o painel novo.";

async function enviar(id: number, kind: string, texto: string, uid: number, encaminhada?: Encaminhada) {
  emVoo.set(`${uid}:${kind}`, id);
  patch(id, { estado: "enviando", text: "Preparando a resposta…", redirects: undefined, repetir: false, card: undefined, cartao: undefined });
  try {
    const r = await conversar(uid, kind, texto, contextos.get(`${uid}:${kind}`) ?? null, encaminhada);
    if (typeof r?.reply !== "string" || !r.reply.trim()) throw new Error("contrato");
    if (!msgs.some((x) => x.id === id)) return; // a conversa foi limpa (troca de conta) com a pergunta em voo: o context dela não volta (Codex no #1157)
    contextos.set(`${uid}:${kind}`, r.context ?? null);
    const redirects = (Array.isArray(r.redirects) ? r.redirects : []).filter((x) => agenteDe(x?.kind) && typeof x.question === "string" && x.access !== "unavailable");
    const c = cardDe(r.card);
    patch(id, { estado: "pronto", text: r.reply, redirects, card: c ? createElement(CardAgente, { uid, c }) : undefined, cartao: c ?? undefined });
  } catch (e) {
    if (!msgs.some((x) => x.id === id)) return; // conversa limpa (troca de conta) com a pergunta em voo: o erro dela não mexe no context da conversa nova (Codex no #1157)
    const d = detalheDe(e), status = e instanceof ErroApi ? e.status : -1;
    const acesso = status === 403 && d.error ? CODIGO_ACESSO[d.error] : undefined;
    if (d.error === "invalid_context") contextos.delete(`${uid}:${kind}`); // o próximo envio recomeça a conversa
    if (acesso) patch(id, estadoSemChat(kind, acesso));
    else if (semSenha(e) || sessaoDe(e)) patch(id, { estado: "erro", text: semSenha(e) ? SEM_SENHA : SESSAO, repetir: false });
    else patch(id, {
      estado: "erro",
      text: typeof d.message === "string" ? d.message
        : status === 0 ? "A conexão foi interrompida antes de recebermos a resposta. Sua pergunta continua aqui."
        : status === 429 ? "Muitas mensagens em pouco tempo. Aguarde um instante antes de tentar novamente."
        : "Não foi possível obter a resposta agora. Tente novamente em instantes.",
      repetir: d.retryable === true || status === 0, // só se o servidor marcou, ou se nem houve resposta (rede)
    });
  } finally {
    solta(`${uid}:${kind}`, id);
  }
}

/** Pergunta a um agente: bolha do usuário (`original`, com as menções) e a resposta, que se atualiza por id. Falso = já há uma pergunta em voo a esse agente (o texto fica na barra). */
export function perguntarAoAgente(a: { kind: string; original: string; texto: string; uid: number; acesso: Acesso; energia?: { custo: number; livre: number } | null; encaminhada?: Encaminhada }) {
  contaDaSessao(a.uid); // o uid da pergunta é o da sessão agora: se mudou, a conversa velha sai antes
  if (a.acesso === "ready" && emVoo.has(`${a.uid}:${a.kind}`)) return false;
  const e = a.energia ?? undefined;
  const bolha = ++seq, resp = ++seq;
  msgs = [...msgs,
    { id: bolha, role: "user", text: a.original, agente: a.kind },
    { id: resp, role: "piggy", agente: a.kind, real: true, uid: a.uid, pergunta: a.texto, encaminhada: a.encaminhada, custo: e?.custo, livre: e?.livre, text: "", ...(a.acesso === "ready" ? {} : estadoSemChat(a.kind, a.acesso, e)) }];
  notifica();
  go("/piggy");
  if (a.acesso === "ready") void enviar(resp, a.kind, a.texto, a.uid, a.encaminhada);
  return true;
}

/** "Tentar de novo" de um erro que o servidor marcou como repetível. */
export function tentarDeNovo(id: number) {
  const m = msgs.find((x) => x.id === id);
  if (m?.estado === "erro" && m.repetir && m.agente && m.pergunta && m.uid != null && !emVoo.has(`${m.uid}:${m.agente}`)) void enviar(id, m.agente, m.pergunta, m.uid, m.encaminhada);
}

/** "Ativar e responder": ativa o agente e manda a pergunta guardada. Enquanto a ativação/o envio desse agente está em voo, o `emVoo` (por conta e agente) segura os outros cliques; duas bolhas inativas clicadas em sequência fazem duas ativações (o servidor aceita reativar). */
export async function ativarEResponder(id: number, depois: () => void) {
  const m = msgs.find((x) => x.id === id);
  if (!m || m.estado !== "inativo" || !m.agente || !m.pergunta || m.uid == null || emVoo.has(`${m.uid}:${m.agente}`)) return;
  emVoo.set(`${m.uid}:${m.agente}`, id); // o mesmo bloqueio por conta e agente do perguntarAoAgente, também durante a ativação
  patch(id, { ativando: true, falhaAtivar: false });
  try {
    await ativar(m.uid, m.agente);
  } catch (e) {
    const d = detalheDe(e), acesso = d.error === "no_energy" ? "no_energy" : d.error === "pro_required" ? "upgrade" : null;
    // Sem os números da bolha: são de quando ela nasceu, e a recusa mostra que a energia mudou depois (Codex no #1157).
    patch(id, acesso ? { ativando: false, ...estadoSemChat(m.agente, acesso) }
      : semSenha(e) || sessaoDe(e) ? { ativando: false, estado: "erro", text: semSenha(e) ? SEM_SENHA : SESSAO, repetir: false }
      : { ativando: false, falhaAtivar: true });
    solta(`${m.uid}:${m.agente}`, id);
    return;
  }
  depois(); // a lista do @ relê o catálogo
  if (!msgs.some((x) => x.id === id)) { solta(`${m.uid}:${m.agente}`, id); return; } // a conversa foi limpa (troca de conta) durante a ativação: a pergunta da conta anterior não sai
  patch(id, { ativando: false });
  await enviar(id, m.agente, m.pergunta, m.uid, m.encaminhada);
}
