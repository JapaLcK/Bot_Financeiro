import { ContratoInvalido, ErroDeApi, RenovacaoIndisponivel, RequisicaoSuperada, SessaoExpirada } from "@/api/client";
import type { Conexao } from "@/api/schemas/openFinance";
import { GENERICO, textoDaFalha } from "@/features/auth/entrar";
import { perfil } from "@/services/auth";
import { conexoes, registrarItem } from "@/services/openFinance";
import { capturarItemBancario, concluirTentativaBancaria, lerTentativaBancaria, marcarTentativaBancariaVista, type TentativaBancaria } from "@/storage/secure";

/**
 * A volta do OAuth do banco (`<scheme>://open-finance-volta?itemId=…`, que a
 * Pluggy abre depois do consentimento): confere no servidor se o item virou
 * conexão e, se não virou, registra. Sem JSX, como `features/auth/entrar.ts`:
 * o Jest exercita com os serviços reais, e a rota só desenha o estado.
 *
 * O link é ENTRADA NÃO CONFIÁVEL: só o `itemId` é lido (e validado), o `uid`
 * vem sempre de `perfil()`. O servidor é a fronteira: o `POST /pluggy-item`
 * confere o dono na Pluggy (`clientUserId`).
 *
 */

/** Mesma regra de `_ITEM_ID_OK` (core/services/pluggy.py); `tests/test_app_espelhos.py` compara. */
const ID_DO_ITEM = /^[A-Za-z0-9_-]{1,64}$/;

export const INTERVALO_MS = 3_000;
/** Medido no iPhone: o item fica em `updating` por 16 s, 42 s e ≥ ~89 s depois do `onSuccess`. */
export const JANELA_MS = 300_000;
export const MAX_POSTS = 3;

let widget = false;

/**
 * Enquanto autorizando mantém o widget em foco, native-intent captura o item
 * no cofre sem cobrir o widget. Ao fechar, a rota organiza pelo mesmo marcador.
 */
export function definirWidgetAberto(v: boolean): void {
  widget = v;
}
export function widgetAberto(): boolean {
  return widget;
}

/** Estados terminais não podem ser readotados pelo retorno de um link. */
const MORTOS = new Set(["removed", "item_missing"]);

export const SEM_SENHA = "Antes de conectar um banco, crie a senha da sua conta pelo link que enviamos por e-mail.";

export type EstadoVolta =
  | { fase: "esperando-trava" }
  | { fase: "conferindo"; instavel: boolean }
  | { fase: "conectado"; ui: Conexao["ui"] }
  | { fase: "ainda-conferindo" }
  | { fase: "organizando" }
  | { fase: "sem-item" }
  | { fase: "escolher-conexao" }
  | { fase: "erro"; texto: string };

export interface Dependencias {
  agora: () => number;
  esperar: (ms: number) => Promise<void>;
  cancelado: () => boolean;
  aoMudar: (estado: EstadoVolta) => void;
  /** `useSessao().expirou`: a pilha troca para o login. */
  expirou: (aviso: string) => void;
  controlador?: AbortController;
}

/** O `itemId` do link, ou `null` se ausente, repetido (array), vazio ou fora da regra do servidor. */
export function itemDoLink(valor: unknown): string | null {
  return typeof valor === "string" && ID_DO_ITEM.test(valor) ? valor : null;
}

const achar = (lista: { connections: Conexao[] }, itemId: string) =>
  lista.connections.find((c) => c.provider_item_id === itemId);

const carimboAtual = (t: TentativaBancaria, c: Conexao) => !!c.reconnected_at &&
  (!t.reconnected_antes || Date.parse(c.reconnected_at) > Date.parse(t.reconnected_antes));
const syncAtual = (c: Conexao) => !!c.last_sync_at && !!c.reconnected_at && Date.parse(c.last_sync_at) >= Date.parse(c.reconnected_at);

// A pausa/retomada pode sobrepor o fim de uma chamada ao novo controller.
// Deduplica somente o POST em voo; cada rodada continua conferindo seu snapshot.
const registros = new Map<string, Promise<Awaited<ReturnType<typeof registrarItem>> | null>>();
async function registrarTentativa(uid: number, t: TentativaBancaria, itemId: string, controlador?: AbortController) {
  const chave = `${t.sessao}:${t.tentativa_id}`;
  const emVoo = registros.get(chave);
  if (emVoo) return emVoo;
  const trabalho = (async () => {
    const corrente = await lerTentativaBancaria(uid);
    if (corrente?.tentativa_id !== t.tentativa_id || corrente.visto_no_servidor) return null;
    const s = await registrarItem(uid, itemId, controlador);
    const c = achar(s, itemId);
    if (c && !MORTOS.has(c.ui.state) && (t.modo === "nova" || carimboAtual(t, c))) await marcarTentativaBancariaVista(t.tentativa_id);
    return s;
  })();
  registros.set(chave, trabalho);
  try { return await trabalho; }
  finally { if (registros.get(chave) === trabalho) registros.delete(chave); }
}

