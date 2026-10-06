import { ErroDeApi, RequisicaoSuperada } from "@/api/client";
import type { Perfil } from "@/api/schemas/auth";
import { perfil } from "@/services/auth";
import { limiteBancario, onboardingBancario, conexoes, pedirConnectToken } from "@/services/openFinance";
import { descartarPreparacaoBancaria, iniciarTentativaBancaria, jtiDe, lerCredenciais, type Credenciais, type SubstituicaoBancaria } from "@/storage/secure";

export type AcessoBancario = { perfil: Perfil; fase: "inicio" | "conectar" | "sem-acesso" | "cobranca-pendente" | "sem-open-finance" | "senha" };

async function conferirSessao(esperada: Credenciais | null) {
  const atual = await lerCredenciais();
  if (!esperada || !atual || (jtiDe(esperada.access) ? jtiDe(atual.access) !== jtiDe(esperada.access) : atual.refresh !== esperada.refresh)) throw new RequisicaoSuperada();
}

/** Entitlement e prova de sync são independentes, ambos decididos pelo servidor. */
export async function carregarAcessoBancario(): Promise<AcessoBancario> {
  const esperada = await lerCredenciais();
  const p = await perfil();
  if (p.precisa_criar_senha) return { perfil: p, fase: "senha" };
  if (p.app_access === false) return { perfil: p, fase: "sem-acesso" };
  if (p.app_access !== true) throw new Error("Não conseguimos confirmar seu acesso. Tente de novo.");
  const marco = await onboardingBancario();
  await conferirSessao(esperada);
  if (marco.completed) return { perfil: p, fase: "inicio" };
  const limite = await limiteBancario(p.user_id);
  await conferirSessao(esperada);
  if (limite.of_banks_max === 0) return { perfil: p, fase: p.cobranca_em_atraso ? "cobranca-pendente" : "sem-open-finance" };
  return { perfil: p, fase: "conectar" };
}

/** Nenhum widget abre sem marcador persistido e permissão do servidor. */
export async function iniciarConexaoBancaria(itemId?: string, cancelado: () => boolean = () => false, substituir?: SubstituicaoBancaria) {
  const credencial = await lerCredenciais();
  const sessao = credencial && jtiDe(credencial.access);
  if (!sessao) throw new Error("Não conseguimos preparar a conexão. Entre de novo.");
  if (substituir && substituir.sessao !== sessao) throw new RequisicaoSuperada();
  const p = await perfil();
  if (p.app_access !== true) throw new Error("Sua conta não tem acesso à conexão bancária agora.");
  const snapshot = await conexoes(p.user_id);
  if (itemId && !snapshot.connections.some((c) => c.provider_item_id === itemId && !["removed", "item_missing", "paused"].includes(c.ui.state))) {
    throw new Error("Esse banco não está disponível para reconexão.");
  }
  const limite = await limiteBancario(p.user_id);
  if (limite.of_banks_max === 0 || (!itemId && !limite.pode_adicionar)) {
    throw new ErroDeApi(402, limite.message ?? "Seu plano não permite adicionar outro banco agora.");
  }
  if (cancelado()) throw new RequisicaoSuperada();
  const tentativa = await iniciarTentativaBancaria(p.user_id, sessao,
    snapshot.connections.flatMap((c) => c.provider_item_id ? [c.provider_item_id] : []), itemId,
    snapshot.connections.find((c) => c.provider_item_id === itemId)?.reconnected_at, substituir);
  if (!tentativa) throw new Error("Sua sessão mudou. Tente de novo.");
  try {
    if (cancelado()) throw new RequisicaoSuperada();
    const token = await pedirConnectToken(p.user_id, itemId, tentativa.tentativa_id);
    await conferirSessao(credencial);
    if (cancelado()) throw new RequisicaoSuperada();
    return { ...token, tentativa_id: tentativa.tentativa_id };
  } catch (e) {
    await descartarPreparacaoBancaria(tentativa.tentativa_id);
    throw e;
  }
}
