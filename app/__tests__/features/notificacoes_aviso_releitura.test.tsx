/**
 * Notificações do app, parte 5: o desligar dos Avisos (D2) × a releitura (PL-06 B6, PR 3; X8–X12 da rodada 3, R1–R10 da rodada 4). As regras:
 * - resposta curta = a conta foi cortada (ou está sem senha), então a tela de antes sai, como no 402 de uma escrita; a releitura decide
 *   o que entra (pronto, `plano`, erro);
 * - o banner de erro da escrita é da fase em que ela falhou (`notificacoes_aviso_banner.test.tsx`);
 * - "desligados" é um evento: anunciado uma vez, focado só se o controle sumiu; visível até a próxima escrita ou a troca de conta,
 *   e nenhuma leitura o anuncia ou foca de novo (plano §26.1).
 * E o contrato da resposta do desligar, com a curta lida do MESMO arquivo que o pytest compara com o servidor.
 * Os controles negativos (mutante, vermelho, restaura) estão no relato do PR.
 */
import { AccessibilityInfo } from "react-native";
import { act, screen, waitFor } from "expo-router/testing-library";

import { avisoDesligadoSchema } from "@/api/schemas/conta";
import { DESLIGADOS, DESLIGADOS_D5, FALHA_CARREGAR, FALHA_SALVAR, TITULO_AVISO } from "@/features/conta/notificacoes";

import curta from "../../../tests/fixtures/app_aviso_desligado_curta.json";
import { cofre, fetchFalso, resposta, segurar } from "./auth_apoio";
import { ALCANCE, BASE, MARGEM, abrirNotificacoes, gets, servidor, trocar } from "./notificacoes_apoio";
import { appVai, drenar, preparar, tocar } from "./seguranca_sem_senha_apoio";

jest.mock("@react-native-community/datetimepicker", () => ({
  __esModule: true,
  default: (p: object) => jest.requireActual("react").createElement(jest.requireActual("react-native").View, { testID: "seletor-hora", ...p }),
  DateTimePickerAndroid: { open: jest.fn() },
}));

beforeEach(() => {
  preparar();
  jest.mocked(AccessibilityInfo.announceForAccessibility).mockClear();
  jest.mocked(AccessibilityInfo.sendAccessibilityEvent).mockClear();
});
afterEach(() => jest.restoreAllMocks());

const PLANO = "Escolha um plano para ver suas notificações.";
const ADERIU = { insight_alert_email_notice: ALCANCE, insight_alert_email_enabled: true };
const anuncios = (t: string) => jest.mocked(AccessibilityInfo.announceForAccessibility).mock.calls.filter(([x]) => x === t).length;
const focos = () => jest.mocked(AccessibilityInfo.sendAccessibilityEvent).mock.calls.filter(([, ev]) => ev === "focus").length;
const assentar = () => act(async () => { for (let i = 0; i < 5; i++) await drenar(); });
const soltar = (seg: ReturnType<typeof segurar>) => act(async () => { seg.soltar(); for (let i = 0; i < 5; i++) await drenar(); });
const voltarAoApp = async () => {
  await appVai("background");
  await appVai("active");
  await assentar();
};
/** Conta com adesão; o GET responde 500 enquanto `falhar.get`. Corta a conta e desliga pela linha: o PATCH recebe a resposta curta. */
const desligarCortada = async (falharNaReleitura: boolean) => {
  const srv = servidor(ADERIU);
  const falhar = { get: false };
  await abrirNotificacoes(srv, { "/settings/1/notifications": (o) => (falhar.get && o.method !== "PATCH" ? resposta(500, { detail: "x" }) : srv.rota(o)) });
  await waitFor(() => expect(screen.getByText("Resumo mensal")).toBeTruthy());
  srv.conta.cortada = true;
  falhar.get = falharNaReleitura;
  await trocar(TITULO_AVISO, false);
  return { srv, falhar };
};
/** Conta com adesão; a partir do 1º PATCH, os GET ficam presos até `seg.soltar()`. `depois` roda logo após o PATCH responder. */
const prenderReleitura = (srv: ReturnType<typeof servidor>, depois = () => {}) => {
  const seg = segurar();
  let prender = false;
  const rota = (o: RequestInit) => {
    if (o.method !== "PATCH") return prender ? seg.promessa.then(() => srv.rota(o)) : srv.rota(o);
    const r = srv.rota(o);
    prender = true;
    depois();
    return r;
  };
  return { seg, rota };
};

