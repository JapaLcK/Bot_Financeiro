import { createContext, useCallback, useContext, useEffect, useRef, useState, type ReactNode } from "react";
import { conversaSchema } from "@/api/schemas/painel";
import { cancelarPendencia, enviarMensagem, lerRecurso } from "@/services/painel";
import { mensagemRecurso, useEstadoPainel } from "./provider";
import type { Pergunta } from "./perguntas";
export type Turno = { id: number; pergunta: string; assunto?: Pergunta; resposta?: string; erro?: string; nota?: string };
type Uso = { used: number; limit: number };
/** abrindo = DELETE /ai/pending (e a cota) em curso; falhou = sem 2xx, envio desligado até "Tentar novamente". */
type Bloqueio = "abrindo" | "livre" | "falhou";
type Conversa = { turnos: Turno[]; uso: Uso | null; bloqueio: Bloqueio; enviando: boolean; rascunho: string; setRascunho: (t: string) => void; abrir: () => void; fechar: () => void; tentarNovamente: () => void; enviar: (texto: string, assunto?: Pergunta) => Promise<void> };
const Contexto = createContext<Conversa | null>(null);
export function useConversa() { const c = useContext(Contexto); if (!c) throw new Error("Conversa indisponível"); return c; }
/**
 * Mora no layout (dentro do PainelProvider) porque o PortaoPainel desmonta a tela no segundo plano e o turno em voo tem de sobreviver.
 * O POST tem AbortController PRÓPRIO (fora de p.operacao()): o cancelar() do painel (segundo plano, trava, atualizar) não o toca;
 * só a troca de conta, o logout, o limite de 60 s do POST e a desmontagem do provider o abortam.
 * Pendência (ai_pending_actions): DELETE bloqueante na abertura sem turno em voo (com voo, só lê a cota: a pendência do POST em voo é do turno que a tela vai mostrar, e um DELETE em paralelo a apagaria) e antes do envio seguinte a um erro (erro que chega com um DELETE em voo mantém `sujo`); na saída, só se enviou.
 */
