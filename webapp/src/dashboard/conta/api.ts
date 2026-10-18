// Perfil da Conta: o que a página lê e escreve fora da /api/v2 (rotas antigas /settings e /auth).
// Dado pessoal: só na memória do React Query, nunca em storage. O contrato é do servidor
// (frontend/routes/settings.py), então os tipos são escritos aqui, não gerados.
import { queryOptions, type QueryClient } from "@tanstack/react-query";
import { DEMO, ErroApi, FUSO, chamar, escrever, instante, meQuery } from "../lib/v2";
import { guardarCodigos } from "./Cofre";

export type Campo = "nome" | "email" | "telefone";
export type Seguranca = { email: string | null; display_name: string | null; phone: string | null; created_at: string | null };
export type CorpoContato = { display_name: string } | { phone: string } | { email: string; current_password?: string };

const GET = (signal: AbortSignal) => ({ headers: { Accept: "application/json" }, signal });
// O `uid` do /auth/dashboard-profile vai no caminho das rotas antigas: só inteiro positivo (0, -3 e 1.5 viram /settings/0/…).
export const uidValido = (uid: unknown): uid is number => typeof uid === "number" && Number.isInteger(uid) && uid > 0;

export const segurancaQuery = (uid: number | undefined) => queryOptions({
  queryKey: ["conta", "seguranca", uid],
  queryFn: ({ signal }) => chamar<Seguranca>(`/settings/${uid}/security`, GET(signal)),
  staleTime: 0, refetchOnWindowFocus: true, networkMode: "always", retry: meQuery.retry,
  enabled: !DEMO && !!uid,
});

// Bancos conectados: o snapshot do Open Finance (`GET /open-finance/{uid}`, fora da /api/v2) e o teto do plano. O snapshot traz também
// contas, transações e investimentos; só os campos que a tela usa ficam no cache. O `uid` vem só da sessão (contaQuery).
export type Conexao = { id: number; institution_name: string | null; last_sync_at: string | null; ui: { state: string; label: string; detail: string | null } };
export type Limite = { of_banks_max: number | null; em_uso: number; pode_adicionar: boolean; message: string | null };
const aberto = { staleTime: 0, refetchOnWindowFocus: true, networkMode: "always", retry: meQuery.retry } as const;

