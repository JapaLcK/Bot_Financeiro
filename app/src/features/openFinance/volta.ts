import { ContratoInvalido, ErroDeApi, RenovacaoIndisponivel, RequisicaoSuperada, SessaoExpirada } from "@/api/client";
import type { Conexao } from "@/api/schemas/openFinance";
import { GENERICO, textoDaFalha } from "@/features/auth/entrar";
import { perfil } from "@/services/auth";
import { conexoes, registrarItem } from "@/services/openFinance";

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
 * ponytail: a rota age com QUALQUER link válido. Se a exclusão na Pluggy falhou
 * no desconectar (best-effort), reabrir um link antigo do histórico do Safari
 * faz o POST e pode ressuscitar um banco removido (`POST /pluggy-item` não olha
 * `origin='removed'`). O marcador "conexão em andamento" fecha isso, no PR das
 * telas 5–7.
 */

/** Mesma regra de `_ITEM_ID_OK` (core/services/pluggy.py); `tests/test_app_espelhos.py` compara. */
const ID_DO_ITEM = /^[A-Za-z0-9_-]{1,64}$/;

export const INTERVALO_MS = 3_000;
export const JANELA_MS = 120_000;
export const MAX_POSTS = 3;

/** Conexão nesses estados não conta como "já está lá": o POST a reescreve. */
const MORTOS = new Set(["removed", "item_missing"]);

export const SEM_SENHA = "Antes de conectar um banco, crie a senha da sua conta pelo link que enviamos por e-mail.";

export type EstadoVolta =
  | { fase: "esperando-trava" }
  | { fase: "conferindo"; instavel: boolean }
  | { fase: "conectado"; ui: Conexao["ui"] }
  | { fase: "ainda-conferindo" }
  | { fase: "sem-item" }
  | { fase: "erro"; texto: string };

export interface Dependencias {
  agora: () => number;
  esperar: (ms: number) => Promise<void>;
  cancelado: () => boolean;
  aoMudar: (estado: EstadoVolta) => void;
  /** `useSessao().expirou`: a pilha troca para o login. */
  expirou: (aviso: string) => void;
}

/** O `itemId` do link, ou `null` se ausente, repetido (array), vazio ou fora da regra do servidor. */
export function itemDoLink(valor: unknown): string | null {
  return typeof valor === "string" && ID_DO_ITEM.test(valor) ? valor : null;
}

const achar = (lista: { connections: Conexao[] }, itemId: string) =>
  lista.connections.find((c) => c.provider_item_id === itemId);

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
 */
export async function conferirVolta(link: unknown, d: Dependencias): Promise<void> {
  const itemId = itemDoLink(link);
  if (!itemId) return d.aoMudar({ fase: "sem-item" });

  const prazo = d.agora() + JANELA_MS;
  let uid: number | null = null;
  let posts = 0;
  let instavel = false;
  d.aoMudar({ fase: "conferindo", instavel });

  for (;;) {
    try {
      uid ??= (await perfil()).user_id;
      if (d.cancelado()) return;
      const atual = achar(await conexoes(uid), itemId);
      if (d.cancelado()) return;
      if (atual && !MORTOS.has(atual.ui.state)) return d.aoMudar({ fase: "conectado", ui: atual.ui });
      if (posts < MAX_POSTS) {
        posts += 1;
        const registrado = achar(await registrarItem(uid, itemId), itemId);
        if (d.cancelado()) return;
        if (registrado) return d.aoMudar({ fase: "conectado", ui: registrado.ui });
      }
      if (instavel) {
        instavel = false;
        d.aoMudar({ fase: "conferindo", instavel });
      }
    } catch (e) {
      if (d.cancelado()) return;
      // Antes do resto: `RequisicaoSuperada` é um 409 (a conta trocou), não o 409 "outra conta" do servidor.
      if (e instanceof RequisicaoSuperada) return;
      if (e instanceof SessaoExpirada) return d.expirou(e.detalhe);
      if (e instanceof ContratoInvalido) return d.aoMudar({ fase: "erro", texto: GENERICO });
      if (!transitoria(e)) return d.aoMudar({ fase: "erro", texto: textoDoErro(e) });
      if (!instavel) {
        instavel = true;
        d.aoMudar({ fase: "conferindo", instavel });
      }
    }
    if (d.agora() >= prazo) return d.aoMudar({ fase: "ainda-conferindo" });
    await d.esperar(INTERVALO_MS);
    if (d.cancelado()) return;
  }
}
