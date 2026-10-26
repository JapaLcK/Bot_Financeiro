// Perfil da Conta: o que a página lê e escreve fora da /api/v2 (rotas antigas /settings e /auth).
// Dado pessoal: só na memória do React Query, nunca em storage. O contrato é do servidor
// (frontend/routes/settings.py), então os tipos são escritos aqui, não gerados.
import { queryOptions, type QueryClient } from "@tanstack/react-query";
import { DEMO, ErroApi, FUSO, NOME_DO_PLANO, apiGet, chamar, escrever, instante, lerContaFresca, meQuery } from "../lib/v2";
import type { Conector } from "../lib/bancos";
import { guardarCodigos } from "./Cofre";
import type { Refresh } from "../lib/atualizarBanco";

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
export type Conexao = { id: number; institution_id: string | null; institution_name: string | null; last_sync_at: string | null; logo_url: string | null; logo_proprio: string | null; provider_item_id: string | null; ui: { state: string; label: string; detail: string | null } };
export type Limite = { of_banks_max: number | null; em_uso: number; pode_adicionar: boolean; message: string | null };
// `logo_url` é dado de terceiro e o navegador o busca: só https no CDN de ícones da Pluggy; outro host, http, `user@host`, porta ou query = null
// (a inicial). A MESMA regra do app (app/src/features/openFinance/conexoes/juntar.ts::logoSeguro): tests/frontend/logo_banco_paridade.test.mjs as compara.
const LOGO_PLUGGY = /^https:\/\/cdn\.pluggy\.ai\/[\w.~/-]+$/;
export const logoSeguro = (url: unknown): string | null => (typeof url === "string" && LOGO_PLUGGY.test(url) ? url : null);
// Logo PRÓPRIO do banco (servidor: core/services/pluggy_catalogo.py::logo_proprio): arquivo do repo, mesma origem. Só `/brand/bancos/<8 dígitos>.png`; qualquer outra
// coisa (outro host, `..`, query, número) é ignorada. Precedência: próprio > `logo_url` da Pluggy (`logoSeguro`) > null (a inicial).
const LOGO_PROPRIO = /^\/brand\/bancos\/\d{8}\.png$/;
export const logoDoBanco = ({ logo_proprio, logo_url }: { logo_proprio?: unknown; logo_url?: unknown }): string | null =>
  (typeof logo_proprio === "string" && LOGO_PROPRIO.test(logo_proprio) ? logo_proprio : logoSeguro(logo_url));
const aberto = { staleTime: 0, refetchOnWindowFocus: true, networkMode: "always", retry: meQuery.retry } as const;

// O leitor do snapshot, UM só: o `conexoesQuery` (página Bancos, aviso do topo) e a máquina do "Atualizar agora" (lib/atualizarBanco.ts) o usam.
const t = (x: unknown) => (typeof x === "string" ? x : null); // fora do contrato (número, objeto) vira null: nunca derruba a tela
const conexaoDe = (c: Partial<Conexao>): Conexao => ({ id: c.id!, institution_id: t(c.institution_id), institution_name: t(c.institution_name), last_sync_at: t(c.last_sync_at), logo_url: logoSeguro(c.logo_url), logo_proprio: logoDoBanco({ logo_proprio: c.logo_proprio }), provider_item_id: t(c.provider_item_id),
  ui: { state: t(c.ui?.state) ?? "", label: t(c.ui?.label) ?? "", detail: t(c.ui?.detail) } });
// `connections` fora de lista é contrato quebrado nos dois caminhos (lista vazia diria "nenhum banco", e na rodada "todos removidos" = em dia).
// `estrito` (a rodada do "Atualizar agora" e o POST): a conexão segue o `conexaoSchema` do app nos campos que a máquina lê, porque o nulo
// saneado (`provider_item_id` número virando null) faria um item acompanhado parecer removido. A página Bancos fica com a borda branda.
const conexaoValida = (c: unknown) => objeto(c) && typeof c.id === "number" && textoOuNulo(c.provider_item_id) && textoOuNulo(c.institution_name)
  && textoOuNulo(c.last_sync_at) && objeto(c.ui) && typeof c.ui.state === "string" && typeof c.ui.label === "string" && textoOuNulo(c.ui.detail, true);
