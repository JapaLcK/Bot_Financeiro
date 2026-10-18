import { useEffect, useSyncExternalStore } from "react";
import { AccessibilityInfo } from "react-native";
import { RequisicaoSuperada } from "@/api/client";
import type { Conexao } from "@/api/schemas/openFinance";
import { aviso, sucesso } from "@/ui/haptics";
import { atualizarBancos, conexoes, limiteBancario } from "@/services/openFinance";
import { acompanharLista, atualizar, classificar, OCIOSO, type AcaoBanco, type Deps, type EstadoAtualizar } from "./atualizarBanco";
import { usePainel } from "./provider";

type Painel = ReturnType<typeof usePainel>;
/** A leitura do snapshot que classifica o botão; `chave` = `uid:versao` do painel (recarregou, releu). Sem `conexoes` nem `falhou` = lendo. */
type Leitura = { chave: string; conexoes?: Conexao[]; pode?: boolean; falhou?: boolean;
  /** (a0) cuja partida ficou ADIADA porque havia uma rodada em voo; `vale` = a operação desta leitura ainda é a do provider. A rodada que acabar a dispara (`reavaliar`), uma vez. */
  pendente?: boolean; vale?: () => boolean };
type Estado = { uid: number; vale: () => boolean; maquina: EstadoAtualizar; leitura: Leitura | null };

/*
 * REGRA DO "RESULTADO" (e da falha, e de "atualizando"): pertence à RODADA e à VISTA em que nasceu. "A mesma vista" NÃO é um número copiado da `versao` do provider
 * (a `versao` sobe em 3 lugares: carga do portão, `atualizar` e o `finally` do `mudarPerfil` quando o PUT estoura, este SEM `cancelar()`; uma cópia local dessincroniza no terceiro).
 * É a PRÓPRIA operação do provider: o estado guarda `vale` = `operacao().atual`, que o provider derruba em todo `cancelar()` (puxar para atualizar, segundo plano, troca de conta, portão)
 * e em troca de dono. Uma rodada viva vale por `op.atual`; ao terminar com a recarga dela, o estado final passa a valer pela operação nova que nasce DEPOIS dessa recarga (`validade`),
 * que é a que o provider realmente renderiza. A mesma função decide o que se VÊ e o que se pode TOCAR (`ocupado`), então nunca há botão morto.
 *   estado \ evento                 | toque | fim da rodada com problema | recarga que cancela (puxar, segundo plano, troca de conta, portão) | bump sem cancelar (`mudarPerfil` estourado) | X do aviso | outro `uid`
 *   ocioso                          | enviando | —                       | classifica de novo (leitura nova por `uid:versao`)             | idem                                          | some tudo | ocioso
 *   enviando / acompanhando         | ignorado | → resultado ou ocioso   | `vale` cai: ocioso e botão NA HORA; a máquina para ao acordar  | a rodada segue VISÍVEL e viva (e o toque segue ignorado) | cancela e silencia: sem `aviso()`, `sucesso()`, anúncio nem recarga | ocioso
 *   resultado / falha (mesma vista) | ignorado: SEM "Atualizar" (sem laço) | — | `vale` cai: ocioso; classificação do zero ((b) "Reconectar", (a) "Atualizar agora") | segue (não é uma recarga que cancele) | some tudo | ocioso
 * LEITURA × MÁQUINA (o texto "O banco está atualizando…" só aparece com a máquina em `acompanhando`; nunca por classificação sozinha):
 *   leitura classifica (a0) \ máquina | enviando / acompanhando                      | ocioso (ou voltou a ocioso)                    | resultado
 *   leitura chega                     | partida ADIADA (`pendente`), sem texto       | parte sozinha, calada                          | nada (mesma vista)
 *   a rodada em voo acaba             | —                                            | `reavaliar`: parte sozinha UMA vez, calada, se a leitura ainda vale (bump sem cancelar: POST falha → acompanha o que o PATCH pôs em coleta) | nada
 *   recarga que cancela / X           | a leitura perde `vale` (ou é descartada): nada parte                                                                 | idem
 * A rodada que nasce SOZINHA (a0, adiada ou não) não faz barulho: sem `sucesso()`, `aviso()` nem anúncio (só `p.atualizar()` no fim e o texto na tela); só a rodada do TOQUE vibra e anuncia.
 * Estado de MÓDULO com o `uid` dentro (idioma de `avisoBanco.ts`): as 4 abas montam o `TelaPainel` ao mesmo tempo, e um estado por instância mostraria
 * "Atualizar agora" ativo na aba Gastos com o POST em voo no Resumo. O que é de um `uid` nunca é lido por outro.
 */
let S: Estado = { uid: 0, vale: () => false, maquina: OCIOSO, leitura: null };
let corrida = 0;
const ativos = new Set<AbortController>(); // operações do painel em voo (rodadas e leituras): o X do aviso as cancela
const ouvintes = new Set<() => void>();
const definir = (parcial: Partial<Estado>) => { S = { ...S, ...parcial }; ouvintes.forEach((o) => o()); };
const assinar = (o: () => void) => { ouvintes.add(o); return () => { ouvintes.delete(o); }; };
/** O que se vê e o que se toca: o estado é desta conta E ainda vale (a operação do provider não foi cancelada). */
const visivel = (uid: number) => S.uid === uid && S.vale();
const ocupado = (uid: number) => visivel(uid) && (S.maquina.fase === "enviando" || S.maquina.fase === "acompanhando");

/** Só para teste: o estado de módulo vaza entre os testes do jest. */
export function __resetAtualizarBanco() { S = { uid: 0, vale: () => false, maquina: OCIOSO, leitura: null }; corrida++; ativos.clear(); }

