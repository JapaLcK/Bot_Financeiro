/**
 * Notificações do app: o corpo de cada PATCH (`patches()`) E o que fica visível. O servidor falso imita a ordem do
 * monólito (ver `notificacoes_apoio.ts`). Os controles negativos (mutante, vermelho, restaura) estão no relato do PR.
 */
import { DateTimePickerAndroid } from "@react-native-community/datetimepicker";
import { Platform } from "react-native";
import { act, fireEvent, screen, waitFor } from "expo-router/testing-library";

import { chamadas, resposta, segurar } from "./auth_apoio";
import { BASE, abrirNotificacoes, chave, escolherHoraIOS, gets, ligada, patches, servidor, trocar } from "./notificacoes_apoio";
import { appVai, drenar, preparar } from "./seguranca_sem_senha_apoio";

jest.mock("@react-native-community/datetimepicker", () => ({
  __esModule: true,
  default: (p: object) => jest.requireActual("react").createElement(jest.requireActual("react-native").View, { testID: "seletor-hora", ...p }),
  DateTimePickerAndroid: { open: jest.fn() },
}));

beforeEach(preparar);
afterEach(() => jest.restoreAllMocks());

const FALHA = "Não conseguimos salvar. Tente de novo.";
const SO_PLUS = "O resumo semanal é dos planos Plus e Pro.";
const esperar = () => waitFor(() => expect(screen.getByLabelText("Resumo mensal")).toBeTruthy());
const abrir = async (inicial = {}, opcoes = {}) => {
  const srv = servidor(inicial, opcoes);
  await abrirNotificacoes(srv);
  await esperar();
  return srv;
};
const hora = (h: number, m: number) => {
  const d = new Date();
  d.setHours(h, m, 0, 0);
  return d;
};
const emAndroid = () => {
  jest.replaceProperty(Platform, "OS", "android");
  (DateTimePickerAndroid.open as jest.Mock).mockClear();
};
const escolherHoraAndroid = (h: number, m: number) =>
  act(async () => {
    const opcoes = (DateTimePickerAndroid.open as jest.Mock).mock.calls.at(-1)[0];
    opcoes.onValueChange({ type: "set" }, hora(h, m));
    await drenar();
  });

describe("Notificações — consentimento: o PATCH leva só o campo tocado", () => {
  it("a) o WhatsApp foi PARADO depois da leitura: mexer no mensal e no horário nunca manda whatsapp_updates_enabled", async () => {
    const srv = await abrir(); // a tela leu whatsapp_updates_enabled: true
    srv.s.whatsapp_updates_enabled = false; // o usuário mandou PARAR no WhatsApp
    await trocar("Resumo mensal", false);
    await waitFor(() => expect(ligada("Resumo mensal")).toBe(false));
    await escolherHoraIOS(20, 15);
    await waitFor(() => expect(patches()).toHaveLength(2));
    expect(patches()).toEqual([{ monthly_report_enabled: false }, { daily_report_hour: 20, daily_report_minute: 15, daily_report_enabled: true }]);
    await waitFor(() => expect(ligada("Atualizações do Piggy")).toBe(false)); // a verdade do servidor, não a leitura velha
  });

  it("b) PATCH do WhatsApp falha (500): nenhum segundo PATCH, o switch volta a desligado e o aviso aparece", async () => {
    await abrir({ whatsapp_updates_enabled: false }, { gancho: () => resposta(500, { detail: "boom" }) });
    await trocar("Atualizações do Piggy", true);
    await waitFor(() => expect(screen.getByText(FALHA)).toBeTruthy());
    await act(async () => void (await drenar()));
    expect(patches()).toEqual([{ whatsapp_updates_enabled: true }]);
    expect(ligada("Atualizações do Piggy")).toBe(false);
    expect(gets()).toBe(2); // a releitura que mostra o que o servidor tem
  });

  it("c) positivo: desligado -> toque manda exatamente {whatsapp_updates_enabled: true} e fica ligado", async () => {
    await abrir({ whatsapp_updates_enabled: false });
    await trocar("Atualizações do Piggy", true);
    await waitFor(() => expect(ligada("Atualizações do Piggy")).toBe(true));
    expect(patches()).toEqual([{ whatsapp_updates_enabled: true }]);
  });

  it("d) sem WhatsApp: switch desativado (toque não manda nada) e 'Conectar' leva ao Perfil", async () => {
    await abrir({ whatsapp_destination: null, whatsapp_updates_available: false, whatsapp_updates_enabled: false });
    expect(chave("Atualizações do Piggy").props.disabled).toBe(true);
    await trocar("Atualizações do Piggy", true);
    expect(patches()).toHaveLength(0);
    expect(screen.getAllByText("—")).toHaveLength(1); // o telefone; o e-mail existe
    fireEvent.press(screen.getByRole("button", { name: "Conectar" }));
    await waitFor(() => expect(screen).toHavePathname("/perfil"));
  });

  it("sem e-mail: Dicas de uso e Insights desativados, sem PATCH", async () => {
    await abrir({ email: null, email_notifications_available: false, tip_email_enabled: false, insight_email_enabled: false });
    expect(chave("Dicas de uso").props.disabled).toBe(true);
    expect(chave("Insights financeiros").props.disabled).toBe(true);
    await trocar("Dicas de uso", true);
    expect(patches()).toHaveLength(0);
  });
});

