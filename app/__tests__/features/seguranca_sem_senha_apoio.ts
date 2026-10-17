/**
 * Apoio dos testes de `Segurança` para conta sem senha (`seguranca_sem_senha*.test.tsx`). Não termina em
 * `.test.tsx`, então o Jest não o roda como suíte.
 */
import { act, fireEvent, renderRouter, screen } from "expo-router/testing-library";

import { guardarCredenciais } from "@/storage/secure";

import { chamadas, prepararCaso, resposta, rotear, S, type Rota } from "./auth_apoio";

export const MFA_DESLIGADO = { enabled: false, has_pending_secret: false, backup_codes_remaining: 0 };

export const drenar = async () => {
  for (let i = 0; i < 20; i++) await Promise.resolve();
};

/** `/auth/me` com `has_password` controlado; `undefined` = o campo nem vem. */
export function abrir(hasPassword: { valor: boolean | undefined; email?: string }, extra: Record<string, Rota> = {}) {
  rotear({
    "/auth/mfa/status": () => resposta(200, MFA_DESLIGADO),
    "/auth/forgot-password": () => resposta(200, { message: "texto do servidor" }),
    "/auth/me": () =>
      resposta(200, { user_id: 1, email: hasPassword.email ?? "s@x.com", display_name: "S", app_access: true, ...(hasPassword.valor === undefined ? {} : { has_password: hasPassword.valor }) }),
    ...extra,
  });
  return guardarCredenciais(S).then(() => renderRouter("./app", { initialUrl: "/seguranca" }));
}

export const tocar = (rotulo: string) =>
  act(async () => {
    fireEvent.press(screen.getByRole("button", { name: rotulo }));
    await drenar();
  });
export const forgot = () => chamadas().filter((c) => c.caminho === "/auth/forgot-password");

const appState = globalThis as unknown as { __dispararAppState: (v: string) => void };
export const appVai = (v: "active" | "inactive" | "background") =>
  act(async () => {
    appState.__dispararAppState(v);
    await drenar();
  });

/** `beforeEach` comum: cofre limpo e app em primeiro plano. */
export function preparar() {
  prepararCaso();
  appState.__dispararAppState("active");
}
