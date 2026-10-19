/**
 * Notificações do app, parte 2: a conta que troca no cofre, o estado ATUAL na hora de confirmar o seletor do Android,
 * e as lacunas da suíte principal (`notificacoes_tela.test.tsx`; separado por causa do teto de 350 linhas).
 */
import { DateTimePickerAndroid } from "@react-native-community/datetimepicker";
import { Platform } from "react-native";
import { act, fireEvent, screen, waitFor } from "expo-router/testing-library";

import * as servicosConta from "@/services/conta";

import { fetchFalso, resposta, segurar } from "./auth_apoio";
import { BASE, abrirNotificacoes, gets, ligada, patches, servidor, trocar } from "./notificacoes_apoio";
import { appVai, drenar, preparar } from "./seguranca_sem_senha_apoio";

jest.mock("@react-native-community/datetimepicker", () => ({
  __esModule: true,
  default: (p: object) => jest.requireActual("react").createElement(jest.requireActual("react-native").View, { testID: "seletor-hora", ...p }),
  DateTimePickerAndroid: { open: jest.fn() },
}));

beforeEach(preparar);
afterEach(() => jest.restoreAllMocks());

const cofre = (globalThis as unknown as { __cofreDeTeste: Map<string, string> }).__cofreDeTeste;
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

describe("Notificações — conta e estado atual", () => {
  it("Android: o seletor aberto confirma com o estado de AGORA (diário desligado por fora não é religado)", async () => {
    emAndroid();
    const srv = await abrir();
    fireEvent.press(screen.getByRole("button", { name: /Horário dos resumos/ }));
    const opcoes = (DateTimePickerAndroid.open as jest.Mock).mock.calls.at(-1)[0];
    srv.s.daily_report_enabled = false; // desligado por fora (WhatsApp/painel) com o diálogo aberto
    await appVai("background");
    await appVai("active");
    await waitFor(() => expect(ligada("Resumo diário")).toBe(false)); // a tela JÁ sabe que está desligado
    await act(async () => {
      opcoes.onValueChange({ type: "set" }, hora(8, 5));
      await drenar();
    });
    expect(patches()).toEqual([{ daily_report_hour: 8, daily_report_minute: 5, daily_report_enabled: false }]);
  });

  it("a conta troca no cofre com o PATCH em voo: some o dado da anterior, relê, e nada é regravado", async () => {
    const seg = segurar();
    await abrir({}, { gancho: () => seg.promessa.then(() => undefined) });
    await trocar("Resumo mensal", false);
    const antes = gets();
    cofre.set("pb.credenciais", JSON.stringify({ access: "access-b", refresh: "rt_b" })); // outra linhagem assumiu o cofre
    await act(async () => {
      seg.soltar();
      for (let i = 0; i < 10; i++) await drenar();
    });
    await waitFor(() => expect(gets()).toBeGreaterThan(antes));
    expect(patches()).toHaveLength(1);
  });

  it("RequisicaoSuperada na escrita: enquanto relê mostra 'Carregando' (sem dado da conta anterior, sem banner de erro) e depois a conta nova", async () => {
    const seg = segurar(), segMe = segurar();
    let presas = 0, prender = false;
    const srv = servidor({}, { gancho: () => seg.promessa.then(() => undefined) });
    await abrirNotificacoes(srv, {
      "/auth/me": () => {
        const r = () => resposta(200, { user_id: 1, email: "sofia@x.com", display_name: "Sofia", app_access: true });
        if (!prender) return r();
        presas++;
        return segMe.promessa.then(r);
      },
    });
    await esperar();
    await trocar("Resumo mensal", false);
    cofre.set("pb.credenciais", JSON.stringify({ access: "access-b", refresh: "rt_b" }));
    prender = true; // só o /auth/me da RELEITURA fica preso
    await act(async () => {
      seg.soltar();
      for (let i = 0; i < 10; i++) await drenar();
    });
    await waitFor(() => expect(presas).toBeGreaterThan(0));
    expect(screen.queryByLabelText("Carregando")).toBeTruthy();
    expect(screen.queryByLabelText("Resumo mensal")).toBeNull();
    expect(screen.queryByText("Não conseguimos salvar. Tente de novo.")).toBeNull();
    await act(async () => {
      segMe.soltar();
      for (let i = 0; i < 10; i++) await drenar();
    });
    await waitFor(() => expect(ligada("Resumo mensal")).toBe(false)); // o que a conta nova tem no servidor
    expect(screen.queryByLabelText("Carregando")).toBeNull();
    expect(screen.queryByText("Não conseguimos salvar. Tente de novo.")).toBeNull();
  });

  it("Android: o seletor aberto na conta 1 e confirmado depois da troca para a conta 2 grava na conta 2", async () => {
    emAndroid();
    let conta = 1;
    const srv2 = servidor({ daily_report_hour: 7 });
    await abrirNotificacoes(servidor(), {
      "/auth/me": () => resposta(200, { user_id: conta, email: "sofia@x.com", display_name: "Sofia", app_access: true }),
      "/settings/2/notifications": srv2.rota,
    });
    await esperar();
    fireEvent.press(screen.getByRole("button", { name: /Horário dos resumos/ }));
    const opcoes = (DateTimePickerAndroid.open as jest.Mock).mock.calls.at(-1)[0];
    conta = 2;
    await appVai("background");
    await appVai("active");
    await waitFor(() => expect(fetchFalso.mock.calls.some(([u]: [string]) => String(u).endsWith("/settings/2/notifications"))).toBe(true));
    await act(async () => void (await drenar()));
    await act(async () => {
      opcoes.onValueChange({ type: "set" }, hora(8, 5));
      await drenar();
    });
    const alvos = fetchFalso.mock.calls.filter(([, o]: [string, RequestInit]) => o.method === "PATCH").map(([u]: [string]) => String(u).replace(/^https?:\/\/[^/]+/, ""));
    expect(alvos).toEqual(["/settings/2/notifications"]);
    expect(srv2.s.daily_report_hour).toBe(8);
  });
});