/** Falha que não diz nada sobre o item: rede, tempo limite, 5xx, 429, renovação instável. */
function transitoria(e: unknown): boolean {
  if (e instanceof RenovacaoIndisponivel) return true;
  if (!(e instanceof ErroDeApi)) return true; // TypeError (rede), AbortError (comLimite)
  return e.status >= 500 || e.status === 429;
}

function textoDoErro(e: unknown): string {
  const detalhe = (e instanceof ErroDeApi ? (e.corpo as { detail?: { error?: unknown } } | null)?.detail : null) ?? null;
  if (detalhe?.error === "password_required") return SEM_SENHA;
  return textoDaFalha(e);
}

/**
 * Laço SEQUENCIAL: espera `INTERVALO_MS` depois de a tentativa anterior
 * terminar, por `JANELA_MS` de relógio de parede. O prazo é conferido ANTES da
 * espera: quem volta do segundo plano já fora do prazo ganha uma última
 * tentativa. Uma tentativa = GET; sem o item vivo, POST (no máximo
 * `MAX_POSTS` por chamada; POST com resposta perdida aparece no GET seguinte).
 * Item em `updating` segue consultando, sem POST, até sair de `updating`; se a
 * janela fechar antes, `organizando`.
 *
 * ponytail: visto o item, uma lista sem ele não faz POST (ressuscitaria um banco
 * desconectado em outro aparelho); no fim da janela mostra `organizando`, e a
 * tela de Conexões mostra a verdade (a linha só some da lista por DELETE).
 */