describe("Notificações — horário", () => {
  it("e) diário desligado e mensal ligado, 20:15: manda daily_report_enabled:false e o diário continua desligado", async () => {
    await abrir({ daily_report_enabled: false, weekly_report_enabled: false, weekly_report_stored_enabled: false });
    await escolherHoraIOS(20, 15);
    await waitFor(() => expect(patches()).toHaveLength(1));
    expect(patches()[0]).toEqual({ daily_report_hour: 20, daily_report_minute: 15, daily_report_enabled: false });
    await waitFor(() => expect((screen.getByTestId("seletor-hora").props.value as Date).getHours()).toBe(20));
    expect((screen.getByTestId("seletor-hora").props.value as Date).getMinutes()).toBe(15);
    expect(ligada("Resumo diário")).toBe(false);
  });

  it("f) positivo: diário ligado -> o corpo leva daily_report_enabled:true", async () => {
    await abrir();
    await escolherHoraIOS(7, 45);
    await waitFor(() => expect(patches()).toHaveLength(1));
    expect(patches()[0]).toEqual({ daily_report_hour: 7, daily_report_minute: 45, daily_report_enabled: true });
    expect(ligada("Resumo diário")).toBe(true);
  });

  it("g) Android: 07:30 aparece como '07:30', nada é enviado ao montar nem ao confirmar o mesmo valor; outro valor manda", async () => {
    emAndroid();
    await abrir({ daily_report_hour: 7, daily_report_minute: 30 });
    expect(screen.getByText("07:30")).toBeTruthy();
    expect(patches()).toHaveLength(0);
    fireEvent.press(screen.getByRole("button", { name: /Horário dos resumos/ }));
    const aberto = (DateTimePickerAndroid.open as jest.Mock).mock.calls[0][0];
    expect([aberto.mode, aberto.is24Hour, aberto.value.getHours(), aberto.value.getMinutes()]).toEqual(["time", true, 7, 30]);
    await escolherHoraAndroid(7, 30);
    expect(patches()).toHaveLength(0);
    await escolherHoraAndroid(8, 5);
    await waitFor(() => expect(screen.getByText("08:05")).toBeTruthy());
    expect(patches()).toEqual([{ daily_report_hour: 8, daily_report_minute: 5, daily_report_enabled: true }]);
  });

  it("g) iOS: o seletor nasce em 07:30, sem PATCH, e girar até o mesmo valor não manda nada", async () => {
    await abrir({ daily_report_hour: 7, daily_report_minute: 30 });
    const v = screen.getByTestId("seletor-hora").props.value as Date;
    expect([v.getHours(), v.getMinutes()]).toEqual([7, 30]);
    await escolherHoraIOS(7, 30);
    expect(patches()).toHaveLength(0);
  });

  const TRES_OFF = { daily_report_enabled: false, weekly_report_enabled: false, weekly_report_stored_enabled: false, monthly_report_enabled: false };

  it("h) três resumos desligados: o seletor do iOS fica desativado", async () => {
    await abrir(TRES_OFF);
    expect(screen.getByTestId("seletor-hora").props.disabled).toBe(true);
  });

  it("h) três resumos desligados: no Android o toque na linha não abre o seletor; com um ligado, abre", async () => {
    emAndroid();
    await abrir(TRES_OFF);
    fireEvent.press(screen.getByRole("button", { name: /Horário dos resumos/ }));
    expect(DateTimePickerAndroid.open).not.toHaveBeenCalled();
    await trocar("Resumo mensal", true);
    await waitFor(() => expect(ligada("Resumo mensal")).toBe(true));
    fireEvent.press(screen.getByRole("button", { name: /Horário dos resumos/ }));
    expect(DateTimePickerAndroid.open).toHaveBeenCalledTimes(1);
  });
});

describe("Notificações — seção No celular", () => {
  it("a tela pronta mostra o grupo 'No celular' (os controles dele: notificacoes_push.test.tsx)", async () => {
    await abrir();
    expect(screen.getByText("No celular")).toBeTruthy();
  });
});

