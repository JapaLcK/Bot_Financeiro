import { RequisicaoSuperada, SessaoExpirada } from "@/api/client";
import type { Conexao, Refresh } from "@/api/schemas/openFinance";
import { JANELA_MS, transitoria } from "@/features/openFinance/volta";

/**
 * O "Atualizar agora" do aviso de banco, sem JSX: a classificação do que o botão faz e a máquina do pedido, com o relógio e a rede INJETADOS (`Deps`) para o Jest.
 * O estado de módulo e o hook moram em `usarAtualizarBanco.ts`. Nenhuma regra de estado nasce aqui: o app só mapeia o `ui.state` do servidor para uma ação,
 * como `conexoes.tsx` já faz.
 */

/** Espelhos de `core.services.pluggy_health` (`_AUTORIZE_NO_APP`, `_DETALHE_COLETA_VENCIDA`): `tests/test_app_espelhos.py` compara. */
export const AUTORIZE_NO_APP = "Autorize o acesso no app do banco";
export const COLETA_VENCIDA = "Está demorando mais que o normal — atualize de novo";
export const ATUALIZANDO_SOZINHO = "O banco está atualizando. Seus dados aparecem aqui sozinhos quando terminar.";
export const FALHA = "Não conseguimos atualizar agora. Tente de novo.";
export const AINDA = "O banco está demorando. Seus dados aparecem aqui quando terminar.";

/** Mesma cadência do `of-status-poll.js` (`tests/test_app_espelhos.py` compara): 5/10/20/40 s e depois 60 s, do FIM de uma leitura ao pedido seguinte (sequencial, sem sobreposição). O TETO é diferente DE PROPÓSITO: 5 min aqui (`JANELA_MS`, o da volta do OAuth), 30 min no web. */
const CADENCIA_MS = [5_000, 10_000, 20_000, 40_000];
const DEPOIS_MS = 60_000;
/** Estados em que um item "em coleta" segue esperando; os outros (erro, ação necessária, estado novo) encerram. Mesma lista do `SEGUE_ESPERANDO` do web (o teste espelho compara). */
const SEGUE = new Set(["updated", "updating", "partial", "no_accounts"]);
/** Estados do veredito que NÃO são problema (`rate_limited` é só do POST: "acabou de atualizar"). */
const OK = new Set(["updated", "rate_limited", "updating"]);

export type AcaoBanco =
  | { tipo: "atualizar" } // (a) pedido sem o usuário: POST /refresh
  | { tipo: "acompanhar" } // (a0) já coletando: só acompanha
  | { tipo: "reconectar"; itemId: string; banco: string } // (b) reautenticar no banco
  | { tipo: "ver"; texto: string | null }; // (c) sem conserto pelo app

export type EstadoAtualizar =
  | { fase: "ocioso"; falha?: boolean }
  | { fase: "enviando" }
  | { fase: "acompanhando" }
  /** Rodada que terminou com problema (ou estourou o teto): fica o texto e SEM "Atualizar agora", para não haver laço. */
  | { fase: "resultado"; texto: string };
export const OCIOSO: EstadoAtualizar = { fase: "ocioso" };

export type Deps = {
  agora: () => number;
  esperar: (ms: number) => Promise<void>;
  /** `p.operacao().atual`: falso em segundo plano, puxar para atualizar, troca de conta e portão. */
  atual: () => boolean;
  enviar: () => Promise<Refresh>;
  ler: () => Promise<Conexao[]>;
  aoMudar: (e: EstadoAtualizar) => void;
  /** `p.atualizar`: relê os recursos; o aviso some porque o servidor deixa de mandar o motivo. */
  recarregar: () => void;
  /** `p.falhou`: 401 vira login; 402 e 403 do onboarding, o portão. */
  falhou: (e: unknown) => void;
  sucesso: () => void;
  aviso: () => void;
  anunciar: (t: string) => void;
};

const nomeDe = (c: Conexao) => c.institution_name ?? "banco";
const semPonto = (t: string) => t.replace(/\.+$/, "");
const frase = (banco: string | null, detalhe: string) => `${banco ?? "Seu banco"}: ${semPonto(detalhe)}.`;

/** Prioridade (b) > (a) > (a0) > (c): reautenticar vence, depois pedir, depois só esperar. `podeReconectar` = `of_banks_max !== 0` (como o `conexoes.tsx`). */
export function classificar(conexoes: Conexao[], podeReconectar: boolean): AcaoBanco {
  const cat = (c: Conexao) => {
    const { state, detail } = c.ui;
    if (state === "needs_user_action") return detail === AUTORIZE_NO_APP || !c.provider_item_id || !podeReconectar ? "c" : "b";
    if (["updated", "partial", "error_recoverable", "no_accounts"].includes(state)) return "a";
    if (state === "updating") return detail === COLETA_VENCIDA ? "a" : "a0";
    return "c"; // item_missing, paused, removed e estado que este arquivo não conhece
  };
  const grupos = conexoes.map((c) => [cat(c), c] as const);
  const b = grupos.find(([g]) => g === "b")?.[1];
  if (b) return { tipo: "reconectar", itemId: b.provider_item_id!, banco: nomeDe(b) };
  if (grupos.some(([g]) => g === "a")) return { tipo: "atualizar" };
  if (grupos.some(([g]) => g === "a0")) return { tipo: "acompanhar" };
  const frases = grupos.filter(([g]) => g === "c").map(([, c]) => frase(c.institution_name, c.ui.detail ?? c.ui.label));
  return { tipo: "ver", texto: frases.length ? frases.join(" ") : null };
}

