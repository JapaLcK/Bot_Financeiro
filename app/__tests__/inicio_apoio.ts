/**
 * Apoio dos testes da tela de entrada (`inicio.test.ts` e
 * `inicio_corridas.test.ts`). Não termina em `.test.ts`, então o Jest não o
 * roda como suíte.
 */
import { _resetRenovacao } from "@/api/client";
import { _resetTela, type Estado } from "@/ui/inicio";

export const cofre = (globalThis as unknown as { __cofreDeTeste: Map<string, string> })
  .__cofreDeTeste;
export const falharApagar = (
  globalThis as unknown as { __falharApagarNoCofre: (v: boolean) => void }
).__falharApagarNoCofre;
/** Prende a próxima gravação no cofre até a promessa resolver. */
export const atrasarEscrita = (
  globalThis as unknown as { __atrasarEscritaNoCofre: (p: Promise<void>) => void }
).__atrasarEscritaNoCofre;
export const fetchFalso = jest.fn();

export const C: Estado = { fase: "carregando" };
export const E: Estado = { fase: "entrada" };
export const GENERICO = "Algo deu errado. Tente de novo.";
export const S = { access: "access-s", refresh: "rt_s" };

/**
 * Faz a LEITURA do cofre falhar: o dublê lê por `has`, e sombrear o método na
 * instância chega até ele sem gancho novo no `jest.setup.js`.
 */
export function falharLeitura(sim: boolean) {
  if (!sim) return void Reflect.deleteProperty(cofre, "has");
  cofre.has = () => {
    throw new Error("keychain recusou ler");
  };
}

export function prepararCaso() {
  _resetTela();
  _resetRenovacao();
  falharLeitura(false);
  cofre.clear();
  falharApagar(false);
  fetchFalso.mockReset();
  globalThis.fetch = fetchFalso as unknown as typeof fetch;
}

/**
 * Drena as microtarefas. Sem a fila, a ação nova termina aqui dentro; com a
 * fila, ela continua esperando a da frente.
 */
export const respirar = () => new Promise<void>((r) => setTimeout(r, 0));

export function resposta(status: number, corpo: unknown): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => corpo,
  } as Response;
}

export function segurar() {
  let soltar: () => void = () => {};
  const promessa = new Promise<void>((r) => (soltar = r));
  return { promessa, soltar: () => soltar() };
}

export function gravador() {
  const aplicados: Estado[] = [];
  return { aplicados, aplicar: (e: Estado) => void aplicados.push(e) };
}

export type Rota = (o: RequestInit) => Response | Promise<Response>;

/** O `/auth/me` responde pelo `Authorization`: `access-ana` é a conta "Ana". */
export function me(o: RequestInit): Response {
  const auth = (o.headers as Record<string, string>)["Authorization"] ?? "";
  const nome = auth.match(/^Bearer access-(.+)$/)?.[1];
  if (!nome) return resposta(401, { detail: "expirado" });
  const exibido = nome.charAt(0).toUpperCase() + nome.slice(1);
  return resposta(200, { user_id: 1, email: `${nome}@x.com`, display_name: exibido });
}

export function credencialDe(email: string) {
  const nome = email.split("@")[0];
  return {
    user_id: 1,
    email,
    access_token: `access-${nome}`,
    refresh_token: `rt_${nome}`,
    dashboard_token: "d",
    expires_in: 900,
  };
}

export function rotear(extra: Record<string, Rota> = {}) {
  const rotas: Record<string, Rota> = {
    "/auth/login": (o) =>
      resposta(200, credencialDe((JSON.parse(String(o.body)) as { email: string }).email)),
    "/auth/me": me,
    "/auth/logout": () => resposta(200, {}),
    ...extra,
  };
  fetchFalso.mockImplementation(async (url: string, o: RequestInit) => {
    const caminho = String(url).replace(/^https?:\/\/[^/]+/, "");
    const rota = rotas[caminho];
    if (!rota) throw new Error(`rota inesperada: ${caminho}`);
    return rota(o);
  });
}

export const chamadas = () =>
  fetchFalso.mock.calls.map(([u, o]: [string, RequestInit]) => ({
    caminho: String(u).replace(/^https?:\/\/[^/]+/, ""),
    auth: (o.headers as Record<string, string>)["Authorization"],
    corpo: o.body === undefined ? undefined : JSON.parse(String(o.body)),
  }));