const conexoesDe = (v: unknown, estrito: boolean) => {
  if (!Array.isArray(v) || (estrito && !v.every(conexaoValida))) throw contrato();
  return (v as Partial<Conexao>[]).map(conexaoDe);
};
export async function lerConexoes(uid: number, signal?: AbortSignal, estrito = false): Promise<Conexao[]> {
  const r = await chamar<{ connections?: unknown }>(`/open-finance/${uid}`, signal ? GET(signal) : { headers: { Accept: "application/json" } });
  return conexoesDe(r.connections, estrito);
}
export const conexoesQuery = (uid: unknown) => queryOptions({
  queryKey: ["conta", "conexoes", uid],
  queryFn: ({ signal }) => lerConexoes(uid as number, signal), // `enabled` só deixa rodar com `uidValido`
  ...aberto, enabled: !DEMO && uidValido(uid),
});
// `POST /open-finance/{uid}/refresh` (pluggy_sync.py): o veredito por item e o snapshot de depois. O veredito decide o desfecho, então a borda é a do
// `refreshSchema` do app: `sync.items` ausente, fora de lista, ou com um item fora do contrato (não-objeto, `item_id`/`state`/`label` sem string,
// `still_updating` sem booleano, `institution`/`detail`/`reason` fora de string ou null) é contrato quebrado, nunca "em dia" calado: falha como o `ContratoInvalido`.
// O servidor sempre manda os campos. O snapshot do POST (`connections`) é o estrito de `conexoesDe`.
const contrato = () => new ErroApi(200, "contrato");
const textoOuNulo = (v: unknown, opcional = false) => typeof v === "string" || v === null || (opcional && v === undefined);
const itemDe = (i: unknown) => {
  if (!objeto(i) || typeof i.item_id !== "string" || typeof i.state !== "string" || typeof i.label !== "string" || typeof i.still_updating !== "boolean"
    || !textoOuNulo(i.institution) || !textoOuNulo(i.detail, true) || !textoOuNulo(i.reason, true)) throw contrato();
  return { item_id: i.item_id, institution: i.institution as string | null, state: i.state, label: i.label, detail: (i.detail ?? null) as string | null,
    reason: (i.reason ?? null) as string | null, still_updating: i.still_updating };
};
const refreshDe = (r: { connections?: unknown; sync?: { ok?: unknown; items?: unknown } }): Refresh => {
  // `sync.ok` é opcional, mas presente tem de ser booleano (`"false"` descartado faria o `items: []` com falha virar "em dia").
  if (!Array.isArray(r.sync?.items) || (r.sync.ok !== undefined && typeof r.sync.ok !== "boolean")) throw contrato();
  return {
    connections: conexoesDe(r.connections, true),
    sync: { ...(r.sync.ok !== undefined ? { ok: r.sync.ok } : {}), items: r.sync.items.map(itemDe) },
  };
};
export const atualizarBancos = (uid: number, signal: AbortSignal) => escrever<Parameters<typeof refreshDe>[0]>("POST", `/open-finance/${uid}/refresh`, {}, signal).then(refreshDe);
export const limiteQuery = (uid: unknown) => queryOptions({
  queryKey: ["conta", "limite", uid],
  queryFn: ({ signal }) => chamar<Limite>(`/open-finance/${uid}/limite`, GET(signal)),
  ...aberto, enabled: !DEMO && uidValido(uid),
});
// O catálogo de bancos do "Disponíveis para conectar" (`GET /open-finance/{uid}/connectors`, a mesma lista do seletor). Leitura opcional: o catálogo
// muda pouco (sem releitura no foco) e falha = a seção some. Dado de terceiro: item sem id inteiro ou sem nome some; logo só pela regra do CDN; cor validada em lib/bancos.
export const conectoresQuery = (uid: unknown, ativo = true) => queryOptions({
  queryKey: ["conta", "conectores", uid],
  queryFn: async ({ signal }): Promise<Conector[]> => {
    const r = await chamar<{ connectors?: unknown }>(`/open-finance/${uid}/connectors`, GET(signal));
    return (Array.isArray(r.connectors) ? r.connectors : []).flatMap((c: Record<string, unknown> | null) =>
      c && Number.isInteger(c.id) && typeof c.name === "string" && c.name.trim()
        ? [{ id: c.id as number, name: c.name.trim(), color: typeof c.color === "string" ? c.color : null, logo: logoSeguro(c.logo), logo_proprio: logoDoBanco({ logo_proprio: c.logo_proprio }) }] : []);
  },
  staleTime: 300000, refetchOnWindowFocus: false, networkMode: "always", retry: meQuery.retry, enabled: !DEMO && uidValido(uid) && ativo,
});
// A sessão já é de outra conta (outra aba trocou): a tela esconde o que está em cache e relê o perfil.
export class ContaTrocada extends Error {}
// `/api/v2/bancos` lê o usuário da sessão, não o `uid`: a chave separa o cache por conta, e a sessão é conferida DEPOIS da resposta
// (outra aba pode ter trocado de conta no meio). Sessão de outra conta = leitura falha (sem números), nunca o total dela sob este `uid`.
export async function daConta<T>(uid: unknown, signal: AbortSignal, r: T): Promise<T> {
  if ((await chamar<{ user_id?: unknown }>("/auth/dashboard-profile", GET(signal))).user_id !== uid) throw new ContaTrocada();
  return r;
}
// A conferência ANTES da leitura de um card da IA: a sessão já é de outra conta = nenhum GET sai (o `daConta` cobre a troca DURANTE a busca).
// `lerContaFresca` relê o perfil, e o cache avisa `contaDaSessao`, que apaga a conversa da conta anterior.
export async function antesDaConta(qc: QueryClient, uid: number) {
  if ((await lerContaFresca(qc)).user_id !== uid) throw new ContaTrocada();
}
export const bancosQuery = (uid: unknown) => queryOptions({
  queryKey: ["conta", "bancos", uid],
  queryFn: async ({ signal }) => daConta(uid, signal, await apiGet("/bancos", signal)),
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
// `pai`: o cancelamento de quem chamou (a rodada do Atualizar agora) corta junto. `abort()` SEM `reason`: com ele o fetch rejeita com esse objeto e o `name` deixa de ser "AbortError".
// O corte (prazo ou pai) sai como AbortError na raiz (`chamar` em lib/v2.ts, que só engole o corpo cortado quando a resposta NÃO era 2xx).
export const comPrazo = <T,>(f: (sinal: AbortSignal) => Promise<T>, ms = 30_000, pai?: AbortSignal) => {
  const parar = new AbortController(), corta = () => parar.abort();
  if (pai?.aborted) corta();
  pai?.addEventListener("abort", corta, { once: true });
  const prazo = setTimeout(corta, ms);
  return f(parar.signal).finally(() => { clearTimeout(prazo); pai?.removeEventListener("abort", corta); });
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
// Desconectar um banco (rota antiga). O 2º DELETE do mesmo id é 404, então abortar no prazo não perde nada: a releitura do snapshot decide.
export const desconectarBanco = (uid: number, id: number) => comPrazo((s) => escrever<{ ok: boolean; deleted?: number }>("DELETE", `/open-finance/${uid}/connections/${id}`, undefined, s));

// Preferências de notificação (rotas antigas /settings/{uid}/notifications). Mesma borda das sessões: booleano só `=== true`;
// hora/minuto fora do intervalo (ou corpo que não é objeto) = ERRO de carga, nunca "09:00" inventado.
const NOTIF_BOOL = ["email_notifications_available", "whatsapp_updates_available", "tip_email_enabled", "insight_email_enabled", "whatsapp_updates_enabled",
  "daily_report_enabled", "weekly_report_enabled", "weekly_report_stored_enabled", "weekly_report_available", "monthly_report_enabled"] as const;
export type Notificacoes = Record<(typeof NOTIF_BOOL)[number], boolean> & { email: string | null; whatsapp_destination: string | null; daily_report_hour: number; daily_report_minute: number };
export type CorpoNotificacoes = Partial<Pick<Notificacoes, (typeof NOTIF_BOOL)[number] | "daily_report_hour" | "daily_report_minute">>;
const notificacoesDe = (r: unknown): Notificacoes => {
  const o = objeto(r) ? r : {};
  const [h, m] = [o.daily_report_hour, o.daily_report_minute];
  const dentro = (v: unknown, max: number): v is number => typeof v === "number" && Number.isInteger(v) && v >= 0 && v <= max;
  if (!dentro(h, 23) || !dentro(m, 59)) throw new ErroApi(200, "contrato");
  const bools = Object.fromEntries(NOTIF_BOOL.map((k) => [k, o[k] === true])) as Record<(typeof NOTIF_BOOL)[number], boolean>;
  return { ...bools, email: txt(o.email) || null, whatsapp_destination: txt(o.whatsapp_destination) || null, daily_report_hour: h, daily_report_minute: m };
};
export const notificacoesQuery = (uid: number) => queryOptions({
  queryKey: ["conta", "notificacoes", uid],
  queryFn: ({ signal }) => chamar<unknown>(`/settings/${uid}/notifications`, GET(signal)).then(notificacoesDe),
  staleTime: 0, refetchOnWindowFocus: true, networkMode: "always", retry: meQuery.retry,
  enabled: !DEMO,
});
// Idempotente (grava valores, não alterna): abortar no prazo não perde nada, e a tela relê depois.
export const salvarNotificacoes = (uid: number, corpo: CorpoNotificacoes) =>
  comPrazo((sinal) => escrever<unknown>("PATCH", `/settings/${uid}/notifications`, corpo, sinal)).then(notificacoesDe);
// Cópia dos <option> de #notif-daily-hour (settings.html): o teste dashboard_v2_conta_notificacoes compara as duas (§0.7).
export const HORAS = [6, 7, 8, 9, 10, 11, 12, 18, 19, 20, 21];

// Cobrança (rotas antigas /billing/*, fora da /api/v2). `GET /billing/subscription` não diz se é teste, carência ou cancelada no fim:
// por isso a tela só mostra o que a rota garante (o plano, o intervalo e "Período atual até"). A borda: nome fora de NOME_DO_PLANO e data
// fora de AAAA-MM-DD viram null (nunca o texto cru do servidor); corpo fora do contrato ou `degraded` = ERRO de carga, nunca "Sem plano".
export type Cobranca = { vitalicio: boolean; pix: boolean; ativa: boolean; plano: string | null; intervalo: "monthly" | "annual" | null; ate: string | null; troca: { plano: string; intervalo: "monthly" | "annual" | null; em: string } | null };
// Por split, nunca `new Date("AAAA-MM-DD")` (é UTC: 12/11 vira 11/11 no fuso de Brasília). Cópia de `fmtBrDate` (precos.html).
const DATA_ISO = /^\d{4}-(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01])$/;
const dataBR = (iso: unknown) => (typeof iso === "string" && DATA_ISO.test(iso) ? iso.split("-").reverse().join("/") : null);
const intervaloDe = (v: unknown): Cobranca["intervalo"] => (v === "monthly" || v === "annual" ? v : null);
const planoDe = (v: unknown) => (typeof v === "string" && Object.keys(NOME_DO_PLANO).includes(v) ? v : null);
export const cobrancaDe = (r: unknown): Cobranca => {
  const o = objeto(r) ? r : {};
  if (typeof o.active !== "boolean" || o.degraded === true) throw new ErroApi(200, "contrato");
  const t = objeto(o.scheduled_change) ? o.scheduled_change : {};
  const troca = planoDe(t.plan) && dataBR(t.effective_at) ? { plano: planoDe(t.plan)!, intervalo: intervaloDe(t.interval), em: dataBR(t.effective_at)! } : null;
  return { ativa: o.active, vitalicio: o.lifetime === true, pix: o.gateway === "pix", plano: planoDe(o.plan), ate: dataBR(o.current_period_end), troca,
    intervalo: intervaloDe(o.interval) };
};
// Cada leitura chama o Stripe de 1 a 4 vezes: sem releitura por foco de aba.
export const cobrancaQuery = queryOptions({
  queryKey: ["conta", "cobranca"],
  queryFn: ({ signal }) => chamar<unknown>("/billing/subscription", GET(signal)).then(cobrancaDe),
  staleTime: 0, refetchOnWindowFocus: false, networkMode: "always", retry: meQuery.retry,
  enabled: !DEMO,
});
// Só segue um link https (nunca javascript:/data:/http:). A volta do Stripe é a chave "/painel" do servidor (_PORTAL_VOLTAR).
export const abrirPortal = () => comPrazo((s) => escrever<{ portal_url?: unknown }>("POST", "/billing/portal?voltar=%2Fpainel", undefined, s))
  .then((r) => { if (typeof r.portal_url === "string" && /^https:\/\//i.test(r.portal_url)) return r.portal_url; throw new ErroApi(200, "contrato"); });
// Abortar no prazo é seguro: o 2º release do mesmo schedule dá 400 `no_change`, que a tela trata como "já desfeito".
export const desfazerTroca = () => comPrazo((s) => escrever<{ ok: boolean }>("POST", "/billing/cancel-change", undefined, s));
// O 403 desta página é só o CSRF; o texto de `falhaDe` fala em "salvar" e não serve ao portal.
export function falhaCobranca(e: unknown, reserva: string): Falha {
  return e instanceof ErroApi && e.status === 403 ? { texto: "Recarregue a página e tente de novo." } : falhaDe(e, reserva, [404, 429, 502, 503]);
}

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