describe("Notificações — resumo semanal", () => {
  it("fora do plano e gravado LIGADO: aparece ligado com 'Pausado no seu plano'; o toque manda false e termina desativado com 'Plus e Pro'", async () => {
    await abrir({ weekly_report_available: false, weekly_report_enabled: false, weekly_report_stored_enabled: true });
    expect(ligada("Resumo semanal")).toBe(true);
    expect(chave("Resumo semanal").props.disabled).toBe(false);
    expect(screen.getByText("Pausado no seu plano")).toBeTruthy();
    await trocar("Resumo semanal", false);
    await waitFor(() => expect(screen.getByText("Plus e Pro")).toBeTruthy());
    expect(patches()).toEqual([{ weekly_report_enabled: false }]);
    expect(ligada("Resumo semanal")).toBe(false);
    expect(chave("Resumo semanal").props.disabled).toBe(true);
  });

  it("fora do plano e gravado DESLIGADO: desativado e nenhum PATCH (true nunca sai)", async () => {
    await abrir({ weekly_report_available: false, weekly_report_enabled: false, weekly_report_stored_enabled: false });
    expect(chave("Resumo semanal").props.disabled).toBe(true);
    expect(screen.getByText("Plus e Pro")).toBeTruthy();
    await trocar("Resumo semanal", true);
    expect(patches()).toHaveLength(0);
  });

  it("com plano: o toque manda true", async () => {
    await abrir({ weekly_report_enabled: false, weekly_report_stored_enabled: false });
    expect(screen.queryByText("Plus e Pro")).toBeNull();
    await trocar("Resumo semanal", true);
    await waitFor(() => expect(ligada("Resumo semanal")).toBe(true));
    expect(patches()).toEqual([{ weekly_report_enabled: true }]);
  });

  it("o plano cai entre a leitura e o toque: 403 pro_required relê e mostra o texto, nunca o código", async () => {
    const srv = await abrir({ weekly_report_enabled: false, weekly_report_stored_enabled: false });
    srv.s.weekly_report_available = false;
    await trocar("Resumo semanal", true);
    await waitFor(() => expect(screen.getByText(SO_PLUS)).toBeTruthy());
    expect(screen.queryByText(/pro_required/)).toBeNull();
    expect(patches()).toEqual([{ weekly_report_enabled: true }]);
    await waitFor(() => expect(chave("Resumo semanal").props.disabled).toBe(true)); // a releitura trouxe o plano novo
    expect(ligada("Resumo semanal")).toBe(false);
  });
});

describe("Notificações — corrida", () => {
  it("dois toques no mesmo tick com o 1º PATCH segurado: um PATCH só; o 2º toque se perde", async () => {
    const seg = segurar();
    await abrir({}, { gancho: () => seg.promessa.then(() => undefined) });
    await act(async () => {
      fireEvent(chave("Resumo diário"), "valueChange", false);
      fireEvent(chave("Resumo mensal"), "valueChange", false);
    });
    expect(patches()).toEqual([{ daily_report_enabled: false }]);
    expect(chave("Resumo mensal").props.disabled).toBe(true); // tudo trava com escrita em voo
    await act(async () => {
      seg.soltar();
      await drenar();
    });
    await waitFor(() => expect(ligada("Resumo diário")).toBe(false));
    expect(patches()).toHaveLength(1);
    expect(ligada("Resumo mensal")).toBe(true);
  });

  it("GET de foco pendente ANTES do PATCH: a resposta velha é descartada, o PATCH vence", async () => {
    const velho = segurar();
    let n = 0;
    const srvRef: { s?: typeof BASE } = {};
    const srv = servidor({}, { get: async () => { const snap = { ...srvRef.s }; if (++n === 2) await velho.promessa; return resposta(200, snap); } });
    srvRef.s = srv.s;
    await abrirNotificacoes(srv);
    await esperar();
    await appVai("background");
    await appVai("active"); // 2º GET fica preso, com o estado de ANTES
    await trocar("Resumo mensal", false);
    await waitFor(() => expect(ligada("Resumo mensal")).toBe(false));
    await act(async () => {
      velho.soltar();
      await drenar();
    });
    expect(ligada("Resumo mensal")).toBe(false);
  });

  it("foco DURANTE o PATCH: a leitura não sai enquanto ele está em voo e roda depois, sem pintar o valor velho", async () => {
    const seg = segurar();
    await abrir({}, { gancho: () => seg.promessa.then(() => undefined) });
    await trocar("Resumo mensal", false);
    const antes = gets();
    await appVai("background");
    await appVai("active");
    expect(gets()).toBe(antes);
    expect(ligada("Resumo mensal")).toBe(false); // a prévia segura o valor tocado
    await act(async () => {
      seg.soltar();
      await drenar();
    });
    await waitFor(() => expect(gets()).toBe(antes + 1));
    expect(ligada("Resumo mensal")).toBe(false);
  });
});

