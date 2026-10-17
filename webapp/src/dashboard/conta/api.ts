// Perfil da Conta: o que a página lê e escreve fora da /api/v2 (rotas antigas /settings e /auth).
// Dado pessoal: só na memória do React Query, nunca em storage. O contrato é do servidor
// (frontend/routes/settings.py), então os tipos são escritos aqui, não gerados.
import { queryOptions } from "@tanstack/react-query";
import { DEMO, ErroApi, chamar, escrever, meQuery } from "../lib/v2";

export type Campo = "nome" | "email" | "telefone";
export type Seguranca = { email: string | null; display_name: string | null; phone: string | null; created_at: string | null };
export type CorpoContato = { display_name: string } | { phone: string } | { email: string; current_password?: string };

const GET = (signal: AbortSignal) => ({ headers: { Accept: "application/json" }, signal });

export const segurancaQuery = (uid: number | undefined) => queryOptions({
  queryKey: ["conta", "seguranca", uid],
  queryFn: ({ signal }) => chamar<Seguranca>(`/settings/${uid}/security`, GET(signal)),
  staleTime: 0, refetchOnWindowFocus: true, networkMode: "always", retry: meQuery.retry,
  enabled: !DEMO && !!uid,
});

// Só o `has_password` do /auth/me (a mesma fonte do settings.html): o resto da resposta nunca entra no cache.
// Só monta com o modal do e-mail aberto; `gcTime: 0` e o valor sai da memória ao fechar.
export const temSenhaQuery = (ativo: boolean) => queryOptions({
  queryKey: ["conta", "tem-senha"],
  queryFn: async ({ signal }) => ({ temSenha: (await chamar<{ has_password?: boolean }>("/auth/me", GET(signal))).has_password !== false }),
  staleTime: 0, refetchOnWindowFocus: false, networkMode: "always", retry: meQuery.retry, gcTime: 0,
  enabled: !DEMO && ativo,
});

// O PATCH que nunca responde não pode prender o modal (o Esc não o fecha em voo). Sem `AbortSignal.timeout`: o Safari < 16 não o tem (dashboard_v2_safari14).
export async function salvarContato(uid: number, corpo: CorpoContato) {
  const parar = new AbortController();
  const prazo = setTimeout(() => parar.abort(), 30000);
  try { return await escrever<Seguranca>("PATCH", `/settings/${uid}/security/contact`, corpo, parar.signal); } finally { clearTimeout(prazo); }
}
export const gerarLinkWhatsApp = () => escrever<{ whatsapp_link?: string }>("POST", "/auth/link-code", {});

// Cópia de `formatPhoneBR` (settings.html): o teste dashboard_v2_conta_perfil compara as duas (§0.7).
export function telefoneBR(valor: string | null) {
  const digitos = String(valor || "").replace(/\D/g, "");
  const local = digitos.startsWith("55") && digitos.length > 11 ? digitos.slice(2) : digitos;
  if (local.length === 11) return `(${local.slice(0, 2)}) ${local.slice(2, 7)}-${local.slice(7)}`;
  if (local.length === 10) return `(${local.slice(0, 2)}) ${local.slice(2, 6)}-${local.slice(6)}`;
  return valor || "";
}

export const SENHA_VAZIA = "Confirme sua senha atual para trocar o e-mail.";
export type Falha = { texto: string; acao?: "recarregar" | "definir-senha"; foco?: "senha" | "valor"; revalida?: boolean };

// O erro do PATCH em texto da tela. O texto do servidor sai como chegou (já é PT); o 401 só é "sessão"
// quando vem SEM `detalhe`: o "Senha incorreta." é 401 de aplicação e nunca pode virar "Sua sessão terminou."
// (os outros widgets tratam todo 401 como sessão: este NÃO).
export function falhaDaEscrita(e: unknown, campo: Campo, temSenha: boolean | undefined): Falha {
  const s = e instanceof ErroApi ? e.status : 0;
  const d = e instanceof ErroApi ? e.detalhe : undefined;
  if (d && (s === 400 || s === 401 || s === 409 || s === 429)) {
    // Só-Google trocando o e-mail: o 409 é "defina uma senha" (a checagem da senha vem antes de qualquer 409 de contato).
    if (s === 409 && campo === "email" && temSenha === false) return { texto: d, acao: "definir-senha" };
    return { texto: d, foco: s === 401 ? "senha" : s === 409 ? "valor" : undefined };
  }
  if (s === 401) return { texto: "Sua sessão terminou.", acao: "recarregar" };
  if (s === 403) return { texto: "Recarregue a página antes de salvar de novo." };
  return { texto: "Não foi possível salvar. Seu rascunho foi mantido.", revalida: true };
}