describe("Notificações — lacunas da suíte", () => {
  it("TODO booleano do contrato ausente é erro, nunca 'ligado'", async () => {
    const campos = Object.keys(BASE).filter((k) => typeof BASE[k as keyof typeof BASE] === "boolean");
    for (const campo of campos) {
      const { [campo]: _fora, ...sem } = BASE as unknown as Record<string, unknown>;
      await abrirNotificacoes(servidor({}, { get: () => resposta(200, sem) }));
      await waitFor(() => expect(screen.getByRole("button", { name: "Tentar de novo" })).toBeTruthy(), { timeout: 2000 }).catch(() => {
        throw new Error(`campo ausente virou tela pronta: ${campo}`);
      });
      screen.unmount();
      preparar();
    }
  });

  it("iOS: girar a roda em vários passos não manda PATCH; só o fechamento manda, uma vez", async () => {
    await abrir();
    const seletor = screen.getByTestId("seletor-hora");
    await act(async () => {
      fireEvent(seletor, "valueChange", {}, hora(10, 0));
      fireEvent(seletor, "valueChange", {}, hora(10, 1));
      fireEvent(seletor, "valueChange", {}, hora(10, 2));
      await drenar();
    });
    expect(patches()).toHaveLength(0);
    await act(async () => {
      fireEvent(seletor, "dismiss");
      await drenar();
    });
    expect(patches()).toEqual([{ daily_report_hour: 10, daily_report_minute: 2, daily_report_enabled: true }]);
  });

  it("RequisicaoSuperada na LEITURA (volta do fundo): some o dado da conta anterior e relê a conta nova", async () => {
    const seg = segurar();
    let conta = 1, prender = false;
    const srv2 = servidor({ monthly_report_enabled: false });
    await abrirNotificacoes(servidor({}, { get: () => (prender ? seg.promessa.then(() => resposta(200, { ...BASE })) : resposta(200, { ...BASE })) }), {
      "/auth/me": () => resposta(200, { user_id: conta, email: "sofia@x.com", display_name: "Sofia", app_access: true }),
      "/settings/2/notifications": srv2.rota,
    });
    await esperar();
    prender = true; // o GET da releitura da conta 1 fica preso
    await appVai("background");
    await appVai("active");
    cofre.set("pb.credenciais", JSON.stringify({ access: "access-b", refresh: "rt_b" })); // outra conta assumiu o cofre
    conta = 2;
    await act(async () => {
      seg.soltar();
      for (let i = 0; i < 10; i++) await drenar();
    });
    await waitFor(() => expect(ligada("Resumo mensal")).toBe(false)); // o que a conta 2 tem no servidor
    expect(fetchFalso.mock.calls.some(([u]: [string]) => String(u).endsWith("/settings/2/notifications"))).toBe(true);
  });

  it("a conta troca ENTRE o /auth/me e a leitura (403 de uid alheio): some o dado da anterior e relê a conta nova", async () => {
    let conta = 1, trocarAntes = false;
    const srv2 = servidor({ monthly_report_enabled: false });
    const daB = (o: RequestInit) => /access-b/.test((o.headers as Record<string, string>)["Authorization"] ?? "");
    await abrirNotificacoes(servidor({}, { get: (o) => (daB(o) ? resposta(403, { detail: "Acesso negado" }) : resposta(200, { ...BASE })) }), {
      "/auth/me": () => resposta(200, { user_id: conta, email: "sofia@x.com", display_name: "Sofia", app_access: true }),
      "/settings/2/notifications": srv2.rota,
    });
    await esperar();
    const real = servicosConta.lerNotificacoes;
    jest.spyOn(servicosConta, "lerNotificacoes").mockImplementation(async (uid) => {
      if (trocarAntes) { // outra conta assume o cofre depois do /auth/me da conta 1 e antes da 2ª leitura
        trocarAntes = false;
        cofre.set("pb.credenciais", JSON.stringify({ access: "access-b", refresh: "rt_b" }));
        conta = 2;
      }
      return real(uid);
    });
    trocarAntes = true;
    await appVai("background");
    await appVai("active");
    await act(async () => { for (let i = 0; i < 10; i++) await drenar(); });
    await waitFor(() => expect(ligada("Resumo mensal")).toBe(false)); // o que a conta 2 tem no servidor, não o da conta 1
    expect(fetchFalso.mock.calls.some(([u]: [string]) => String(u).endsWith("/settings/2/notifications"))).toBe(true);
  });

  it("releitura que FALHA com dados na tela mantém a tela (não troca por 'erro')", async () => {
    let falhar = false;
    await abrirNotificacoes(servidor({}, { get: () => (falhar ? resposta(500, { detail: "boom" }) : resposta(200, { ...BASE })) }));
    await esperar();
    falhar = true;
    await appVai("background");
    const antes = gets();
    await appVai("active");
    await act(async () => void (await drenar()));
    expect(gets()).toBeGreaterThan(antes); // a releitura saiu (e falhou); sem isto a tela intacta poderia ser só uma tela que nunca releu
    expect(screen.getByLabelText("Resumo mensal")).toBeTruthy();
    expect(screen.queryByText("Não conseguimos carregar suas notificações.")).toBeNull();
  });

  it("Android: com um PATCH em voo a linha 'Horário dos resumos' fica desativada e o toque não abre o seletor", async () => {
    emAndroid();
    const seg = segurar();
    await abrir({}, { gancho: () => seg.promessa.then(() => undefined) });
    await trocar("Resumo mensal", false);
    const linha = screen.getByRole("button", { name: /Horário dos resumos/ });
    expect(linha.props.accessibilityState).toMatchObject({ disabled: true });
    fireEvent.press(linha);
    expect(DateTimePickerAndroid.open).not.toHaveBeenCalled();
    await act(async () => {
      seg.soltar();
      await drenar();
    });
  });

  it("semanal gravado ligado fora do plano: com o PATCH em voo o switch já mostra desligado", async () => {
    const seg = segurar();
    await abrir({ weekly_report_available: false, weekly_report_enabled: false, weekly_report_stored_enabled: true }, { gancho: () => seg.promessa.then(() => undefined) });
    await trocar("Resumo semanal", false);
    expect(ligada("Resumo semanal")).toBe(false);
    await act(async () => {
      seg.soltar();
      await drenar();
    });
  });
});
