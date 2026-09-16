import { chamar } from "../api/client";
import {
  loginSchema,
  perfilSchema,
  respostaLoginSchema,
  type Perfil,
} from "../api/schemas/auth";
import {
  guardarCredenciais,
  lerCredenciais,
  limparSessaoDe,
} from "../storage/secure";

/**
 * O resultado de uma tentativa de entrada.
 *
 * Conta com dois fatores NÃO devolve credencial no primeiro passo: devolve um
 * desafio. Tratar as duas respostas com um esquema só transformava o login
 * legítimo dessas contas em erro de contrato — e a pessoa via "resposta
 * inesperada" em vez da tela de código.
 */
export type Entrada =
  | { fase: "pronta"; perfil: Perfil }
  | { fase: "mfa"; desafio: string; email: string };

export async function entrar(email: string, senha: string): Promise<Entrada> {
  const r = await chamar("/auth/login", respostaLoginSchema, {
    metodo: "POST",
    corpo: { email, password: senha },
    semAuth: true,
  });
  if ("mfa_required" in r) {
    return { fase: "mfa", desafio: r.mfa_challenge, email: r.email };
  }
  await guardarCredenciais({
    access: r.access_token,
    refresh: r.refresh_token,
  });
  return {
    fase: "pronta",
    perfil: { user_id: r.user_id, email: r.email, plan: r.plan },
  };
}

/** Completa a entrada de quem tem dois fatores. `backup` usa código de reserva. */
export async function verificarMfa(
  desafio: string,
  codigo: string,
  backup = false,
): Promise<Perfil> {
  const r = await chamar("/auth/mfa/verify-login", loginSchema, {
    metodo: "POST",
    corpo: { challenge: desafio, code: codigo, use_backup: backup },
    semAuth: true,
  });
  await guardarCredenciais({
    access: r.access_token,
    refresh: r.refresh_token,
  });
  return { user_id: r.user_id, email: r.email, plan: r.plan };
}

export async function perfil(): Promise<Perfil> {
  return chamar("/auth/me", perfilSchema);
}

export async function temSessao(): Promise<boolean> {
  return (await lerCredenciais()) !== null;
}

/**
 * Sair: avisa o servidor e apaga o que está no aparelho — nesta ordem, mas o
 * apagar acontece mesmo se o aviso falhar.
 *
 * Se a limpeza dependesse da rede, um logout feito no metrô deixaria a
 * credencial no keychain e o próximo a abrir o app entraria na conta de quem
 * achou que tinha saído.
 *
 * A requisição fala pela sessão que INICIOU a saída, e a limpeza identifica
 * essa sessão pelo `jti`, não pelo refresh token. Os dois detalhes vêm do mesmo
 * lugar: entre a captura e o fim há tempo de rede, e nesse tempo ou outra conta
 * entra (e não pode ser deslogada por engano) ou esta mesma sessão é renovada
 * por outra tela (e não pode escapar da saída com um refresh novo).
 */
export async function sair(): Promise<void> {
  const daSaida = await lerCredenciais();
  // Sem sessão capturada não há logout a fazer, e TENTAR é pior que não fazer:
  // a requisição releria o cofre e poderia sair autenticada por uma conta que
  // entrou depois. Saída duplicada ou tardia cai exatamente aqui.
  if (!daSaida) return;
  try {
    await chamar("/auth/logout", perfilSchema.partial(), {
      metodo: "POST",
      credencial: daSaida,
    });
  } catch {
    // Silêncio de propósito: o servidor revoga por expiração de qualquer forma.
  } finally {
    await limparSessaoDe(daSaida.access, daSaida.refresh);
  }
}