export const conexoesQuery = (uid: unknown) => queryOptions({
  queryKey: ["conta", "conexoes", uid],
  queryFn: async ({ signal }): Promise<Conexao[]> => {
    const r = await chamar<{ connections?: Partial<Conexao>[] }>(`/open-finance/${uid}`, GET(signal));
    const t = (x: unknown) => (typeof x === "string" ? x : null); // fora do contrato (número, objeto) vira null: nunca derruba a tela
    return (r.connections ?? []).map((c) => ({ id: c.id!, institution_name: t(c.institution_name), last_sync_at: t(c.last_sync_at),
      ui: { state: t(c.ui?.state) ?? "", label: t(c.ui?.label) ?? "", detail: t(c.ui?.detail) } }));
  },
  ...aberto, enabled: !DEMO && uidValido(uid),
});
export const limiteQuery = (uid: unknown) => queryOptions({
  queryKey: ["conta", "limite", uid],
  queryFn: ({ signal }) => chamar<Limite>(`/open-finance/${uid}/limite`, GET(signal)),
  ...aberto, enabled: !DEMO && uidValido(uid),
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

// Espera uma releitura contra o prazo de 30 s (as consultas não recebem signal): a que sobra termina no cache, sem efeito.
// Estouro ou rejeição = null, e quem espera (o modal do MFA, o diálogo das sessões) não fica preso numa leitura que nunca responde.
export const comPrazoLeitura = <T,>(p: Promise<T>) => {
  let prazo: ReturnType<typeof setTimeout>;
  return Promise.race([p.catch(() => null), new Promise<null>((ok) => { prazo = setTimeout(() => ok(null), 30000); })])
    .finally(() => clearTimeout(prazo));
};
// A releitura do status depois de um enable ambíguo.
export const lerStatusMfa = (qc: QueryClient) => comPrazoLeitura(qc.fetchQuery(mfaStatusQuery));

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

// Dispositivos conectados (rotas antigas /settings/{uid}/sessions). Só monta com o uid já validado (inteiro positivo).
export type Sessao = { jti: string; device_label: string; ip: string | null; created_at: string | null; last_seen_at: string | null; is_current: boolean };
export type SessoesAtivas = { sessions: Sessao[]; current_jti: string | null };
// A borda: item que não é objeto com `jti` string some; ip/datas que não são string viram vazio; rótulo vazio vira o texto do servidor (core/sessions.py: device_label).
const txt = (v: unknown) => (typeof v === "string" ? v : "");
const objeto = (v: unknown): v is Record<string, unknown> => !!v && typeof v === "object";
const sessoesDe = (r: unknown): SessoesAtivas => {
  // Fora do contrato (corpo que não é objeto, `sessions` que não é lista, lista cheia de itens e nenhum aproveitável) é ERRO de carga,
  // não "Nenhuma sessão ativa."; só a lista vazia de verdade (`sessions: []`) é vazia.
  const o = objeto(r) ? r : {};
  if (!Array.isArray(o.sessions)) throw new ErroApi(200, "contrato");
  const sessions = o.sessions.filter(objeto).filter((s) => typeof s.jti === "string" && s.jti).map((s) => ({
    jti: s.jti as string, device_label: txt(s.device_label).trim() || "Dispositivo desconhecido", ip: txt(s.ip) || null, created_at: txt(s.created_at) || null, last_seen_at: txt(s.last_seen_at) || null, is_current: s.is_current === true }));
  if (o.sessions.length && !sessions.length) throw new ErroApi(200, "contrato");
  return { current_jti: typeof o.current_jti === "string" ? o.current_jti : null, sessions };
};
export const sessoesQuery = (uid: number) => queryOptions({
  queryKey: ["conta", "sessoes", uid],
  queryFn: ({ signal }) => chamar<unknown>(`/settings/${uid}/sessions`, GET(signal)).then(sessoesDe),
  staleTime: 0, refetchOnWindowFocus: true, networkMode: "always", retry: meQuery.retry,
  enabled: !DEMO,
});
// Idempotentes no servidor (o 2º DELETE da mesma sessão é 404, o das outras devolve revoked: 0): abortar no prazo não perde nada.
export const encerrarSessao = (uid: number, jti: string) => comPrazo((sinal) => escrever<{ ok: boolean }>("DELETE", `/settings/${uid}/sessions/${encodeURIComponent(jti)}`, undefined, sinal));
export const encerrarOutras = (uid: number) => comPrazo((sinal) => escrever<{ ok: boolean; revoked?: number }>("DELETE", `/settings/${uid}/sessions`, undefined, sinal));

// Cópia de `formatActivityTime` (settings.html): o teste dashboard_v2_conta_sessoes compara as duas (§0.7). Duas diferenças, de propósito:
// `instante` em vez de `new Date` (o ISO de 6 casas é NaN no Safari 14) e a data antiga no FUSO do painel, não no do aparelho.
const dataAntiga = new Intl.DateTimeFormat("pt-BR", { timeZone: FUSO, day: "2-digit", month: "2-digit", year: "numeric" });
export function horaRelativa(iso: unknown, agora = Date.now()) {
  const t = instante(iso);
  if (isNaN(t)) return "";
  const seg = Math.max(1, Math.floor((agora - t) / 1000));
  if (seg < 60) return `há ${seg}s`;
  const min = Math.floor(seg / 60);
  if (min < 60) return `há ${min} min`;
  const h = Math.floor(min / 60);
  if (h < 24) return `há ${h}h`;
  const d = Math.floor(h / 24);
  return d < 30 ? `há ${d}d` : dataAntiga.format(new Date(t));
}

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