describe("Avisos do Piggy — o desligar e a releitura", () => {
  it("X8: curta, a releitura falha (erro, sem a tela velha); depois a pessoa volta e o 402 entra: 'Escolha um plano' e nada de 'não carregou'", async () => {
    const { falhar } = await desligarCortada(true);
    await waitFor(() => expect(screen.getByRole("button", { name: "Tentar de novo" })).toBeTruthy());
    expect(screen.queryByText("Resumo mensal")).toBeNull();
    falhar.get = false;
    const antes = gets();
    await voltarAoApp();
    await waitFor(() => expect(screen.getByText(PLANO)).toBeTruthy());
    expect(gets()).toBeGreaterThan(antes);
    expect(screen.queryByText(FALHA_CARREGAR)).toBeNull();
    expect(screen.getByText(DESLIGADOS)).toBeTruthy(); // o desligar continua dito
  });

  it("X8b: o mesmo, mas o plano voltou: a tela entra completa e nada de 'não carregou'", async () => {
    const { srv, falhar } = await desligarCortada(true);
    await waitFor(() => expect(screen.getByRole("button", { name: "Tentar de novo" })).toBeTruthy());
    falhar.get = false;
    srv.conta.cortada = false;
    await voltarAoApp();
    await waitFor(() => expect(screen.getByText("Resumo mensal")).toBeTruthy());
    expect(screen.queryByText(FALHA_CARREGAR)).toBeNull();
  });

  it("X9: a releitura do desligar fica presa, a volta ao app dispara outra que falha na hora, e depois a 1ª falha: a tela diz que não carregou", async () => {
    const srv = servidor(ADERIU);
    const seg = segurar();
    let modo: "ok" | "prende" | "falha" = "ok";
    await abrirNotificacoes(srv, { "/settings/1/notifications": (o) => {
      if (o.method === "PATCH" || modo === "ok") return srv.rota(o);
      if (modo === "prende") { modo = "falha"; return seg.promessa.then(() => resposta(500, { detail: "x" })); }
      return resposta(500, { detail: "x" });
    } });
    await waitFor(() => expect(screen.getByText("Resumo mensal")).toBeTruthy());
    srv.conta.cortada = true;
    modo = "prende";
    await trocar(TITULO_AVISO, false);
    await voltarAoApp(); // 2ª leitura, falha na hora
    await soltar(seg);
    expect(gets()).toBe(3); // a 1ª carga, a releitura presa e a da volta
    expect(screen.getByText(FALHA_CARREGAR)).toBeTruthy();
    expect(screen.queryByText("Resumo mensal")).toBeNull(); // nunca os dados velhos com a linha desligada
    expect(screen.getByText(DESLIGADOS)).toBeTruthy();
  });

  it("X12 (controle positivo): depois de curta → plano → plano de volta, uma releitura COMUM que falha mantém a tela, calada, como sempre", async () => {
    const { srv, falhar } = await desligarCortada(false);
    await waitFor(() => expect(screen.getByText(PLANO)).toBeTruthy());
    srv.conta.cortada = false;
    await voltarAoApp();
    await waitFor(() => expect(screen.getByText("Resumo mensal")).toBeTruthy());
    falhar.get = true;
    const antes = gets();
    await voltarAoApp();
    expect(gets()).toBeGreaterThan(antes);
    expect(screen.getByText("Resumo mensal")).toBeTruthy();
    expect(screen.queryByText(FALHA_CARREGAR)).toBeNull();
  });

  it("R8 (Médio 2): curta com a releitura presa: 'Carregando', nem 'não carregou' nem a tela velha; ao soltar, `plano`", async () => {
    const srv = servidor(ADERIU);
    const { seg, rota } = prenderReleitura(srv);
    await abrirNotificacoes(srv, { "/settings/1/notifications": rota });
    await waitFor(() => expect(screen.getByText("Resumo mensal")).toBeTruthy());
    srv.conta.cortada = true;
    await trocar(TITULO_AVISO, false);
    await assentar();
    expect(screen.getByLabelText("Carregando")).toBeTruthy();
    expect(screen.queryByText(FALHA_CARREGAR)).toBeNull();
    expect(screen.queryByText("Resumo mensal")).toBeNull();
    await soltar(seg);
    await waitFor(() => expect(screen.getByText(PLANO)).toBeTruthy());
  });
});

it("A8: 402 numa escrita: enquanto a releitura não volta, a tela mostra 'Carregando', nunca 'não carregou'", async () => {
  const srv = servidor(ADERIU);
  const seg = segurar();
  let prender = false;
  await abrirNotificacoes(srv, { "/settings/1/notifications": (o) => (prender && o.method !== "PATCH" ? seg.promessa.then(() => srv.rota(o)) : srv.rota(o)) });
  await waitFor(() => expect(screen.getByText("Resumo mensal")).toBeTruthy());
  srv.conta.cortada = true;
  prender = true;
  await trocar("Resumo mensal", false);
  await assentar();
  expect(screen.getByLabelText("Carregando")).toBeTruthy();
  expect(screen.queryByText(FALHA_CARREGAR)).toBeNull();
  await soltar(seg);
  expect(screen.getByRole("button", { name: "Desligar agora" })).toBeTruthy(); // a releitura achou a adesão
});

