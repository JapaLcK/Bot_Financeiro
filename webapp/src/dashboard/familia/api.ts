// Plano Família (api/v2/familia.py): os tipos são escritos aqui, à mão, porque a rota não tem schema OpenAPI (o gerador só entende GET/PUT/POST 200 com corpo).
// Cada leitura confere o formato na borda: resposta fora do contrato vira ErroApi(200, "contrato"), nunca uma tela quebrada.
// O `ref` do membro é opaco: só volta ao servidor (DELETE /familia/membros/{ref}), nunca vira id nem chave de armazenamento.
import { queryOptions } from "@tanstack/react-query";
import { DEMO, ErroApi, erroDe, escrever, chamar, instante, meQuery, urlDe, FUSO } from "../lib/v2";
import { buscar } from "../lib/sessao";

export type Membro = { ref: string; nome: string; status: "ativo" | "suspenso" };
export type Titular = { papel: "titular"; capacidade: number; membros: Membro[]; convites_ativos: number };
export type Visao = Titular | { papel: "membro" } | null; // null = sem família (404 not_found)
export type Convite = { token: string; link: string; expira_em: string };
export type Dissolucao = { membros: number; convites: number; consentimentos: number; confirmacao: string };

const contrato = () => new ErroApi(200, "contrato");
const objeto = (v: unknown): v is Record<string, unknown> => !!v && typeof v === "object";
const inteiro = (v: unknown): v is number => typeof v === "number" && Number.isInteger(v) && v >= 0;
const texto = (v: unknown): v is string => typeof v === "string" && v !== "";

function visaoDe(v: unknown): Visao {
  if (!objeto(v)) throw contrato();
  if (v.papel === "membro") return { papel: "membro" };
  if (v.papel !== "titular" || !inteiro(v.capacidade) || !inteiro(v.convites_ativos) || !Array.isArray(v.membros)) throw contrato();
  const membros = v.membros.map((m: unknown) => {
    if (!objeto(m) || !texto(m.ref) || typeof m.nome !== "string" || (m.status !== "ativo" && m.status !== "suspenso")) throw contrato();
    return { ref: m.ref, nome: m.nome, status: m.status as Membro["status"] };
  });
  return { papel: "titular", capacidade: v.capacidade, membros, convites_ativos: v.convites_ativos };
}

export function conviteDe(v: unknown): Convite {
  if (!objeto(v) || !texto(v.token) || !texto(v.link) || !texto(v.expira_em) || !Number.isFinite(instante(v.expira_em))) throw contrato();
  return { token: v.token, link: v.link, expira_em: v.expira_em };
}

function dissolucaoDe(v: unknown): Dissolucao {
  if (!objeto(v) || !inteiro(v.membros) || !inteiro(v.convites) || !inteiro(v.consentimentos) || !texto(v.confirmacao)) throw contrato();
  return { membros: v.membros, convites: v.convites, consentimentos: v.consentimentos, confirmacao: v.confirmacao };
}

const GET = (signal?: AbortSignal) => ({ headers: { Accept: "application/json" }, signal });

export const familiaQuery = queryOptions({
  queryKey: ["conta", "familia"],
  queryFn: async ({ signal }): Promise<Visao> => {
    try { return visaoDe(await chamar<unknown>("/familia", GET(signal))); }
    catch (e) { if (e instanceof ErroApi && e.status === 404 && e.code === "not_found") return null; throw e; }
  },
  staleTime: 0, refetchOnWindowFocus: false, networkMode: "always", retry: meQuery.retry,
  enabled: !DEMO,
});

// Escrita com corpo na resposta (201): criar família e gerar convite. Corpo `{}` para levar o content-type json (o CSRF do servidor o exige).
export const criarFamilia = async () => { visaoDe(await escrever<unknown>("POST", "/familia", {})); };
export const gerarConvite = async () => conviteDe(await escrever<unknown>("POST", "/familia/convites", {}));
export const lerDissolucao = async () => dissolucaoDe(await chamar<unknown>("/familia/dissolucao", GET()));

// Escrita que responde 204 sem corpo: o `chamar` trata 204 como erro (espera JSON), então esta tem o próprio fetch, com o mesmo CSRF e o mesmo erro.
async function semCorpo(method: "POST" | "DELETE", path: string, corpo: unknown = {}) {
  const json = { Accept: "application/json", "Content-Type": "application/json" };
  let r: Response;
  try { r = await buscar(urlDe(path), { method, credentials: "same-origin", headers: window.pbCsrfHeaders?.(json) ?? json, body: JSON.stringify(corpo) }); }
  catch { throw new ErroApi(0, "network"); }
  if (r.status === 204) return;
  let body: unknown;
  try { body = await r.json(); } catch { body = undefined; }
  throw erroDe(r, body); // 2xx com outro corpo também cai aqui (http_200): contrato quebrado, não sucesso
}
export const sairDaFamilia = () => semCorpo("POST", "/familia/sair");
export const removerMembro = (ref: string) => semCorpo("DELETE", `/familia/membros/${encodeURIComponent(ref)}`);
export const revogarConvite = (token: string) => semCorpo("DELETE", "/familia/convites", { token });
export const dissolverFamilia = (confirmacao: string) => semCorpo("POST", "/familia/dissolucao", { confirmacao });

const DD_MM = new Intl.DateTimeFormat("pt-BR", { timeZone: FUSO, day: "2-digit", month: "2-digit" });
export const validade = (iso: string) => DD_MM.format(instante(iso));

export type Falha = { texto: string; plano?: true };
/** O erro de uma escrita em texto da tela. O texto do servidor (inglês, genérico) nunca vai para a tela. */
export function falhaFamilia(e: unknown): Falha {
  const s = e instanceof ErroApi ? e.status : 0;
  const c = e instanceof ErroApi ? e.code : "";
  if (s === 402) return { texto: "Seu plano não inclui família.", plano: true };
  if (s === 429) return { texto: "Muitas tentativas. Tente de novo mais tarde." };
  if (c === "full") return { texto: "Todos os lugares estão ocupados." };
  if (c === "confirmation_mismatch") return { texto: "A família mudou. Confira de novo." };
  if (s === 404) return { texto: "Isso não existe mais." };
  if (s === 401 || s === 403) return { texto: "Recarregue a página e tente de novo." };
  return { texto: "Não foi possível concluir agora. Tente de novo." };
}
