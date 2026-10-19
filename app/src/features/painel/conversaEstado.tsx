import { createContext, useCallback, useContext, useEffect, useRef, useState, type ReactNode } from "react";
import { AccessibilityInfo } from "react-native";
import { ErroDeApi } from "@/api/client";
import { conversaSchema, lerEvento, type BlocoChat } from "@/api/schemas/painel";
import { avaliarResposta, cancelarPendencia, conversarFluxo, lerRecurso } from "@/services/painel";
import { mensagemRecurso, useEstadoPainel } from "./provider";
import type { Pergunta } from "./perguntas";
import type { Motivo } from "./motivoFeedback";
import { useConversaAgentes, type TurnoAgente } from "./conversaAgentes";
/** `passos`/`previa`/`blocos` são só GUARDADOS aqui: quem os desenha é o D2/E1. `parcial` = texto do stream até o `fim`. */
export type Turno = { id: number; pergunta: string; assunto?: Pergunta; resposta?: string; erro?: string; nota?: string; passos: string[]; previa: string | null; parcial: string; blocos: BlocoChat[]; messageId: number | null; feedback: 1 | -1 | null; motivo: Motivo | null; interrompida: boolean; chips: string[]; agente?: TurnoAgente };
export const NOVO = { passos: [], previa: null, parcial: "", blocos: [], messageId: null, feedback: null, motivo: null, interrompida: false, chips: [] };
type Uso = { used: number; limit: number };
export const cotaCheia = (u: Uso | null) => !!u && u.used >= u.limit;
/** abrindo = DELETE /ai/pending e a cota em curso, e a fila de DELETE em voo (`delecoes`) ainda não esvaziou; falhou = sem 2xx, envio desligado até "Tentar novamente". */
type Bloqueio = "abrindo" | "livre" | "falhou";
/** O estado inteiro da avaliação, como o servidor o grava: `motivo`/`texto` só valem com `nota: -1`. */
export type Avaliacao = { nota: 1 | -1 | null; motivo?: Motivo; texto?: string };
/** Modo comentário do "Outro": o texto mora aqui (separado do `rascunho` do chat) e só vale para o último turno enquanto ele estiver com 👎 + motivo "outro" (derivado em `useComentario`: reverter a avaliação desliga o modo sozinho). */
export type Comentario = { turno: number; texto: string };
type Conversa = { turnos: Turno[]; uso: Uso | null; bloqueio: Bloqueio; enviando: boolean; rascunho: string; setRascunho: (t: string) => void; abrir: () => void; fechar: () => void; tentarNovamente: () => void; enviar: (texto: string, assunto?: Pergunta, doCampo?: boolean) => Promise<void>; parar: () => void; avaliar: (turno: Turno, nota: 1 | -1) => Promise<boolean | null>; gravarAvaliacao: (turno: Turno, a: Avaliacao) => Promise<boolean | null>; avaliando: boolean; comentario: Comentario | null; comentar: (c: Comentario | null) => void; agentes: ReturnType<typeof useConversaAgentes> };
const Contexto = createContext<Conversa | null>(null);
export function useConversa() { const c = useContext(Contexto); if (!c) throw new Error("Conversa indisponível"); return c; }
/**
 * Mora no layout (dentro do PainelProvider) porque o PortaoPainel desmonta a tela quando a trava biométrica cobre (e a troca de conta a remonta) e o turno em voo tem de sobreviver.
 * Resposta em stream (`/ai/chat/stream`): TODO evento passa por `vivo()` (mesmo dono E mesmo voo); o `fim` encerra o voo, então o que vier depois cai nessa guarda. `parar()` aborta o voo e marca `parado`: é o que separa parar dos 60 s e da troca de conta.
 * O POST tem AbortController PRÓPRIO (fora de p.operacao()): o cancelar() do painel (segundo plano, trava, atualizar) não o toca;
 * só a troca de conta, o logout, o limite de 60 s do POST e a desmontagem do provider o abortam.
 * Pendência (ai_pending_actions): DELETE bloqueante na abertura sem turno em voo (com voo, só lê a cota: a pendência do POST em voo é do turno que a tela vai mostrar, e um DELETE em paralelo a apagaria) e antes do envio seguinte a um erro (erro que chega com um DELETE em voo mantém `sujo`); na saída, só se enviou.
 * Fila única (`delecoes`): NENHUM DELETE (abertura ou saída) é abortado no cliente — abortar não desfaz o pedido já enviado, e ele apagaria a confirmação mostrada. Só troca de conta e desmontagem abortam; todo `preparar` espera a fila inteira antes de `livre`. Residual: DELETE que estoura os 15 s do `comLimite` e chega atrasado ao servidor; logout e entrada de novo na mesma conta com um DELETE abortado ainda a caminho do servidor. "Tentar novamente" repete o MODO da tentativa que falhou (`semDeleteUlt`): se o voo já acabou, um DELETE apagaria a pendência que a tela mostra.
 * O envio depois de erro só sai com a cota que o `preparar` acabou de ler (cheia: não envia).
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
  const [comentario, comentar] = useState<Comentario | null>(null);
  const [salvando, setSalvando] = useState(false); // POST de avaliação em voo: a UI desativa as pílulas
  // Estado atrelado ao dono: ajusta DURANTE o render (o React refaz o render antes de commitar), senão um quadro mostraria o turno de A com o uid de B; o useEffect abaixo segue abortando pedidos e limpando refs.
  const [de, setDe] = useState(uid);
  if (de !== uid) { setDe(uid); setTurnos([]); setRascunho(""); comentar(null); setUso(null); setEnviando(false); setBloqueio("abrindo"); }
  const aberta = useRef(false), enviou = useRef(false), sujo = useRef(false), erros = useRef(0), proximo = useRef(0), abertura = useRef(0), versaoUso = useRef(0); // versaoUso: o POST que grava o uso invalida a cota que um GET mais antigo ainda traz
  const delecoes = useRef(new Set<Promise<void>>()), semDeleteUlt = useRef(false);
  const voo = useRef<AbortController | null>(null), parado = useRef<AbortController | null>(null), avaliando = useRef(new Set<number>()), prep = useRef<AbortController[]>([]), ativos = useRef(new Set<AbortController>());
  const novo = () => { const c = new AbortController(); ativos.current.add(c); return c; };
  /** A cota que vem numa resposta: pedidos em paralelo (Piggy e agentes) chegam fora de ordem, e um redirect traz a leitura de antes do modelo, então uma
   * contagem MENOR com o mesmo limite não se aplica às cegas (pode ser velha, ou o mês que virou): relê a cota no servidor, e a leitura só entra se
   * nenhuma outra cota foi gravada enquanto ela voava. Limite novo (troca de plano) entra direto. */
  const usoAtual = useRef(uso); usoAtual.current = uso;
  const aplicarUso = (n: Uso) => {
    versaoUso.current++; const u = usoAtual.current;
    if (!u || u.limit !== n.limit || n.used >= u.used) { usoAtual.current = n; setUso(n); return; }
    const alvo = dono.current, v0 = versaoUso.current, c = novo();
    void lerRecurso("/ai/messages?limit=1", conversaSchema, c).then((r) => { if (dono.current === alvo && versaoUso.current === v0) { usoAtual.current = r.usage; setUso(r.usage); } }, () => {}).finally(() => ativos.current.delete(c));
  };
  const ag = useConversaAgentes({ uid, dono, ativos, voo, proximo, setTurnos, setRascunho, aplicarUso, esgotarCota: () => { versaoUso.current++; const u = usoAtual.current; if (u) { const e = { ...u, used: Math.max(u.used, u.limit) }; usoAtual.current = e; setUso(e); } }, falhou });
  const preparar = useCallback(async (semDelete = false): Promise<Uso | null> => {
    const alvo = dono.current; if (alvo === null) return null;
    semDeleteUlt.current = semDelete;
    for (const c of prep.current) c.abort(); // só o GET da cota: o DELETE da abertura não se aborta (fila única)
    const cg = novo(); prep.current = [cg]; setBloqueio("abrindo");
    const vivo = () => prep.current[0] === cg && dono.current === alvo, e0 = erros.current, v0 = versaoUso.current; // erro de POST durante o DELETE: ele pode ter saído antes do turno acabar no servidor
    const cd = semDelete ? null : novo(), d = cd ? cancelarPendencia(cd) : undefined;
    if (cd && d) { const q = d.then(() => {}, () => {}); delecoes.current.add(q); void q.then(() => { delecoes.current.delete(q); ativos.current.delete(cd); }); }
    try {
      const [, cota] = await Promise.all([d, lerRecurso("/ai/messages?limit=1", conversaSchema, cg), ...delecoes.current]);
      if (!vivo()) return null;
      if (erros.current === e0 && !semDelete) sujo.current = false;
      if (versaoUso.current === v0) { usoAtual.current = cota.usage; setUso(cota.usage); }
      setBloqueio("livre"); return versaoUso.current === v0 ? cota.usage : usoAtual.current; // leitura descartada (outra cota gravou no meio): vale a cota da tela
    } catch (e) { if (vivo()) { falhou(e); setBloqueio("falhou"); } return null; }
    finally { ativos.current.delete(cg); }
  }, [falhou]);
  /** DELETE da saída: não bloqueia a tela e falha em silêncio (o próximo foco repete), mas segura o próximo `preparar` (fila). Só vale para a conta que o pediu. */
  const sair = useCallback((alvo: number, turno?: number) => {
    if (dono.current !== alvo) return;
    const c = novo();
    const p = (async () => {
      try {
        const r = await cancelarPendencia(c);
        if (dono.current === alvo && r.cancelada && turno !== undefined) { const nota = r.cancelada; setTurnos((ts) => ts.map((t) => t.id === turno ? { ...t, nota } : t)); }
      } catch (e) { if (dono.current === alvo) falhou(e); }
      finally { ativos.current.delete(c); }
    })();
    delecoes.current.add(p); void p.then(() => delecoes.current.delete(p)); // nunca rejeita (o catch acima)
  }, [falhou]);
  const abrir = useCallback(() => { aberta.current = true; abertura.current++; enviou.current = false; void preparar(!!voo.current); }, [preparar]);
  const fechar = useCallback(() => {
    if (!aberta.current) return;
    aberta.current = false; for (const c of prep.current) c.abort(); prep.current = [];
    const emVoo = voo.current ? proximo.current : null; // o turno em voo é sempre o último
    setRascunho(""); comentar(null); ag.zerar(); setTurnos((ts) => ts.filter((t) => t.id === emVoo)); // zerar: o context dos agentes não sobrevive à saída da aba
    const alvo = dono.current;
    if (enviou.current && !voo.current && alvo !== null) { enviou.current = false; sair(alvo); }
  }, [sair, ag.zerar]);
  const enviar = async (texto: string, assunto?: Pergunta, doCampo = false) => { // doCampo: só o envio do campo limpa o rascunho; chip e sugestão preservam o que está digitado. No erro, qualquer origem volta ao campo vazio
    const message = texto.trim(), alvo = dono.current, minha = abertura.current;
    const pode = () => dono.current === alvo && aberta.current && !voo.current && !oculto.current && !!message && message.length <= 2000;
    if (alvo === null || !pode() || bloqueio !== "livre" || !uso || uso.used >= uso.limit) return;
    if (sujo.current) { const cota = await preparar(); if (!cota || !pode() || cota.used >= cota.limit) return; } // depois de erro: DELETE bloqueante e cota relida antes de enviar
    const ctrl = new AbortController(); ativos.current.add(ctrl); voo.current = ctrl;
    const id = ++proximo.current; enviou.current = true;
    setEnviando(true); if (doCampo) setRascunho((r) => r === texto ? "" : r); setTurnos((ts) => [...ts, { id, pergunta: message, assunto, ...NOVO }]);
    const vivo = () => dono.current === alvo && voo.current === ctrl;
    const atualizar = (f: (t: Turno) => Partial<Turno>) => setTurnos((ts) => ts.map((t) => t.id === id ? { ...t, ...f(t) } : t));
    const concluir = () => {
      voo.current = null; setEnviando(false);
      if (aberta.current) enviou.current = true; // o turno terminou nesta abertura: a saída cancela o que ele armou
      else { enviou.current = false; sair(alvo, id); }
    };
    let fim = false;
    try {
      await conversarFluxo(message, ctrl, (bruto) => {
        if (!vivo()) return;
        const e = lerEvento(bruto); if (!e) return;
        if (e.tipo === "passo") atualizar((t) => ({ passos: [...t.passos, e.texto], previa: e.previa }));
        else if (e.tipo === "bloco") atualizar((t) => ({ blocos: [...t.blocos, e.bloco] }));
        else if (e.tipo === "delta") atualizar((t) => ({ parcial: t.parcial + e.texto }));
        else if (e.tipo === "reinicio") atualizar(() => ({ parcial: "" }));
        else if (e.tipo === "erro") throw new ErroDeApi(500, e.mensagem ?? "erro");
        else {
          fim = true; atualizar(() => ({ resposta: e.reply, parcial: "", messageId: e.message_id, chips: aberta.current ? e.chips : [] }));
          aplicarUso(e.usage);
          if (aberta.current) AccessibilityInfo.announceForAccessibility("Piggy respondeu");
          concluir();
        }
      });
      if (!fim && vivo()) throw new Error("stream sem fim");
    } catch (e) {
      if (vivo()) {
        sujo.current = true; erros.current++;
        if (parado.current === ctrl) atualizar(() => ({ interrompida: true }));
        else { falhou(e); atualizar(() => ({ erro: mensagemRecurso(e), parcial: "" })); if (aberta.current && abertura.current === minha) setRascunho((r) => r || message); }
        concluir();
      }
    } finally { ativos.current.delete(ctrl); }
  };
  const parar = () => { const c = voo.current; if (!c || !aberta.current) return; parado.current = c; c.abort(); };
  /** `true` = o POST foi 2xx (gravou de verdade); `false` = falhou e o toast é da UI; `null` = nada gravado nem falha: descartada (já há um POST deste turno, sem dono ou sem `messageId`) ou abortada pela troca de dono — quem chama não agradece nem avisa. Otimista, com reversão de nota E motivo. Um POST por turno por vez. */
  const gravarAvaliacao = async (turno: Turno, { nota, motivo, texto }: Avaliacao): Promise<boolean | null> => {
    const alvo = dono.current, id = turno.messageId;
    if (alvo === null || id === null || avaliando.current.has(turno.id)) return null;
    const { feedback: antes, motivo: motivoAntes } = turno;
    const gravar = (feedback: 1 | -1 | null, motivo: Motivo | null) => setTurnos((ts) => ts.map((t) => t.id === turno.id ? { ...t, feedback, motivo } : t));
    avaliando.current.add(turno.id); setSalvando(true); gravar(nota, nota === -1 ? motivo ?? null : null); const c = novo();
    try { await avaliarResposta(id, { nota, motivo, texto }, c); return true; }
    catch (e) { if (dono.current !== alvo) return null; gravar(antes, motivoAntes); falhou(e); return false; }
    finally { ativos.current.delete(c); avaliando.current.delete(turno.id); setSalvando(avaliando.current.size > 0); }
  };
  /** 👍👎: tocar a nota já marcada manda `null` (e zera o motivo no servidor). */
  const avaliar = (turno: Turno, nota: 1 | -1) => gravarAvaliacao(turno, { nota: turno.feedback === nota ? null : nota });
  const antes = useRef(uid);
  useEffect(() => {
    const prev = antes.current; antes.current = uid;
    if (prev === null || prev === uid) return;
    // Troca de conta (ou logout): aborta tudo e NÃO manda DELETE — a pendência do outro dono não é desta sessão.
    for (const c of ativos.current) c.abort();
    ativos.current.clear(); delecoes.current.clear(); prep.current = []; voo.current = null; sujo.current = false; enviou.current = false;
    setTurnos([]); setRascunho(""); comentar(null); setUso(null); setEnviando(false); setBloqueio("abrindo");
    if (aberta.current && uid !== null) void preparar();
  }, [uid, preparar]);
  useEffect(() => () => { for (const c of ativos.current) c.abort(); }, []);
  return <Contexto.Provider value={{ turnos, uso, bloqueio, enviando, rascunho, setRascunho, abrir, fechar, tentarNovamente: () => void preparar(semDeleteUlt.current || !!voo.current), enviar, parar, avaliar, gravarAvaliacao, avaliando: salvando, comentario, comentar, agentes: ag }}>{children}</Contexto.Provider>;
}
