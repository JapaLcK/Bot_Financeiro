import { chamar } from "../api/client";
import { loginSchema, perfilSchema, type Perfil } from "../api/schemas/auth";
import {
  guardarCredenciais,
  lerCredenciais,
  limparCredenciais,
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
 */
export async function sair(): Promise<void> {
  try {
    await chamar("/auth/logout", perfilSchema.partial(), { metodo: "POST" });
  } catch {
    // Silêncio de propósito: o servidor revoga por expiração de qualquer forma.
  } finally {
    await limparCredenciais();
  }
}
