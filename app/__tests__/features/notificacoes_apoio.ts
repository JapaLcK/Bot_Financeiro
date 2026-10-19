/**
 * Apoio dos testes das Notificações (`notificacoes_*.test.tsx`). Não termina em `.test.tsx`, então o Jest não o roda como suíte.
 * O servidor falso imita a ORDEM de `update_notification_settings_route`: a hora LIGA o diário e só depois o
 * `daily_report_enabled` explícito sobrescreve; `weekly true` fora do plano é 403 `pro_required`; sem destino é 400.
 */
import { act, fireEvent, renderRouter, screen } from "expo-router/testing-library";

import type { Notificacoes } from "@/api/schemas/conta";
import { guardarCredenciais } from "@/storage/secure";

import { fetchFalso, resposta, rotear, S, type Rota } from "./auth_apoio";
import { drenar } from "./seguranca_sem_senha_apoio";

export const BASE: Notificacoes = {
  email: "sofia@x.com",
  whatsapp_destination: "+5511987654321",
  email_notifications_available: true,
  whatsapp_updates_available: true,
  tip_email_enabled: true,
  insight_email_enabled: true,
  whatsapp_updates_enabled: true,
  push_bank_reconnect_enabled: true,
  daily_report_enabled: true,
  daily_report_hour: 9,
  daily_report_minute: 0,
  weekly_report_enabled: true,
  weekly_report_stored_enabled: true,
  weekly_report_available: true,
  monthly_report_enabled: true,
};

type Corpo = Record<string, unknown>;
/** `undefined` = segue para o servidor falso; uma `Response` (ou promessa dela) a substitui. */
type Gancho = (corpo: Corpo) => Response | Promise<Response | undefined> | undefined;

export function servidor(inicial: Partial<Notificacoes> = {}, opcoes: { gancho?: Gancho; get?: Rota } = {}) {
  const s: Notificacoes = { ...BASE, ...inicial };
  const aplicar = (c: Corpo) => {
    if (c.weekly_report_enabled === true && !s.weekly_report_available) return resposta(403, { detail: { error: "pro_required" } });
    if ("whatsapp_updates_enabled" in c && !s.whatsapp_destination) return resposta(400, { detail: "Vincule um WhatsApp para receber atualizações." });
    for (const k of ["tip_email_enabled", "insight_email_enabled", "whatsapp_updates_enabled", "push_bank_reconnect_enabled", "monthly_report_enabled"] as const) if (typeof c[k] === "boolean") s[k] = c[k];
    if (c.daily_report_hour !== undefined || c.daily_report_minute !== undefined) {
      s.daily_report_hour = (c.daily_report_hour as number | undefined) ?? s.daily_report_hour;
      s.daily_report_minute = (c.daily_report_minute as number | undefined) ?? s.daily_report_minute;
      s.daily_report_enabled = true; // set_daily_report_hour liga o diário
    }
    if (typeof c.daily_report_enabled === "boolean") s.daily_report_enabled = c.daily_report_enabled;
    if (typeof c.weekly_report_enabled === "boolean") {
      s.weekly_report_stored_enabled = c.weekly_report_enabled;
      s.weekly_report_enabled = s.weekly_report_available && c.weekly_report_enabled;
    }
    return resposta(200, { ok: true, engagement_email_enabled: true, ...s });
  };
  const rota: Rota = (o) => {
    if (o.method !== "PATCH") return opcoes.get ? opcoes.get(o) : resposta(200, { ok: true, engagement_email_enabled: true, ...s });
    const corpo = JSON.parse(String(o.body)) as Corpo;
    const g = opcoes.gancho?.(corpo);
    return g === undefined ? aplicar(corpo) : Promise.resolve(g).then((r) => r ?? aplicar(corpo));
  };
  return { s, rota };
}

export const abrirNotificacoes = (srv: ReturnType<typeof servidor>, extra: Record<string, Rota> = {}, me: object = {}, url = "/notificacoes") => {
  rotear({
    "/auth/me": () => resposta(200, { user_id: 1, email: "sofia@x.com", display_name: "Sofia", app_access: true, ...me }),
    "/settings/1/notifications": srv.rota,
    "/settings/1/security": () => resposta(200, { ok: true, email: "sofia@x.com", display_name: "Sofia Lima", phone: null, created_at: null, identities: [] }),
    "/auth/forgot-password": () => resposta(200, { message: "x" }),
    ...extra,
  });
  return guardarCredenciais(S).then(() => renderRouter("./app", { initialUrl: url }));
};

/** Corpos dos PATCH enviados, na ordem. */
export const patches = () =>
  fetchFalso.mock.calls
    .filter(([u, o]: [string, RequestInit]) => String(u).endsWith("/notifications") && o.method === "PATCH")
    .map(([, o]: [string, RequestInit]) => JSON.parse(String(o.body)) as Corpo);
export const gets = () =>
  fetchFalso.mock.calls.filter(([u, o]: [string, RequestInit]) => String(u).endsWith("/settings/1/notifications") && o.method !== "PATCH").length;

export const chave = (rotulo: string) => screen.getByLabelText(rotulo);
/** `value` no iOS, `on` no host do Android. */
export const ligada = (rotulo: string) => (chave(rotulo).props.value ?? chave(rotulo).props.on) as boolean;
export const trocar = (rotulo: string, valor: boolean) =>
  act(async () => {
    fireEvent(chave(rotulo), "valueChange", valor);
    await drenar();
  });

/** O seletor de hora do iOS (mock com `testID`): gira até `h:m` e fecha o seletor, que é quando a hora vale. */
export const escolherHoraIOS = (h: number, m: number) =>
  act(async () => {
    const seletor = screen.getByTestId("seletor-hora");
    const d = new Date();
    d.setHours(h, m, 0, 0);
    fireEvent(seletor, "valueChange", {}, d);
    fireEvent(seletor, "dismiss");
    await drenar();
  });
