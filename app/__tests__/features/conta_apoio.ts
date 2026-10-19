/**
 * Apoio dos testes de Exportar e Excluir conta (`conta_*.test.tsx`). Não termina em `.test.tsx`, então o Jest não o roda
 * como suíte. Cliente, cofre, roteador e sessão são os de verdade; só `fetch`, `Alert` e SecureStore são dublês.
 */
import { act, fireEvent, renderRouter, screen, waitFor } from "expo-router/testing-library";
import { Alert, type AlertButton } from "react-native";

import { guardarCredenciais } from "@/storage/secure";

import { chamadas, fetchFalso, resposta, rotear, S, type Rota } from "./auth_apoio";
import { drenar } from "./seguranca_sem_senha_apoio";

export const MARCA = { "WWW-Authenticate": 'Bearer realm="pigbank", error="invalid_token"' };
export const MASCARADO = "a•••@x.com";

/** 401 de SESSÃO: leva a marca do servidor. */
export const sessaoMorta = () => ({ ...resposta(401, { detail: "Token inválido ou expirado." }), headers: new Headers(MARCA) }) as Response;

export const meDe = (extra: object = {}): Rota => () =>
  resposta(200, { user_id: 7, email: "ana@x.com", display_name: "Ana", app_access: true, ...extra });

export async function abrir(url: string, me: object = {}, extra: Record<string, Rota> = {}) {
  rotear({
    "/auth/me": meDe(me),
    "/auth/account/reauth-code": () => resposta(200, { status: "sent", expires_in_minutes: 15, email_mascarado: MASCARADO }),
    ...extra,
  });
  await guardarCredenciais(S);
  renderRouter("./app", { initialUrl: url });
}

/** O alerta responde sozinho com o botão `escolha`; os sem botão (avisos) só ficam registrados. */
export function responderAlerta(escolha: "Cancelar" | "Excluir") {
  return jest.spyOn(Alert, "alert").mockImplementation((_t, _m, botoes?: AlertButton[]) => {
    botoes?.find((b) => b.text === escolha)?.onPress?.();
  });
}

export const tocar = (rotulo: string) =>
  act(async () => {
    fireEvent.press(screen.getByRole("button", { name: rotulo }));
    await drenar();
  });

export const digitar = (rotulo: string, valor: string) => fireEvent.changeText(screen.getByLabelText(rotulo), valor);

export const esperarBotao = (rotulo: string) => waitFor(() => expect(screen.getByRole("button", { name: rotulo })).toBeTruthy());

export const chamadasA = (caminho: string) => chamadas().filter((c) => c.caminho === caminho);

/** A requisição crua (método) da última chamada a `caminho`. */
export const metodoDe = (caminho: string) =>
  fetchFalso.mock.calls.filter(([u]: [string]) => String(u).endsWith(caminho)).at(-1)?.[1]?.method as string | undefined;
