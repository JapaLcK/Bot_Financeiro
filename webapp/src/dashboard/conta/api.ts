// Perfil da Conta: o que a página lê e escreve fora da /api/v2 (rotas antigas /settings e /auth).
// Dado pessoal: só na memória do React Query, nunca em storage. O contrato é do servidor
// (frontend/routes/settings.py), então os tipos são escritos aqui, não gerados.
import { queryOptions, type QueryClient } from "@tanstack/react-query";
import { DEMO, ErroApi, chamar, escrever, meQuery } from "../lib/v2";
import { guardarCodigos } from "./Cofre";

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

// A escrita que nunca responde não pode prender o modal (o Esc não o fecha em voo). Sem `AbortSignal.timeout`: o Safari < 16 não o tem (dashboard_v2_safari14).
// NÃO vale para o que o servidor comita e só devolve uma vez (enable, regenerate-backup-codes): abortar perderia os códigos.
const comPrazo = <T,>(f: (sinal: AbortSignal) => Promise<T>) => {
  const parar = new AbortController();
  const prazo = setTimeout(() => parar.abort(), 30000);
  return f(parar.signal).finally(() => clearTimeout(prazo));
};
export const salvarContato = (uid: number, corpo: CorpoContato) => comPrazo((sinal) => escrever<Seguranca>("PATCH", `/settings/${uid}/security/contact`, corpo, sinal));
export const gerarLinkWhatsApp = () => escrever<{ whatsapp_link?: string }>("POST", "/auth/link-code", {});

// MFA e senha: rotas antigas (/auth/mfa, /settings/{uid}/password-reset). Senha, TOTP, segredo e códigos nunca entram no cache
// do React Query: sem `useMutation` e sem `setQueryData`; os códigos vão direto para o Cofre (conta/Cofre.tsx).
export type MfaStatus = { enabled: boolean; has_pending_secret: boolean; backup_codes_remaining: number };
export type MfaSetup = { secret: string; uri: string; qr_code: string };

export const mfaStatusQuery = queryOptions({
  queryKey: ["conta", "mfa"],
  queryFn: ({ signal }) => chamar<MfaStatus>("/auth/mfa/status", GET(signal)),
  staleTime: 0, refetchOnWindowFocus: true, networkMode: "always", retry: meQuery.retry,
  enabled: !DEMO,
});

// A releitura do status depois de um enable ambíguo. `fetchQuery` não recebe signal: corre contra o mesmo prazo de 30 s (a consulta que
// sobra termina no cache, sem efeito); estouro ou rejeição = null, e o modal não fica preso na releitura que nunca responde.
export const lerStatusMfa = (qc: QueryClient) => {
  let prazo: ReturnType<typeof setTimeout>;
  return Promise.race([qc.fetchQuery(mfaStatusQuery).catch(() => null), new Promise<null>((ok) => { prazo = setTimeout(() => ok(null), 30000); })])
    .finally(() => clearTimeout(prazo));
};

const post = <T,>(path: string, corpo: unknown, sinal?: AbortSignal) => escrever<T>("POST", path, corpo, sinal);
export const iniciarMfa = (password: string) => comPrazo((sinal) => post<MfaSetup>("/auth/mfa/setup", { password }, sinal));
export const desativarMfa = (password: string, code: string) => comPrazo((sinal) => post<{ ok: boolean }>("/auth/mfa/disable", { password, code }, sinal));
export const enviarLinkSenha = (uid: number) => comPrazo((sinal) => post<{ ok: boolean; message?: string }>(`/settings/${uid}/password-reset`, {}, sinal));
// Sem prazo (ver `comPrazo`). A resposta vai DIRETO ao Cofre, nunca ao estado do componente: quem chamou pode ter desmontado.
// No máximo UM pedido gerador de códigos em voo no módulo: sem prazo, duas respostas poderiam chegar em ordem qualquer e o Cofre
// mostraria códigos que o servidor já invalidou. O MfaForm trava o envio enquanto `geracaoEmVoo()` (ele sobrevive ao modal desmontado).
let emVoo = false;
const ouvintes = new Set<() => void>();
export const geracaoEmVoo = () => emVoo;
export const assinarGeracao = (f: () => void) => { ouvintes.add(f); return () => { ouvintes.delete(f); }; };
const gerar = <T,>(f: () => Promise<T>) => {
  const muda = (v: boolean) => { emVoo = v; ouvintes.forEach((o) => o()); };
  muda(true);
  return f().finally(() => muda(false));
};
export const ativarMfa = (code: string) => gerar(() => post<{ backup_codes: unknown }>("/auth/mfa/enable", { code }).then((r) => guardarCodigos(r.backup_codes, false)));
export const regerarCodigos = (password: string, code: string) =>
  gerar(() => post<{ backup_codes: unknown }>("/auth/mfa/regenerate-backup-codes", { password, code }).then((r) => guardarCodigos(r.backup_codes, true)));

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

// O erro de uma escrita em texto da tela. O texto do servidor sai como chegou (já é PT) nos status de `detalhados`; o 401
// só é "sessão" quando vem SEM `detalhe`: o "Senha incorreta." é 401 de aplicação e nunca pode virar "Sua sessão terminou."
// (os outros widgets tratam todo 401 como sessão: este NÃO). `revalida`: a escrita pode ter ido, releia o estado.
// Os status das escritas da Conta (MFA, link da senha) em que o texto do servidor sai como chegou; o 404 também (conta/e-mail inexistente).
export const DETALHES = [400, 401, 404, 409, 429];
export function falhaDe(e: unknown, reserva: string, detalhados = [400, 401, 409, 429]): Falha {
  const s = e instanceof ErroApi ? e.status : 0;
  const d = e instanceof ErroApi ? e.detalhe : undefined;
  if (d && detalhados.includes(s)) return { texto: d, foco: s === 401 ? "senha" : s === 409 ? "valor" : undefined };
  if (s === 401) return { texto: "Sua sessão terminou.", acao: "recarregar" };
  if (s === 403) return { texto: "Recarregue a página antes de salvar de novo." };
  return { texto: reserva, revalida: true };
}

export function falhaDaEscrita(e: unknown, campo: Campo, temSenha: boolean | undefined): Falha {
  // Só-Google trocando o e-mail: o 409 é "defina uma senha" (a checagem da senha vem antes de qualquer 409 de contato).
  if (campo === "email" && temSenha === false && e instanceof ErroApi && e.status === 409 && e.detalhe) return { texto: e.detalhe, acao: "definir-senha" };
  return falhaDe(e, "Não foi possível salvar. Seu rascunho foi mantido.");
}
