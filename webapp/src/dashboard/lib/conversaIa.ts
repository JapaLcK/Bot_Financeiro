// O turno da conversa com a IA no /painel (freio `chat_ia` do /me): um POST `/ai/chat/stream` por vez, o texto chegando aos poucos.
// Copia o comportamento de app/src/features/painel/conversaEstado.tsx, sem importá-lo. Quem guarda as mensagens é lib/conversation.ts;
// aqui mora a máquina do turno e o estado que a barra e o chat leem (`useIa`).
//
// Pendência (ai_pending_actions): o DELETE /ai/pending é BLOQUEANTE e roda só antes do 1º envio de cada carga da página e antes do envio
// seguinte a um turno "sujo" (erro, parada, prazo, stream sem `fim`): ali o servidor pode ter armado uma confirmação que a tela nunca mostrou,
// e um "sim" depois a executaria. Não roda ao trocar de página nem ao trocar de conta (a pendência do outro dono não é desta sessão).
// O DELETE não leva sinal (abortar não desfaz o pedido já enviado, e ele apagaria a confirmação que a tela mostra), mas o TURNO não fica refém
// dele: Parar e o prazo de 60 s saem na hora (e sujam), e o DELETE tem teto de 15 s (`TETO_DELETE_MS`, o do app). Só há um DELETE em voo (fila
// única): o envio seguinte espera por ele em vez de abrir outro, que correria com o POST. Falhou ou estourou o teto = o turno falha e o próximo refaz.
// Só um turno por vez; o que o fecha é o `fim`, um erro, a parada, os 60 s do turno todo ou a troca de conta.
import { useSyncExternalStore } from "react";
import { ErroApi, chamar, detalheDe, escrever, semSenha, sessaoDe } from "./v2";
import { fluxo } from "./sse";
import type { Msg } from "./conversation";

export interface Uso { used: number; limit: number }
/** `ocupado`: preparando ou em voo (um turno por vez). `cheia`: a cota acabou (ou o plano não tem IA: o servidor responde igual). */
export interface EstadoIa { ocupado: boolean; uso: Uso | null; cheia: boolean }
/** Os campos da mensagem do Piggy que vêm da IA (o resto é do `Msg`). */
export interface CamposIa {
  ia?: true; passos?: string[]; parcial?: string; blocos?: string[]; messageId?: number | null; chips?: string[];
  feedback?: 1 | -1 | null; motivo?: Motivo | null; avaliando?: boolean; avisoAvaliar?: "falha" | "obrigado"; // o 👍👎 (lib/conversation.ts `avaliarIa`)
}

/** Espelho de `db/ai_feedback.py::MOTIVOS` e de `TEXTO_MAX` em `frontend/routes/ai_chat.py` (§0.7: `tests/test_motivo_feedback_app.py` compara). */
export const MOTIVOS = { numero_errado: "Número errado", nao_entendeu: "Não entendeu", faltou_card: "Faltou card", outro: "Outro" } as const;
export type Motivo = keyof typeof MOTIVOS;
export const TEXTO_MAX = 500;
export const PRAZO_MS = 60_000; // o turno todo, preparar incluído (o do app)
export const TETO_DELETE_MS = 15_000;
export const PREPARANDO = "Preparando a resposta…";
export const PAROU = "Você parou esta resposta.";
export const SEM_COTA = "Suas mensagens com o Piggy acabaram por este mês ou não fazem parte do seu plano.";
const SEM_SENHA = "Crie sua senha para conversar com o Piggy. Depois de criar, volte para o painel novo.";
export const SESSAO = "Sua sessão terminou. Recarregue a página.";
const PRAZO = "A resposta demorou mais que o esperado. Tente de novo.";
const REDE = "A conexão foi interrompida antes de recebermos a resposta.";
const MUITAS = "Muitas mensagens em pouco tempo. Aguarde um instante antes de tentar novamente.";
const GENERICO = "Não foi possível obter a resposta agora. Tente novamente em instantes.";

let estado: EstadoIa = { ocupado: false, uso: null, cheia: false };
const subs = new Set<() => void>();
const muda = (p: Partial<EstadoIa>) => { estado = { ...estado, ...p }; subs.forEach((f) => f()); };
export const useIa = () => useSyncExternalStore((f) => { subs.add(f); return () => subs.delete(f); }, () => estado);
/** O envio à IA está fechado: há um turno em curso ou a cota acabou. */
export const iaBloqueada = () => estado.ocupado || estado.cheia;

