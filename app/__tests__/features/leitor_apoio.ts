/** Apoio dos testes do leitor (credencial de visualização) no app. Não termina em `.test.tsx`: o Jest não o roda como suíte. */
import { act, renderRouter, screen, waitFor } from "expo-router/testing-library";

import { guardarCredenciais } from "@/storage/secure";

import { fetchFalso, resposta } from "./auth_apoio";
import { drenar } from "./open_finance_volta_rota_apoio";
import fixture from "./painel.fixture.json";

const b64 = (o: object) => Buffer.from(JSON.stringify(o)).toString("base64url");
export const jwt = (carga: object) => `${b64({ alg: "HS256" })}.${b64(carga)}.assinatura`;
export const LEITOR = { access: jwt({ ro: true, jti: "j-leitor", name: "leitor" }), refresh: "rt_leitor" };
export const DONO = { access: "access-ana", refresh: "rt_ana" };
const RECEITAS = [{ chave: "extrato#sal", nome: "Salário Acme", valor: "5000.00", dia: 5, status: "pendente" }];

/** Servidor de painel: o leitor não tem o GET de perfil (403), como no servidor. */
export function servir(leitor: boolean) {
  fetchFalso.mockImplementation(async (url: string) => {
    const path = new URL(url).pathname;
    if (path === "/auth/me") return resposta(200, { user_id: 1, app_access: true, ...(leitor ? { somente_leitura: true } : { display_name: "Ana" }) });
    if (path === "/onboarding/open-finance") return resposta(200, { ok: true, completed: true, completed_at: "2026-10-05T12:00:00Z" });
    if (path === "/api/app/perfil") return leitor ? resposta(403, { error: "somente_leitura" }) : resposta(200, { perfil: "padrao" });
    if (path === "/api/app/previsao") return resposta(200, { ...fixture[path], receitas_do_banco: RECEITAS });
    return resposta(200, (fixture as Record<string, unknown>)[path] ?? {});
  });
}
export async function abrir(leitor: boolean, url: string) {
  servir(leitor);
  await guardarCredenciais(leitor ? LEITOR : DONO);
  renderRouter("./app", { initialUrl: url });
  await waitFor(() => expect(screen.getByLabelText("Aba Resumo")).toBeTruthy());
  await act(drenar);
}

/** Os pedidos que NÃO são GET (escrita), por método e caminho: o boot do leitor não pode ter nenhum, nem POST sem corpo nem DELETE. */
export const escritas = () =>
  fetchFalso.mock.calls
    .map(([u, o]: [string, RequestInit]) => `${o.method ?? "GET"} ${new URL(u).pathname}`)
    .filter((m) => !m.startsWith("GET "));
