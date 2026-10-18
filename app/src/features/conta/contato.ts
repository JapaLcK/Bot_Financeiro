import { ErroDeApi } from "@/api/client";

// Cópia de `telefoneBR` (webapp/src/dashboard/conta/api.ts): o app não importa o web, então
// `__tests__/features/perfil_paridade.test.ts` compara as duas (§0.7).
export function telefoneBR(valor: string | null) {
  const digitos = String(valor || "").replace(/\D/g, "");
  const local = digitos.startsWith("55") && digitos.length > 11 ? digitos.slice(2) : digitos;
  if (local.length === 11) return `(${local.slice(0, 2)}) ${local.slice(2, 7)}-${local.slice(7)}`;
  if (local.length === 10) return `(${local.slice(0, 2)}) ${local.slice(2, 6)}-${local.slice(6)}`;
  return valor || "";
}

/**
 * Só o link oficial do WhatsApp com o código. `wa.me/?text=` abriria o seletor de contatos (o
 * código iria para quem a pessoa escolher) e o resto (`javascript:`, outro host) nunca é nosso.
 * Regex ancorada, sem `new URL()`: `https://api.whatsapp.com@evil.com/send?` não passa.
 */
export const linkSeguro = (s: string) => /^https:\/\/api\.whatsapp\.com\/send\?/.test(s);

/** O 403 do `/auth/link-code` de conta sem credencial (o monólito manda `{detail:{error:"password_required"}}`). */
export const semSenha = (e: unknown) =>
  e instanceof ErroDeApi && e.status === 403 && (e.corpo as { detail?: { error?: unknown } } | null)?.detail?.error === "password_required";