interface Turno { ctrl: AbortController; timer: ReturnType<typeof setTimeout>; parou: boolean; prazo: boolean }
let atual: Turno | null = null;
let preparado = false; // o DELETE já rodou nesta carga da página, para esta conta
let sujo = false; // o último turno não terminou limpo: o servidor pode ter armado uma confirmação que a tela não mostrou

const usoDe = (x: unknown): Uso | null => {
  const u = x as Partial<Uso> | null | undefined;
  return Number.isInteger(u?.used) && Number.isInteger(u?.limit) ? { used: u!.used!, limit: u!.limit! } : null;
};

type Evento =
  | { tipo: "passo"; texto: string } | { tipo: "delta"; texto: string } | { tipo: "reinicio" } | { tipo: "erro" } | { tipo: "bloco"; card: string }
  | { tipo: "fim"; reply: string; usage: Uso; messageId: number | null; chips: string[] };
/** `null` para tipo desconhecido ou forma inválida: o evento é descartado e o stream segue (como no app). */
export function lerEvento(bruto: unknown): Evento | null {
  if (!bruto || typeof bruto !== "object") return null;
  const o = bruto as Record<string, unknown>;
  switch (o.tipo) {
    case "passo": return typeof o.texto === "string" ? { tipo: "passo", texto: o.texto } : null;
    case "delta": return typeof o.texto === "string" ? { tipo: "delta", texto: o.texto } : null;
    case "reinicio": return { tipo: "reinicio" };
    case "erro": return { tipo: "erro" };
    case "bloco": {
      const b = o.bloco as { card?: unknown; abas?: unknown } | null;
      return typeof b?.card === "string" && Array.isArray(b.abas) && b.abas.length >= 1 && b.abas.length <= 3 ? { tipo: "bloco", card: b.card } : null;
    }
    case "fim": {
      const usage = usoDe(o.usage), id = o.message_id;
      const idOk = id === null || (typeof id === "number" && Number.isInteger(id) && id > 0);
      if (typeof o.reply !== "string" || !usage || !idOk) return null;
      const c = o.chips;
      const chips = Array.isArray(c) && c.length <= 4 && c.every((x) => typeof x === "string" && x.length >= 1 && x.length <= 40) ? c as string[] : [];
      return { tipo: "fim", reply: o.reply, usage, messageId: id as number | null, chips };
    }
    default: return null;
  }
}

/** O que a pessoa lê quando o turno falha. 403 `ai_chat` (cota cheia ou plano sem IA, o servidor não distingue) é "cota", não "falhou". */
function falha(e: unknown): { estado: "erro" | "cota"; text: string } {
  const d = detalheDe(e);
  if (e instanceof ErroApi && e.status === 403 && d.error === "pro_required") return { estado: "cota", text: SEM_COTA };
  if (semSenha(e)) return { estado: "erro", text: SEM_SENHA };
  if (sessaoDe(e)) return { estado: "erro", text: SESSAO };
  const status = e instanceof ErroApi ? e.status : -1;
  return { estado: "erro", text: status === 0 ? REDE : status === 429 ? MUITAS : GENERICO };
}

let delecao: Promise<unknown> | null = null; // o DELETE em voo (fila única); a troca de conta o solta sem abortá-lo
function apagarPendencia() {
  if (delecao) return delecao;
  let timer: ReturnType<typeof setTimeout>;
  const teto = new Promise<never>((_, no) => { timer = setTimeout(() => no(new Error("DELETE sem resposta")), TETO_DELETE_MS); });
  const p: Promise<unknown> = Promise.race([escrever("DELETE", "/ai/pending", undefined), teto]).finally(() => { clearTimeout(timer); if (delecao === p) delecao = null; });
  p.catch(() => {}); // quem saiu do turno (Parar, prazo) não espera por ele: a falha não vira "unhandled rejection"
  return delecao = p;
}

const lerCota = (signal?: AbortSignal) => chamar<{ usage?: unknown }>("/ai/messages?limit=1", { headers: { Accept: "application/json" }, signal }).then((r) => usoDe(r?.usage));
const sem = (uso: Uso) => ({ uso, cheia: uso.used >= uso.limit });
let epoca = 0; // sobe a cada troca de conta: a leitura da cota que estava em voo não é da conta nova

