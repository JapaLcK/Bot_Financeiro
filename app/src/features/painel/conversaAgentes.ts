import { useCallback, useEffect, useRef, useState, type Dispatch, type MutableRefObject, type SetStateAction } from "react";
import { AccessibilityInfo } from "react-native";
import { ErroDeApi, RequisicaoSuperada, SessaoExpirada } from "@/api/client";
import { ativarAgente, conversarComAgente, lerAgentes } from "@/services/agentes";
import { TEM_ARROBA, acessoDe, agenteDe, energiaDe, mencaoDe, type Acesso, type Agentes } from "./agentes";
import { NOVO, type Turno } from "./conversaEstado";
// O `@Nome` do chat: o turno do agente é um `Turno` com `agente` (ordem, limpeza no fechar e na troca de conta, OCULTA: tudo já coberto). Porta de webapp/src/dashboard/lib/conversation.ts.
export type Energia = { custo: number; livre: number };
export type Redirecionamento = { kind: string; name: string; question: string; access: string };
export type TurnoAgente = { kind: string; mensagem: string; estado: "enviando" | "pronto" | "erro" | "inativo" | "sem_energia" | "sem_plano"; texto: string; redirects: Redirecionamento[]; repetir: boolean; energia: Energia | null; ativando: boolean; falhaAtivar: boolean };
type Deps = { uid: number | null; dono: MutableRefObject<number | null>; ativos: MutableRefObject<Set<AbortController>>; voo: MutableRefObject<AbortController | null>; proximo: MutableRefObject<number>; setTurnos: Dispatch<SetStateAction<Turno[]>>; setRascunho: Dispatch<SetStateAction<string>>; aplicarUso: Dispatch<SetStateAction<{ used: number; limit: number } | null>>; falhou: (e: unknown) => void };

const nomeAg = (kind: string) => agenteDe(kind)?.nome ?? "agente";
const estadoSemChat = (kind: string, acesso: Exclude<Acesso, "ready">, e: Energia | null): Partial<TurnoAgente> =>
  acesso === "upgrade" ? { estado: "sem_plano", texto: `Conversar com o ${nomeAg(kind)} faz parte do PigBank+.` }
  : acesso === "no_energy" ? { estado: "sem_energia", texto: e ? `O ${nomeAg(kind)} usa ⚡${e.custo} de energia e você tem ${e.livre} livre.` : `Você está sem energia livre para ativar o ${nomeAg(kind)}.` }
  : { estado: "inativo", texto: e ? `Ativar o ${nomeAg(kind)} usa ⚡${e.custo} de energia. Você tem ${e.livre} livre.` : `O ${nomeAg(kind)} ainda não está ativo.` };
const CODIGO_ACESSO: Record<string, Exclude<Acesso, "ready">> = { activate: "activate", upgrade: "upgrade", no_energy: "no_energy" };
/** O `detail` de `/agents/*` é objeto (`{error, message, retryable}`) ou texto (CSRF, "Acesso negado"): só o objeto interessa. */
const detalheDe = (e: unknown): { error?: string; message?: string; retryable?: boolean } => {
  const d = e instanceof ErroDeApi ? (e.corpo as { detail?: unknown } | null | undefined)?.detail : undefined;
  return d && typeof d === "object" ? d : {};
};
const SEM_SENHA = "Crie a senha da sua conta pelo link que enviamos por e-mail para conversar com os agentes.";
const semSenha = (e: unknown) => e instanceof ErroDeApi && (detalheDe(e).error === "password_required" || (e.corpo as { code?: unknown } | null | undefined)?.code === "password_required");