describe("Notificações — estados", () => {
  const abrirCom = async (get: () => Response, extra = {}, me = {}) => {
    await abrirNotificacoes(servidor({}, { get }), extra, me);
  };

  it("402: 'Escolha um plano', sem controles", async () => {
    await abrirCom(() => resposta(402, { detail: { error: "plan_selection_required" } }));
    await waitFor(() => expect(screen.getByText("Escolha um plano para ver suas notificações.")).toBeTruthy());
    expect(screen.queryByLabelText("Resumo mensal")).toBeNull();
  });

  it("403 de exclusão agendada", async () => {
    await abrirCom(() => resposta(403, { detail: "Conta agendada para exclusão." }));
    await waitFor(() => expect(screen.getByText("Esta conta está agendada para exclusão.")).toBeTruthy());
  });

  it("403 password_required: EsqueciSenha, nunca o texto cru", async () => {
    await abrirCom(() => resposta(403, { detail: { error: "password_required" } }));
    await waitFor(() => expect(screen.getByText("Definir senha")).toBeTruthy());
    expect(screen.queryByText(/password_required/)).toBeNull();
    expect(screen.queryByLabelText("Resumo mensal")).toBeNull();
  });

  it("401 + refresh 401: vai para /entrar", async () => {
    await abrirCom(() => resposta(401, { detail: "expirado" }), { "/auth/refresh": () => resposta(401, { detail: "invalid_refresh_token" }) });
    await waitFor(() => expect(screen).toHavePathname("/entrar"));
  });

  it("500: erro com 'Tentar de novo', que lê de novo", async () => {
    let ok = false;
    const srv = servidor({}, { get: () => (ok ? resposta(200, { ...BASE }) : resposta(500, { detail: "boom" })) });
    await abrirNotificacoes(srv);
    await waitFor(() => expect(screen.getByText("Não conseguimos carregar suas notificações.")).toBeTruthy());
    ok = true;
    fireEvent.press(screen.getByRole("button", { name: "Tentar de novo" }));
    await esperar();
  });

  it("booleano ausente: erro de contrato, NUNCA 'ligado'", async () => {
    const { tip_email_enabled: _fora, ...sem } = BASE;
    await abrirCom(() => resposta(200, sem));
    await waitFor(() => expect(screen.getByRole("button", { name: "Tentar de novo" })).toBeTruthy());
    expect(screen.queryByLabelText("Dicas de uso")).toBeNull();
  });

  it("o uid é o do /auth/me desta carga, nunca outro", async () => {
    const srv = servidor();
    await abrirNotificacoes(srv, { "/settings/7/notifications": srv.rota }, { user_id: 7 });
    await esperar();
    await trocar("Resumo mensal", false);
    await waitFor(() => expect(patches()).toHaveLength(1));
    const caminhos = chamadas().map((c) => c.caminho).filter((c) => c.startsWith("/settings"));
    expect(new Set(caminhos)).toEqual(new Set(["/settings/7/notifications"]));
  });

  it.each([
    ["400 com detail", resposta(400, { detail: "Vincule um e-mail para configurar notificações por e-mail." }), "Vincule um e-mail para configurar notificações por e-mail."],
    ["422", resposta(422, { detail: [{ msg: "x" }] }), FALHA],
    ["200 sem contrato", resposta(200, { ok: true }), FALHA],
  ])("escrita recusada (%s): o texto certo e a releitura", async (_n, r, texto) => {
    await abrir({}, { gancho: () => r });
    await trocar("Resumo mensal", false);
    await waitFor(() => expect(screen.getByText(texto)).toBeTruthy());
    expect(ligada("Resumo mensal")).toBe(true);
    expect(patches()).toHaveLength(1);
  });

  it.each([
    ["402", resposta(402, { detail: { error: "subscription_required" } }), "Escolha um plano para ver suas notificações."],
    ["403 password_required", resposta(403, { detail: { error: "password_required" } }), "Definir senha"],
  ])("escrita com %s: muda de fase", async (_n, r, texto) => {
    await abrir({}, { gancho: () => r });
    await trocar("Resumo mensal", false);
    await waitFor(() => expect(screen.getByText(texto)).toBeTruthy());
  });
});

describe("Configurações", () => {
  it("'Notificações' abre a tela", async () => {
    await abrirNotificacoes(servidor(), {}, {}, "/configuracoes");
    fireEvent.press(await screen.findByRole("button", { name: "Notificações" }));
    await waitFor(() => expect(screen).toHavePathname("/notificacoes"));
    await esperar();
  });
});