// Cota cheia desliga o Enviar e os chips, e nada mais a relê: quem renovou o plano noutra aba (ou virou o mês) voltaria a ela só recarregando.
// Ao a aba voltar a ficar visível, relê (o mesmo GET do preparo). Falhou = continua cheia.
if (typeof document !== "undefined") document.addEventListener("visibilitychange", () => {
  if (document.visibilityState !== "visible" || !estado.cheia) return;
  const e = epoca;
  void lerCota().then((uso) => { if (uso && e === epoca) muda(sem(uso)); }).catch(() => {});
});

/** Apaga a pendência (a fila acima) e lê a cota. Parar e o prazo cortam a ESPERA, não o DELETE. Falhar aqui não deixa enviar. */
async function preparar(t: Turno): Promise<Uso | null> {
  await Promise.race([apagarPendencia(), new Promise<never>((_, no) => t.ctrl.signal.addEventListener("abort", () => no(new DOMException("Aborted", "AbortError"))))]);
  if (atual !== t) return null; // a conta trocou durante o DELETE: ele já saiu, mas nada daqui é da conta nova
  preparado = true; sujo = false;
  return lerCota(t.ctrl.signal);
}

/** Um turno. `patch` atualiza a mensagem da IA que `conversation.ts` já pôs na conversa. */
export async function enviarIa(texto: string, patch: (p: Partial<Msg>) => void) {
  const ctrl = new AbortController();
  const t: Turno = { ctrl, parou: false, prazo: false, timer: setTimeout(() => { t.prazo = true; ctrl.abort(); }, PRAZO_MS) };
  atual = t; muda({ ocupado: true });
  const vivo = () => atual === t && !ctrl.signal.aborted;
  const liberar = () => { clearTimeout(t.timer); if (atual === t) { atual = null; muda({ ocupado: false }); } };
  const passos: string[] = [], blocos: string[] = [];
  let parcial = "", fim = false;
  try {
    if (!preparado || sujo) {
      const uso = await preparar(t);
      if (!vivo()) throw new DOMException("Aborted", "AbortError");
      if (!uso) throw new Error("cota fora do contrato");
      muda(sem(uso));
      if (uso.used >= uso.limit) { liberar(); return patch({ estado: "cota", text: SEM_COTA }); } // sem POST: o servidor recusaria
    }
    await fluxo("/ai/chat/stream", { message: texto, cards: [] }, ctrl.signal, (bruto) => {
      if (!vivo()) return; // troca de conta, parada ou prazo: nada mais entra
      const e = lerEvento(bruto);
      if (!e) return;
      if (e.tipo === "passo") { passos.push(e.texto); patch({ passos: [...passos] }); }
      else if (e.tipo === "bloco") { blocos.push(e.card); patch({ blocos: [...blocos] }); }
      else if (e.tipo === "delta") { parcial += e.texto; patch({ parcial }); }
      else if (e.tipo === "reinicio") { parcial = ""; patch({ parcial }); }
      else if (e.tipo === "erro") throw new Error("erro do servidor");
      else {
        fim = true; // o `fim` encerra o turno: o que vier depois cai na guarda `vivo()`
        liberar();
        muda(sem(e.usage));
        patch({ estado: "pronto", text: e.reply, parcial: "", messageId: e.messageId, chips: e.chips });
        ctrl.abort(); // o servidor já terminou: solta a conexão
      }
    });
    if (!fim && atual === t) throw new Error("stream sem fim");
  } catch (e) {
    if (fim || atual !== t) return; // concluído, ou a conta trocou (abortarIa cuidou do estado): sem texto
    sujo = true;
    if (t.parou) patch({ estado: "parou", text: PAROU });
    else if (t.prazo) patch({ estado: "erro", text: PRAZO, parcial: "" });
    else {
      const f = falha(e);
      if (f.estado === "cota") muda({ cheia: true });
      patch({ estado: f.estado, text: f.text, parcial: "" });
    }
  } finally {
    liberar();
  }
}

/** O botão Parar: corta a conexão (é assim que o servidor sabe). A mensagem mostra o que já chegou. */
export function pararIa() {
  const t = atual;
  if (!t || t.ctrl.signal.aborted) return;
  t.parou = true;
  t.ctrl.abort();
}

/** A sessão virou outra conta: o turno em voo morre SEM DELETE (a pendência é do outro dono) e o "preparado" volta a zero. Nenhum evento da conta anterior entra depois. */
export function abortarIa() {
  const t = atual;
  atual = null; preparado = false; sujo = false; delecao = null; epoca++;
  if (t) { clearTimeout(t.timer); t.ctrl.abort(); }
  muda({ ocupado: false, uso: null, cheia: false });
}