describe("Avisos do Piggy — 'desligados' é um evento: anunciado e focado uma vez, nunca de novo por releitura", () => {
  it.each([
    ["402 → plano", "plano", PLANO],
    ["403 exclusão → exclusao", "exclusao", "Esta conta está agendada para exclusão."],
    ["403 password_required → senha", "senha", "Definir senha"],
  ] as const)("R1: D5 na tela; a volta ao app lê %s: D5 continua visível, sem novo anúncio e sem foco", async (_nome, destino, texto) => {
    const srv = servidor(ADERIU);
    let modo: "ok" | "exclusao" | "senha" = "ok";
    await abrirNotificacoes(srv, { "/settings/1/notifications": (o) => (
      modo === "exclusao" ? resposta(403, { detail: "Conta agendada para exclusão." })
        : modo === "senha" ? resposta(403, { detail: { error: "password_required" } })
          : srv.rota(o)) });
    await waitFor(() => expect(screen.getByText("Resumo mensal")).toBeTruthy());
    await trocar("Insights financeiros", false);
    await waitFor(() => expect(screen.getByText(DESLIGADOS_D5)).toBeTruthy());
    expect({ a: anuncios(DESLIGADOS_D5), f: focos() }).toEqual({ a: 1, f: 0 }); // positivo: o anúncio do evento
    if (destino === "plano") srv.conta.cortada = true;
    else modo = destino;
    await voltarAoApp();
    await waitFor(() => expect(screen.getByText(texto)).toBeTruthy());
    expect(screen.queryByText("Resumo mensal")).toBeNull();
    expect(screen.getByText(DESLIGADOS_D5)).toBeTruthy();
    expect({ a: anuncios(DESLIGADOS_D5), f: focos() }).toEqual({ a: 1, f: 0 });
  });

  it("R2: linha → curta → o plano volta antes da releitura → pronto: 'Avisos desligados.' anunciado e focado UMA vez, e fica", async () => {
    const srv = servidor(ADERIU);
    await abrirNotificacoes(srv, { "/settings/1/notifications": (o) => {
      if (o.method !== "PATCH" || !srv.conta.cortada) return srv.rota(o);
      const r = srv.rota(o);
      srv.conta.cortada = false;
      return r;
    } });
    await waitFor(() => expect(screen.getByText("Resumo mensal")).toBeTruthy());
    srv.conta.cortada = true;
    await trocar(TITULO_AVISO, false);
    await assentar();
    await waitFor(() => expect(screen.getByText("Resumo mensal")).toBeTruthy());
    expect(screen.getByText(DESLIGADOS)).toBeTruthy();
    expect({ a: anuncios(DESLIGADOS), f: focos() }).toEqual({ a: 1, f: 1 }); // f = 1: a curta ainda foca
  });

  it("R3: 'Desligar agora' → curta → releitura presa → o plano volta → pronto: anúncio e foco UMA vez", async () => {
    const srv = servidor(ADERIU);
    srv.conta.cortada = true;
    const { seg, rota } = prenderReleitura(srv, () => void (srv.conta.cortada = false));
    await abrirNotificacoes(srv, { "/settings/1/notifications": rota });
    await waitFor(() => expect(screen.getByRole("button", { name: "Desligar agora" })).toBeTruthy());
    await tocar("Desligar agora");
    await assentar();
    expect({ a: anuncios(DESLIGADOS), f: focos() }).toEqual({ a: 1, f: 1 });
    await soltar(seg);
    await waitFor(() => expect(screen.getByText("Resumo mensal")).toBeTruthy());
    expect(screen.getByText(DESLIGADOS)).toBeTruthy();
    expect({ a: anuncios(DESLIGADOS), f: focos() }).toEqual({ a: 1, f: 1 });
  });

  it("R10: o plano volta antes do toque e 'Desligar agora' recebe a resposta COMPLETA: o botão some, 'Avisos desligados.' anunciado e focado; depois, pronto", async () => {
    const srv = servidor(ADERIU);
    srv.conta.cortada = true;
    const { seg, rota } = prenderReleitura(srv);
    await abrirNotificacoes(srv, { "/settings/1/notifications": rota });
    await waitFor(() => expect(screen.getByText(MARGEM)).toBeTruthy());
    srv.conta.cortada = false; // o pagamento voltou entre a leitura e o toque: o PATCH responde a forma completa
    await tocar("Desligar agora");
    await assentar();
    expect(screen.queryByRole("button", { name: "Desligar agora" })).toBeNull();
    expect(screen.getByText(DESLIGADOS)).toBeTruthy();
    expect({ a: anuncios(DESLIGADOS), f: focos() }).toEqual({ a: 1, f: 1 });
    await soltar(seg);
    await waitFor(() => expect(screen.getByText("Resumo mensal")).toBeTruthy());
    expect({ a: anuncios(DESLIGADOS), f: focos() }).toEqual({ a: 1, f: 1 });
  });

  it("R4: D5 da conta 1 na tela → a conta troca com a leitura em voo (RequisicaoSuperada) → a tela da conta 2 não diz 'desligados'", async () => {
    const seg = segurar();
    let conta = 1, prender = false;
    const srv1 = servidor(ADERIU);
    const srv2 = servidor({ monthly_report_enabled: false });
    await abrirNotificacoes(srv1, {
      "/auth/me": () => resposta(200, { user_id: conta, email: "sofia@x.com", display_name: "Sofia", app_access: true }),
      "/settings/1/notifications": (o) => (prender && o.method !== "PATCH" ? seg.promessa.then(() => srv1.rota(o)) : srv1.rota(o)),
      "/settings/2/notifications": srv2.rota,
    });
    await waitFor(() => expect(screen.getByText("Resumo mensal")).toBeTruthy());
    await trocar("Insights financeiros", false);
    await waitFor(() => expect(screen.getByText(DESLIGADOS_D5)).toBeTruthy());
    prender = true;
    await appVai("background");
    await appVai("active");
    cofre.set("pb.credenciais", JSON.stringify({ access: "access-b", refresh: "rt_b" }));
    conta = 2;
    await soltar(seg);
    await waitFor(() => expect(fetchFalso.mock.calls.some(([u]: [string]) => String(u).endsWith("/settings/2/notifications"))).toBe(true));
    await assentar();
    expect(screen.getByText("Resumo mensal")).toBeTruthy(); // positivo: a conta 2 carregou
    expect(screen.queryByText(DESLIGADOS_D5)).toBeNull();
  });
});