export function ConversaProvider({ children }: { children: ReactNode }) {
  const p = useEstadoPainel(); const falhou = p.falhou;
  const uid = p.usuario?.user_id ?? null;
  const dono = useRef(uid); dono.current = uid;
  const oculto = useRef(p.oculto); oculto.current = p.oculto; // pode() roda depois de awaits: lê o valor de agora, não o do render que criou o enviar
  const [turnos, setTurnos] = useState<Turno[]>([]);
  const [uso, setUso] = useState<Uso | null>(null);
  const [bloqueio, setBloqueio] = useState<Bloqueio>("abrindo");
  const [enviando, setEnviando] = useState(false);
  const [rascunho, setRascunho] = useState("");
  const aberta = useRef(false), enviou = useRef(false), sujo = useRef(false), erros = useRef(0), proximo = useRef(0), abertura = useRef(0), versaoUso = useRef(0); // versaoUso: o POST que grava o uso invalida a cota que um GET mais antigo ainda traz
  const voo = useRef<AbortController | null>(null), prep = useRef<AbortController[]>([]), ativos = useRef(new Set<AbortController>());
  const novo = () => { const c = new AbortController(); ativos.current.add(c); return c; };
  const preparar = useCallback(async (semDelete = false) => {
    const alvo = dono.current; if (alvo === null) return false;
    for (const c of prep.current) c.abort();
    const cd = novo(), cg = novo(); prep.current = [cd, cg]; setBloqueio("abrindo");
    const vivo = () => prep.current[0] === cd && dono.current === alvo, e0 = erros.current, v0 = versaoUso.current; // erro de POST durante o DELETE: ele pode ter saído antes do turno acabar no servidor
    try {
      const [, cota] = await Promise.all([semDelete ? undefined : cancelarPendencia(cd), lerRecurso("/ai/messages?limit=1", conversaSchema, cg)]);
      if (!vivo()) return false;
      if (erros.current === e0 && !semDelete) sujo.current = false;
      if (versaoUso.current === v0) { setUso(cota.usage); }
      setBloqueio("livre"); return true;
    } catch (e) { if (vivo()) { falhou(e); setBloqueio("falhou"); } return false; }
    finally { ativos.current.delete(cd); ativos.current.delete(cg); }
  }, [falhou]);
  /** DELETE da saída: sem bloqueio, falha em silêncio (o próximo foco repete). Só vale para a conta que o pediu. */
  const sair = useCallback(async (alvo: number, turno?: number) => {
    if (dono.current !== alvo) return;
    const c = novo();
    try {
      const r = await cancelarPendencia(c);
      if (dono.current === alvo && r.cancelada && turno !== undefined) { const nota = r.cancelada; setTurnos((ts) => ts.map((t) => t.id === turno ? { ...t, nota } : t)); }
    } catch (e) { if (dono.current === alvo) falhou(e); }
    finally { ativos.current.delete(c); }
  }, [falhou]);
  const abrir = useCallback(() => { aberta.current = true; abertura.current++; enviou.current = false; void preparar(!!voo.current); }, [preparar]);
  const fechar = useCallback(() => {
    if (!aberta.current) return;
    aberta.current = false; for (const c of prep.current) c.abort(); prep.current = [];
    const emVoo = voo.current ? proximo.current : null; // o turno em voo é sempre o último
    setRascunho(""); setTurnos((ts) => ts.filter((t) => t.id === emVoo));
    const alvo = dono.current;
    if (enviou.current && !voo.current && alvo !== null) { enviou.current = false; void sair(alvo); }
  }, [sair]);
  const enviar = async (texto: string, assunto?: Pergunta) => {
    const message = texto.trim(), alvo = dono.current, minha = abertura.current;
    const pode = () => dono.current === alvo && aberta.current && !voo.current && !oculto.current && !!message && message.length <= 2000;
    if (alvo === null || !pode() || bloqueio !== "livre" || !uso || uso.used >= uso.limit) return;
    if (sujo.current && (!(await preparar()) || !pode())) return; // depois de erro: DELETE bloqueante antes de enviar
    const ctrl = new AbortController(); ativos.current.add(ctrl); voo.current = ctrl;
    const id = ++proximo.current; enviou.current = true;
    setEnviando(true); setRascunho(""); setTurnos((ts) => [...ts, { id, pergunta: message, assunto }]);
    const marcar = (campos: Partial<Turno>) => setTurnos((ts) => ts.map((t) => t.id === id ? { ...t, ...campos } : t));
    try {
      const r = await enviarMensagem(message, ctrl);
      if (dono.current === alvo) { marcar({ resposta: r.reply }); versaoUso.current++; setUso(r.usage); }
    } catch (e) {
      if (dono.current === alvo) { sujo.current = true; erros.current++; falhou(e); marcar({ erro: mensagemRecurso(e) }); if (aberta.current && abertura.current === minha) setRascunho((r) => r || message); }
    } finally {
      ativos.current.delete(ctrl);
      if (dono.current === alvo && voo.current === ctrl) {
        voo.current = null; setEnviando(false);
        if (aberta.current) enviou.current = true; // o turno terminou nesta abertura: a saída cancela o que ele armou
        else { enviou.current = false; void sair(alvo, id); }
      }
    }
  };
  const antes = useRef(uid);
  useEffect(() => {
    const prev = antes.current; antes.current = uid;
    if (prev === null || prev === uid) return;
    // Troca de conta (ou logout): aborta tudo e NÃO manda DELETE — a pendência do outro dono não é desta sessão.
    for (const c of ativos.current) c.abort();
    ativos.current.clear(); prep.current = []; voo.current = null; sujo.current = false; enviou.current = false;
    setTurnos([]); setRascunho(""); setUso(null); setEnviando(false); setBloqueio("abrindo");
    if (aberta.current && uid !== null) void preparar();
  }, [uid, preparar]);
  useEffect(() => () => { for (const c of ativos.current) c.abort(); }, []);
  return <Contexto.Provider value={{ turnos, uso, bloqueio, enviando, rascunho, setRascunho, abrir, fechar, tentarNovamente: () => void preparar(!!voo.current), enviar }}>{children}</Contexto.Provider>;
}