type Item = { banco: string | null; estado: string; detalhe: string };
/** `null` = tudo em dia; senão, uma frase por banco com a instrução do servidor. */
function julgar(itens: Item[]): string | null {
  const ruins = itens.filter((i) => !OK.has(i.estado));
  return ruins.length ? ruins.map((i) => frase(i.banco, i.detalhe)).join(" ") : null;
}

const parar = (d: Deps) => d.aoMudar(OCIOSO);

function falhar(d: Deps, e: unknown, comoResultado: boolean) {
  d.falhou(e);
  if (!d.atual() || e instanceof SessaoExpirada || e instanceof RequisicaoSuperada) return parar(d);
  d.aoMudar(comoResultado ? { fase: "resultado", texto: FALHA } : { fase: "ocioso", falha: true });
  d.aviso(); d.anunciar(FALHA);
}

/**
 * Fim da rodada com a lista de depois: tudo em dia → `sucesso()`; senão o texto fica e SEM botão.
 * A recarga vai primeiro só por clareza: o estado final vale pela operação do provider que nasce DEPOIS dela (`correr` em usarAtualizarBanco.ts, regra do "resultado"), e a ordem das duas linhas não muda isso.
 * ponytail: se o motivo do aviso vem de outra fonte que não esta conexão (POST ok, aviso fica), "Atualizar agora" volta a cada recarga e o servidor responde `rate_limited` (cooldown de 120 s); sem fonte do motivo no app, não há como saber.
 * ponytail: a recarga cancela as outras operações em voo (perfil, assinaturas, receita); elas se reconciliam na releitura, como no puxar para atualizar.
 */
function concluir(d: Deps, texto: string | null) {
  d.recarregar();
  if (texto === null) { parar(d); d.sucesso(); } else { d.aoMudar({ fase: "resultado", texto }); d.aviso(); d.anunciar(texto); }
}
const daLista = (lista: Conexao[]) => julgar(lista.map((c) => ({ banco: c.institution_name, estado: c.ui.state, detalhe: c.ui.detail ?? c.ui.label })));

/** Para quando todo item acompanhado sumiu, mudou o `last_sync_at` (o sync do fim da coleta) ou saiu dos estados de espera. */
const terminou = (lista: Conexao[], base: Map<string, string | null>) =>
  [...base].every(([id, desde]) => { const c = lista.find((x) => x.provider_item_id === id); return !c || c.last_sync_at !== desde || !SEGUE.has(c.ui.state); });

/**
 * `base`: item → `last_sync_at` de quando o acompanhamento começou. Teto de `JANELA_MS` (5 min, o da volta do OAuth) EXATOS: a última espera é cortada para a última leitura cair no prazo.
 * Se nenhuma leitura deu certo até o teto, o app nunca soube de nada: vale a falha aprovada (com `aviso()`), não o "demorando".
 */
export async function acompanhar(d: Deps, base: Map<string, string | null>): Promise<void> {
  d.aoMudar({ fase: "acompanhando" });
  const prazo = d.agora() + JANELA_MS;
  let confirmou = false;
  for (let k = 0; ; k++) {
    await d.esperar(Math.min(CADENCIA_MS[k] ?? DEPOIS_MS, prazo - d.agora()));
    if (!d.atual()) return parar(d);
    let lista: Conexao[] | null = null;
    try { lista = await d.ler(); confirmou = true; } catch (e) {
      if (!d.atual()) return parar(d);
      if (!transitoria(e)) return falhar(d, e, true); // rede, 5xx e 429 só seguem a cadência
    }
    if (!d.atual()) return parar(d);
    if (lista && terminou(lista, base)) return concluir(d, daLista(lista));
    if (d.agora() >= prazo) {
      const texto = confirmou ? AINDA : FALHA;
      d.aoMudar({ fase: "resultado", texto }); if (!confirmou) d.aviso(); d.anunciar(texto); return;
    }
  }
}

const baseDe = (conexoes: Conexao[]) => new Map(conexoes.flatMap((c) => c.provider_item_id ? [[c.provider_item_id, c.last_sync_at] as const] : []));

/** (a0) e o estouro de 60 s do POST: acompanha quem está em `updating` na lista lida. Sem ninguém coletando, a lista já é o veredito. */
export async function acompanharLista(d: Deps, lista: Conexao[]): Promise<void> {
  const base = baseDe(lista.filter((c) => c.ui.state === "updating"));
  if (base.size) return acompanhar(d, base);
  concluir(d, daLista(lista));
}

/** (a): o POST. Resposta ok → veredito por item; `still_updating` → acompanha; estouro do prazo → lê e acompanha; o resto é falha (o botão volta). */
export async function atualizar(d: Deps): Promise<void> {
  d.aoMudar({ fase: "enviando" }); d.anunciar("Atualizando os dados do banco");
  try {
    let r: Refresh;
    try { r = await d.enviar(); } catch (e) {
      if (!d.atual() || !(e instanceof Error && e.name === "AbortError")) throw e;
      return acompanharLista(d, await d.ler()); // o prazo de 60 s cortou o POST, mas o servidor pode ter terminado
    }
    if (!d.atual()) return parar(d);
    const coletando = r.sync.items.filter((i) => i.still_updating);
    if (coletando.length) return acompanhar(d, new Map(coletando.map((i) => [i.item_id, r.connections.find((c) => c.provider_item_id === i.item_id)?.last_sync_at ?? null])));
    concluir(d, julgar(r.sync.items.map((i) => ({ banco: i.institution, estado: i.state, detalhe: i.detail ?? i.label }))));
  } catch (e) {
    if (!d.atual()) return parar(d);
    falhar(d, e, false);
  }
}