export function useConversaAgentes({ uid, dono, ativos, voo, proximo, setTurnos, setRascunho, aplicarUso, falhou }: Deps) {
  const [catalogo, setCatalogo] = useState<{ uid: number; dados: Agentes } | null>(null);
  const contextos = useRef(new Map<string, string | null>()), emVoo = useRef(new Set<string>()), epoca = useRef(0);
  const lendo = useRef<Promise<Agentes | null> | null>(null), esperando = useRef(false), leitura = useRef(0); // esperando: o 2º toque durante a espera do catálogo não duplica o envio
  const atual = catalogo?.uid === uid ? catalogo.dados : null; // o catálogo de A nunca é visto sob B, nem por um quadro
  const atualizar = (id: number, f: Partial<TurnoAgente>) => setTurnos((ts) => ts.map((t) => t.id === id && t.agente ? { ...t, agente: { ...t.agente, ...f } } : t));
  const zerar = useCallback(() => { contextos.current.clear(); emVoo.current.clear(); esperando.current = false; lendo.current = null; epoca.current++; }, []);
  useEffect(() => { zerar(); setCatalogo(null); }, [uid, zerar]); // troca de conta: o efeito do provider já abortou os pedidos; aqui some o que era de A
  const ler = useCallback((): Promise<Agentes | null> => {
    const alvo = dono.current; if (alvo === null) return Promise.resolve(null);
    if (lendo.current) return lendo.current;
    const c = new AbortController(); ativos.current.add(c); const e0 = epoca.current, n = ++leitura.current; // n: só a leitura mais recente grava
    const eu: { p?: Promise<Agentes | null> } = {}; // a promessa se reconhece no finally
    eu.p = (async () => {
      try {
        const dados = await lerAgentes(alvo, c);
        if (dono.current !== alvo || epoca.current !== e0) return null;
        if (leitura.current === n) setCatalogo({ uid: alvo, dados });
        return dados;
      } catch (e) { if (dono.current === alvo && epoca.current === e0 && !(e instanceof RequisicaoSuperada)) falhou(e); return null; } // 404 do beta, contrato e rede: sem lista, a mensagem vai ao Piggy
      finally { ativos.current.delete(c); if (lendo.current === eu.p) lendo.current = null; }
    })();
    return (lendo.current = eu.p!);
  }, [dono, ativos, falhou]);

  const enviarAgente = async (id: number, kind: string, mensagem: string, energia: Energia | null) => {
    const alvo = dono.current, e0 = epoca.current, c = new AbortController(); ativos.current.add(c);
    const vivo = () => dono.current === alvo && epoca.current === e0;
    emVoo.current.add(kind); atualizar(id, { estado: "enviando", texto: "", redirects: [], repetir: false });
    try {
      const r = await conversarComAgente(alvo!, kind, mensagem, contextos.current.get(kind) ?? null, c);
      if (dono.current !== alvo) return;
      if (r.usage) aplicarUso(r.usage); // a cota do app sai desta resposta; o servidor só desconta quando o agente respondeu
      if (!vivo()) return;
      if (!r.reply.trim()) throw new ErroDeApi(200, "resposta vazia");
      contextos.current.set(kind, r.context);
      atualizar(id, { estado: "pronto", texto: r.reply, redirects: (r.redirects ?? []).filter((x) => agenteDe(x.kind) && x.access !== "unavailable") });
      AccessibilityInfo.announceForAccessibility(`${nomeAg(kind)} respondeu`);
    } catch (e) {
      if (!vivo() || e instanceof RequisicaoSuperada) return; // troca de conta ou saída da aba: o turno não é mais desta tela
      const d = detalheDe(e), status = e instanceof ErroDeApi ? e.status : -1;
      const acesso = status === 403 && d.error ? CODIGO_ACESSO[d.error] : undefined;
      if (d.error === "invalid_context") contextos.current.delete(kind); // o próximo envio recomeça a conversa
      if (d.error === "quota_exhausted") aplicarUso((u) => u && { ...u, used: Math.max(u.used, u.limit) }); // outro cliente gastou o resto: trava o campo e as ações como a cota cheia
      falhou(e);
      if (acesso) atualizar(id, estadoSemChat(kind, acesso, energia));
      else if (semSenha(e)) atualizar(id, { estado: "erro", texto: SEM_SENHA, repetir: false });
      else if (e instanceof SessaoExpirada || (e instanceof ErroDeApi && (status === 401 || (status === 403 && !d.error)))) atualizar(id, { estado: "erro", texto: "Sua sessão terminou. Entre de novo.", repetir: false });
      else atualizar(id, {
        estado: "erro",
        texto: typeof d.error === "string" && typeof d.message === "string" ? d.message
          : status === 429 ? "Muitas mensagens em pouco tempo. Aguarde um instante antes de tentar novamente."
          : "Não foi possível obter a resposta agora. Tente novamente em instantes.",
        repetir: d.retryable === true || !(e instanceof ErroDeApi), // só se o servidor marcou, ou se nem houve resposta (rede, prazo de 60 s)
      });
    } finally { ativos.current.delete(c); if (vivo()) emVoo.current.delete(kind); }
  };
  /** Cria o turno do agente. Falso = já há pergunta em voo a ele, ou o Piggy está respondendo (turno de agente só nasce com `!enviando`: o `fechar` supõe que o turno em voo é o último). */
  const perguntar = (kind: string, original: string, mensagem: string, acesso: Acesso, energia: Energia | null) => {
    if (voo.current || (acesso === "ready" && emVoo.current.has(kind))) return false;
    const id = ++proximo.current;
    setTurnos((ts) => [...ts, { id, pergunta: original, ...NOVO, agente: { kind, mensagem, estado: "enviando", texto: "", redirects: [], repetir: false, energia, ativando: false, falhaAtivar: false, ...(acesso === "ready" ? {} : estadoSemChat(kind, acesso, energia)) } }]);
    if (acesso === "ready") void enviarAgente(id, kind, mensagem, energia);
    return true;
  };
  const redirecionar = (r: Redirecionamento) => perguntar(r.kind, r.question, r.question, r.access === "ready" || r.access === "no_energy" || r.access === "upgrade" ? r.access : "activate", atual ? energiaDe(atual, r.kind) : null);
  /** Decide para onde vai o texto do campo: "piggy" (segue o envio de sempre), "agente" (criou o turno e limpou o campo) ou "fica" (o texto continua no campo). */
  const rotear = async (texto: string): Promise<"piggy" | "agente" | "fica"> => {
    if (!TEM_ARROBA.test(texto)) return "piggy";
    const alvo = dono.current; if (alvo === null || esperando.current) return alvo === null ? "piggy" : "fica";
    let dados = atual; const e0 = epoca.current;
    if (!dados) { esperando.current = true; try { dados = await ler(); } finally { esperando.current = false; } if (dono.current !== alvo || epoca.current !== e0) return "fica"; if (!dados) return "piggy"; }
    const m = mencaoDe(texto, dados);
    if (!m) return "piggy";
    if (!m.texto || !perguntar(m.kind, texto.trim(), m.texto, acessoDe(dados, m.kind)!, energiaDe(dados, m.kind))) return "fica";
    setRascunho((r) => r === texto ? "" : r); return "agente"; // texto novo digitado durante a espera fica
  };
  /** "Ativar e responder": o `emVoo` por kind segura o duplo toque já durante a ativação; depois da ativação, a lista relê o catálogo. */
  const ativarEResponder = async (t: Turno) => {
    const a = t.agente, alvo = dono.current;
    if (!a || a.estado !== "inativo" || alvo === null || emVoo.current.has(a.kind)) return;
    const e0 = epoca.current, c = new AbortController(); ativos.current.add(c); const vivo = () => dono.current === alvo && epoca.current === e0;
    emVoo.current.add(a.kind); atualizar(t.id, { ativando: true, falhaAtivar: false });
    try { await ativarAgente(alvo, a.kind, c); }
    catch (e) {
      if (!vivo() || e instanceof RequisicaoSuperada) return;
      emVoo.current.delete(a.kind); falhou(e);
      const d = detalheDe(e), acesso = d.error === "no_energy" ? "no_energy" : d.error === "pro_required" ? "upgrade" : null;
      atualizar(t.id, acesso ? { ativando: false, ...estadoSemChat(a.kind, acesso, a.energia) } : semSenha(e) ? { ativando: false, estado: "erro", texto: SEM_SENHA, repetir: false } : { ativando: false, falhaAtivar: true });
      return;
    } finally { ativos.current.delete(c); }
    if (!vivo()) return;
    lendo.current = null; void ler(); atualizar(t.id, { ativando: false });
    await enviarAgente(t.id, a.kind, a.mensagem, a.energia);
  };
  const tentarDeNovo = (t: Turno) => { const a = t.agente; if (a?.estado === "erro" && a.repetir && !emVoo.current.has(a.kind)) void enviarAgente(t.id, a.kind, a.mensagem, a.energia); };
  return { catalogo: atual, ler, rotear, redirecionar, ativarEResponder, tentarDeNovo, zerar };
}