it("R7 (PROBE2): a curta com um campo novo compatível não transforma o desligamento gravado em erro na tela", async () => {
  const srv = servidor(ADERIU);
  await abrirNotificacoes(srv, { "/settings/1/notifications": async (o) => {
    if (o.method !== "PATCH" || !srv.conta.cortada) return srv.rota(o);
    await srv.rota(o); // o servidor grava o desligar
    return resposta(200, { ...curta, engagement_email_enabled: true });
  } });
  await waitFor(() => expect(screen.getByText("Resumo mensal")).toBeTruthy());
  srv.conta.cortada = true;
  await trocar(TITULO_AVISO, false);
  await assentar();
  expect(srv.s.insight_alert_email_enabled).toBe(false);
  expect(screen.getByText(DESLIGADOS)).toBeTruthy();
  expect(screen.queryByText(FALHA_SALVAR)).toBeNull();
  await waitFor(() => expect(screen.getByText(PLANO)).toBeTruthy());
  expect(screen.getByText(DESLIGADOS)).toBeTruthy();
});

describe("Avisos do Piggy — o contrato da resposta do desligar", () => {
  const completa = { ok: true, engagement_email_enabled: true, ...BASE, insight_alert_email_enabled: false };
  const aceita = (corpo: object) => avisoDesligadoSchema.safeParse(corpo).success;
  /** A completa descarta o `ok` na saída; a tela reconhece a curta por ele. */
  const comoCurta = (corpo: object) => {
    const r = avisoDesligadoSchema.safeParse(corpo);
    return r.success && "ok" in r.data;
  };

  it("R9: as duas respostas reais passam: a completa e a curta EXATA do servidor (a fixture que o pytest compara)", () => {
    expect(comoCurta(curta)).toBe(true);
    expect(aceita(completa)).toBe(true);
    expect(comoCurta(completa)).toBe(false);
  });

  it("R9: a curta tolera chave a mais (campo novo compatível) e continua curta", () => {
    expect(comoCurta({ ...curta, engagement_email_enabled: true })).toBe(true);
    expect(comoCurta({ ...curta, request_id: "abc" })).toBe(true);
  });

  it("R9: a curta nunca traz `email`; a completa com um campo quebrado não vira 'conta cortada': é ContratoInvalido", () => {
    expect(aceita({ ...curta, email: null })).toBe(false);
    expect(aceita({ ...curta, email: "x" })).toBe(false);
    const { insight_alert_email_paused_notice: _fora, ...semCampo } = completa;
    expect(aceita(semCampo)).toBe(false);
    expect(aceita({ ...completa, weekly_report_available: "sim" })).toBe(false);
  });

  it("R9: o desligar que volta LIGADO é ContratoInvalido, nas duas formas", () => {
    expect(aceita({ ...completa, insight_alert_email_enabled: true })).toBe(false);
    expect(aceita({ ...curta, insight_alert_email_enabled: true })).toBe(false);
  });
});
