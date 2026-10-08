/** Apoio dos dois arquivos da rota `open-finance-volta` (`renderRouter("./app")`). */
import * as LA from "expo-local-authentication";
import { act, screen } from "expo-router/testing-library";

import { INTERVALO_MS } from "@/features/openFinance/volta";

import { chamadas, resposta, rotear, type Rota } from "./auth_apoio";

export const A = "item_a";
export const B = "item_b";
const conexao = (id: string) => ({ id: 1, status: "ACTIVE", status_reason: null, last_sync_at: "2026-10-05T12:00:00Z", reconnected_at: null, provider_item_id: id, institution_name: "Nubank", ui: { state: "updated", label: "Atualizado", detail: null } });
export const lista = (...ids: string[]) => resposta(200, { ok: true, connections: ids.map(conexao) });
export const atualizando = (id: string) =>
  resposta(200, { ok: true, connections: [{ ...conexao(id), ui: { state: "updating", label: "Atualizando…", detail: null } }] });

const ESPERA = "Organizando seus dados";
const FINAL = "Conectando seu banco";
/**
 * O título VISÍVEL (`getByText`; o rótulo da barra também diz "Organizando seus
 * dados", mas é `accessibilityLabel`, não texto). `espera` = trava, conferindo,
 * `updating` e `organizando`; os demais estados são "Conectando seu banco".
 */
export function titulo(espera: boolean, opcoes?: { includeHiddenElements: boolean }) {
  expect(screen.getByText(espera ? ESPERA : FINAL, opcoes)).toBeTruthy();
  expect(screen.queryByText(espera ? FINAL : ESPERA, opcoes)).toBeNull();
}

/** Sem `setTimeout(0)` dentro de `act` (ver `layout.test.tsx`): microtarefas à mão. */
export const drenar = async () => {
  for (let i = 0; i < 20; i++) await Promise.resolve();
};

export const deOpenFinance = () => chamadas().filter((c) => c.caminho.startsWith("/open-finance"));
export const contarGets = () => deOpenFinance().filter((c) => c.caminho === "/open-finance/1").length;
export const postsDe = (id: string) =>
  chamadas().filter((c) => c.caminho.endsWith("/pluggy-item") && (c.corpo as { item: { id: string } }).item.id === id);

/** Uma espera do laço (o `renderRouter` liga o relógio falso). */
export const umIntervalo = () =>
  act(async () => {
    jest.advanceTimersByTime(INTERVALO_MS);
    await drenar();
  });

/** A trava do Face ID: cada prompt fica pendente até `liberar()`. */
export const pendentes: ((r: LA.LocalAuthenticationResult) => void)[] = [];
export function ligarTrava() {
  jest.mocked(LA.getEnrolledLevelAsync).mockResolvedValue(LA.SecurityLevel.BIOMETRIC);
  jest.mocked(LA.authenticateAsync).mockClear().mockImplementation(() => new Promise((r) => pendentes.push(r)));
}
export function desligarTrava() {
  jest.mocked(LA.getEnrolledLevelAsync).mockResolvedValue(LA.SecurityLevel.NONE);
  jest.mocked(LA.authenticateAsync).mockResolvedValue({ success: true });
}
export async function liberar() {
  await act(async () => {
    const soltar = pendentes.shift();
    if (!soltar) throw new Error("nenhum prompt da trava pendente");
    soltar({ success: true });
    await drenar();
  });
}
export const prompts = () => jest.mocked(LA.authenticateAsync).mock.calls.length;

const appState = globalThis as unknown as { __dispararAppState: (v: string) => void };
export async function appVai(valor: "active" | "inactive" | "background") {
  await act(async () => {
    appState.__dispararAppState(valor);
    await drenar();
  });
}

/** O servidor de OF: o GET devolve o que já foi registrado; o POST registra. */
export function servidor(get?: Rota) {
  const registrados: string[] = [];
  rotear({
    "/open-finance/1": get ?? (() => lista(...registrados)),
    "/open-finance/1/pluggy-item": (o) => {
      registrados.push((JSON.parse(String(o.body)) as { item: { id: string } }).item.id);
      return lista(...registrados);
    },
  });
}