/** O X do aviso: cancela o que estiver em voo e silencia a rodada (sem `aviso()`, `sucesso()`, anúncio nem recarga depois de fechado). */
export function cancelarAtualizarBanco() {
  corrida++; ativos.forEach((c) => c.abort()); ativos.clear();
  definir({ maquina: OCIOSO, leitura: null });
}

/** Uma operação do painel (guarda de geração e de dono) para o `uid`. */
function correr(p: Painel, uid: number, entrada: (d: Deps) => Promise<void>, calada = false) {
  const op = p.operacao(), n = ++corrida; let validade = op.atual;
  ativos.add(op.controlador);
  const d: Deps = {
    agora: () => Date.now(), esperar: (ms) => new Promise<void>((r) => { const t = setTimeout(r, ms); op.controlador.signal.addEventListener("abort", () => { clearTimeout(t); r(); }, { once: true }); }), // acorda ao cancelar: a máquina para em vez de dormir até 60 s com o estado "acompanhando"
    atual: op.atual,
    enviar: () => atualizarBancos(uid, op.controlador), ler: async () => (await conexoes(uid, op.controlador)).connections,
    aoMudar: (maquina) => { if (n === corrida) definir({ uid, vale: () => validade(), maquina }); }, // só a corrida mais nova escreve; o barulho (aviso, sucesso, anúncio, recarga) já some com `atual()`, que o abort do X ou da troca de conta derruba
    recarregar: () => { p.atualizar(); validade = p.operacao().atual; }, // a operação nasce DEPOIS do `cancelar()` da recarga: o estado final vale por ela, não pela que a recarga acabou de derrubar
    falhou: p.falhou, sucesso: calada ? () => {} : sucesso, aviso: calada ? () => {} : aviso, anunciar: (t) => { if (!calada) AccessibilityInfo.announceForAccessibility(t); },
  };
  void entrada(d).catch(() => d.aoMudar(OCIOSO)).finally(() => { ativos.delete(op.controlador); reavaliar(p, uid); });
}

/** Fim de uma rodada: se a leitura vigente classificou (a0) mas a partida foi adiada, parte agora (uma vez). Sem isso o aviso ficaria sem leitura rodando e sem botão. */
function reavaliar(p: Painel, uid: number) {
  // As guardas de `uid` (chave e S.uid) são defesa em profundidade na fronteira entre usuários: nos estados alcançáveis, `pendente` e `fase` já barram; só o mutante que tira as três cai.
  const l = S.leitura;
  if (!l?.pendente || !l.conexoes || !l.vale?.() || !l.chave.startsWith(`${uid}:`) || S.uid !== uid || S.maquina.fase !== "ocioso") return;
  const lista = l.conexoes; definir({ leitura: { ...l, pendente: false } });
  correr(p, uid, (d) => acompanharLista(d, lista), true);
}

async function lerSnapshot(p: Painel, uid: number, versao: number) {
  const chave = `${uid}:${versao}`;
  // Dedupe só da leitura que ainda vale: o provider remontado (sair e entrar na mesma conta) recomeça a `versao` e repete a chave, mas a operação velha já caiu.
  if (S.leitura?.chave === chave && S.leitura.vale?.()) return;
  const op = p.operacao(); ativos.add(op.controlador);
  const minha: Leitura = { chave, vale: op.atual }; definir({ leitura: minha });
  const solta = () => { if (S.leitura === minha) definir({ leitura: null }); };
  try {
    const lista = (await conexoes(uid, op.controlador)).connections;
    // O limite do plano só se pergunta com candidato a reconectar (como o `conexoes.tsx`); sem resposta, não oferece.
    const pode = classificar(lista, true).tipo !== "reconectar" || (await limiteBancario(uid).then((l) => l.of_banks_max !== 0, () => false));
    if (!op.atual()) return solta();
    // Com `resultado` (ou a falha dele) visível na mesma vista, a leitura (a0) não parte nem fica pendente: só uma recarga que derruba o `vale` reabre.
    const sozinho = classificar(lista, pode).tipo === "acompanhar" && !(visivel(uid) && S.maquina.fase === "resultado"), adiada = sozinho && ocupado(uid);
    definir({ leitura: { chave, conexoes: lista, pode, pendente: adiada, vale: op.atual } });
    if (sozinho && !adiada) correr(p, uid, (d) => acompanharLista(d, lista), true);
  } catch (e) {
    if (!op.atual() || e instanceof RequisicaoSuperada) return solta();
    p.falhou(e);
    if (op.atual()) definir({ leitura: { chave, falhou: true, vale: op.atual } }); // o aviso segue com "Ver bancos conectados"
  } finally { ativos.delete(op.controlador); }
}

/** O que o corpo do aviso de banco desenha: a ação (`null` = ainda lendo, mostra o botão de hoje), o estado do pedido e o gatilho. */
export function useAtualizarBanco(): { acao: AcaoBanco | null; maquina: EstadoAtualizar; atualizar: () => void } {
  const p = usePainel(), uid = p.usuario.user_id, versao = p.versao;
  const s = useSyncExternalStore(assinar, () => S);
  useEffect(() => { void lerSnapshot(p, uid, versao); }, [uid, versao]);
  const l = s.leitura?.chave === `${uid}:${versao}` && s.leitura.vale?.() ? s.leitura : null;
  const acao: AcaoBanco | null = !l || (!l.conexoes && !l.falhou) ? null : l.conexoes ? classificar(l.conexoes, l.pode ?? false) : { tipo: "ver", texto: null };
  return { acao, maquina: s.uid === uid && s.vale() ? s.maquina : OCIOSO, atualizar: () => { if (!ocupado(uid)) correr(p, uid, atualizar); } };
}