export async function conferirVolta(link: unknown, d: Dependencias): Promise<void> {
  let itemId = itemDoLink(link);
  if (link !== undefined && link !== null && !itemId) return d.aoMudar({ fase: "sem-item" });
  let inicial: Awaited<ReturnType<typeof lerTentativaBancaria>>;
  try {
    inicial = await lerTentativaBancaria();
    if (itemId && inicial) await capturarItemBancario(itemId, inicial.tentativa_id);
  } catch {
    if (d.cancelado()) return;
    return d.aoMudar({ fase: "erro", texto: "Não conseguimos ler o retorno do banco neste aparelho. Tente de novo." });
  }
  if (d.cancelado()) return;
  if (!itemId && !inicial) return d.aoMudar({ fase: "sem-item" });
  itemId ??= inicial?.item_id ?? null;
  const daRodada = (t: TentativaBancaria | null) => !!inicial && t?.tentativa_id === inicial.tentativa_id && t?.sessao === inicial.sessao;

  const prazo = d.agora() + JANELA_MS;
  let uid: number | null = null;
  let posts = 0;
  let instavel = false;
  let visto = inicial?.item_id === itemId && inicial?.visto_no_servidor === true;
  d.aoMudar({ fase: "conferindo", instavel });

  /** Mostra a conexão; `true` = parar. Em `updating` continua consultando. */
  const parou = (ui: Conexao["ui"]) => {
    d.aoMudar({ fase: "conectado", ui });
    if (ui.state !== "updating") return true;
    visto = true;
    instavel = false;
    return false;
  };

  for (;;) {
    try {
      uid ??= (await perfil()).user_id;
      if (d.cancelado()) return;
      const lida = await lerTentativaBancaria(uid);
      let tentativa = daRodada(lida) ? lida : null;
      if (d.cancelado()) return;
      if (inicial && !tentativa) return d.aoMudar({ fase: "sem-item" });
      // onSuccess/native-intent pode entregar a pista depois do primeiro GET.
      // Só a tentativa original desta rodada pode preencher o item ainda ausente.
      itemId ??= itemDoLink(tentativa?.item_id);
      const snapshot = await conexoes(uid, d.controlador);
      const candidatos = snapshot.connections.filter((c) => c.provider_item_id &&
        !tentativa?.ids_antes.includes(c.provider_item_id) && !MORTOS.has(c.ui.state));
      if (!itemId && candidatos.length > 1 && tentativa) return d.aoMudar({ fase: "escolher-conexao" });
      if (!itemId && candidatos.length === 1 && tentativa) {
        itemId = candidatos[0]!.provider_item_id;
        if (itemId) {
          await capturarItemBancario(itemId, tentativa.tentativa_id);
          const capturada = await lerTentativaBancaria(uid);
          tentativa = daRodada(capturada) ? capturada : null;
          if (d.cancelado()) return;
          if (!tentativa) return d.aoMudar({ fase: "sem-item" });
        }
      }
      const atual = itemId ? achar(snapshot, itemId) : null;
      const daTentativa = tentativa?.item_id === itemId ? tentativa : null;
      const reconectando = daTentativa?.modo === "reconectar" ? daTentativa : null;
      const atualConfirmada = !!atual && (!reconectando || carimboAtual(reconectando, atual));
      if (d.cancelado()) return;
      if (atual && MORTOS.has(atual.ui.state)) {
        if (daTentativa) await concluirTentativaBancaria(daTentativa.tentativa_id);
        return d.aoMudar({ fase: "erro", texto: "Esse banco foi desconectado. Inicie uma nova conexão." });
      }
      if (atual && atualConfirmada) {
        if (daTentativa) await marcarTentativaBancariaVista(daTentativa.tentativa_id);
        if (d.cancelado()) return;
        const ui = reconectando && ["updated", "partial"].includes(atual.ui.state) && !syncAtual(atual)
          ? { state: "updating", label: "Atualizando…", detail: null } : atual.ui;
        if (parou(ui)) {
          if (daTentativa && ["updated", "partial"].includes(ui.state)) await concluirTentativaBancaria(daTentativa.tentativa_id);
          return;
        }
      }
      // Pista de callback só pode adotar item na tentativa desta sessão.
      // Sem isso um link antigo ressuscitaria banco removido após reinstalar.
      const corrente = await lerTentativaBancaria(uid);
      if (d.cancelado()) return;
      const podeRegistrar = tentativa && corrente?.tentativa_id === tentativa.tentativa_id && itemId && tentativa.item_id === itemId &&
        !tentativa.visto_no_servidor && (!reconectando || tentativa.autorizacao_recebida) && Date.now() - tentativa.iniciada_em <= 60 * 60_000;
      if (tentativa && posts < MAX_POSTS && !visto && podeRegistrar) {
        posts += 1;
        const resposta = await registrarTentativa(uid, tentativa, itemId!, d.controlador);
        const registrado = resposta ? achar(resposta, itemId!) : null;
        if (d.cancelado()) return;
        if (registrado && (!reconectando || carimboAtual(tentativa, registrado))) {
          await marcarTentativaBancariaVista(tentativa!.tentativa_id);
          if (d.cancelado()) return;
          const ui = reconectando && ["updated", "partial"].includes(registrado.ui.state) && !syncAtual(registrado)
            ? { state: "updating", label: "Atualizando…", detail: null } : registrado.ui;
          if (parou(ui)) {
            if (["updated", "partial"].includes(ui.state)) await concluirTentativaBancaria(tentativa!.tentativa_id);
            return;
          }
        }
      }
      if (instavel) {
        instavel = false;
        d.aoMudar({ fase: "conferindo", instavel });
      }
    } catch (e) {
      if (d.cancelado()) return;
      if (e instanceof ErroDeApi && (e.corpo as { detail?: { code?: string } } | undefined)?.detail?.code === "OF_ITEM_REMOVED") {
        try {
          const t = await lerTentativaBancaria(uid ?? undefined);
          if (t?.item_id === itemId) await concluirTentativaBancaria(t.tentativa_id);
        } catch {
          // A lápide do servidor continua válida mesmo se o cofre não permitir
          // limpar o marcador; nenhuma tentativa extra de adoção é feita aqui.
        }
        if (d.cancelado()) return;
        return d.aoMudar({ fase: "erro", texto: "Esse banco foi desconectado. Inicie uma nova conexão." });
      }
      // Antes do resto: `RequisicaoSuperada` é um 409 (a conta trocou), não o 409 "outra conta" do servidor.
      if (e instanceof RequisicaoSuperada) return;
      if (e instanceof SessaoExpirada) return d.expirou(e.detalhe);
      // Visto o item, o banco já conectou: falha definitiva vira `organizando`, e a
      // transitória não muda a tela (segue em `updating`). "Definitiva" inclui
      // `ContratoInvalido` (status 200: `transitoria()` o dá como não transitório), e
      // por isso este ramo vem antes do dele: depois de visto, `organizando`; antes,
      // `erro`. Coberto pelo "contrato quebrado" do N8.
      if (visto && !transitoria(e)) return d.aoMudar({ fase: "organizando" });
      if (e instanceof ContratoInvalido) return d.aoMudar({ fase: "erro", texto: GENERICO });
      if (!transitoria(e)) return d.aoMudar({ fase: "erro", texto: textoDoErro(e) });
      if (!visto && !instavel) {
        instavel = true;
        d.aoMudar({ fase: "conferindo", instavel });
      }
    }
    if (d.agora() >= prazo) return d.aoMudar(visto ? { fase: "organizando" } : { fase: "ainda-conferindo" });
    await d.esperar(INTERVALO_MS);
    if (d.cancelado()) return;
  }
}
