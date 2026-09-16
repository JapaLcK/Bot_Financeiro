import { chamar } from "../api/client";
import { loginSchema, perfilSchema, type Perfil } from "../api/schemas/auth";
import {
  guardarCredenciais,
  lerCredenciais,
  limparSe,
} from "../storage/secure";

export async function entrar(email: string, senha: string): Promise<Perfil> {
  const r = await chamar("/auth/login", loginSchema, {
    metodo: "POST",
    corpo: { email, password: senha },
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
 * E apaga só a sessão que INICIOU a saída. A requisição ao servidor leva tempo,
 * e nesse tempo outra conta pode ter entrado: uma limpeza incondicional no fim
 * deletaria a sessão nova, e o efeito seria entrar e ser deslogado em seguida,
 * sem explicação. A fila do cofre torna cada operação atômica, mas não serializa
 * um logout inteiro que passa pela rede — quem amarra é o token de origem.
 */
export async function sair(): Promise<void> {
  const daSaida = await lerCredenciais();
  // Sem sessão capturada não há logout a fazer, e TENTAR é pior que não fazer:
  // a requisição releria o cofre e poderia sair autenticada por uma conta que
  // entrou depois, revogando no servidor a sessão de quem acabou de chegar.
  // Saída duplicada ou tardia cai exatamente aqui.
  if (!daSaida) return;
  try {
    await chamar("/auth/logout", perfilSchema.partial(), {
      metodo: "POST",
      // A requisição fala pela sessão que INICIOU a saída. Sem isto ela releria
      // o cofre por dentro, e uma conta que entrasse nesse intervalo teria a
      // própria sessão revogada no servidor pelo logout da anterior.
      credencial: daSaida,
    });
  } catch {
    // Silêncio de propósito: o servidor revoga por expiração de qualquer forma.
  } finally {
    await limparSe(daSaida.refresh);
  }
}
