/**
 * Notificações do app, parte 4: Avisos do Piggy por e-mail (PL-06 B6, PR 3; A1–A8 e A10 do §22.4 do plano, mais B1 da revisão). O servidor falso imita a
 * ordem da rota (ver `notificacoes_apoio.ts`) e manda textos que NÃO são as constantes Python: o que aparece veio do servidor.
 * Os controles negativos (mutante, vermelho, restaura) estão no relato do PR.
 */
import { AccessibilityInfo, StyleSheet } from "react-native";
import { act, fireEvent, screen, waitFor } from "expo-router/testing-library";

import { ErroDeApi } from "@/api/client";
import type { Notificacoes } from "@/api/schemas/conta";
import { DESLIGADOS, DESLIGADOS_D5, FALHA_CARREGAR, FALHA_SALVAR, LIGUE_INSIGHTS, PLUS_E_PRO, SO_PLUS_AVISO, TITULO_AVISO, linhaDoAviso, textoDaEscrita } from "@/features/conta/notificacoes";

import { resposta, segurar } from "./auth_apoio";
import { ALCANCE, BASE, D5_ANTES, MARGEM, PAUSADO, abrirNotificacoes, chave, gets, ligada, patches, servidor, trocar } from "./notificacoes_apoio";
import { drenar, preparar } from "./seguranca_sem_senha_apoio";

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

const FALHA = FALHA_SALVAR;
const PLANO = "Escolha um plano para ver suas notificações.";
const LIBERADA = { insight_alert_email_notice: ALCANCE };
const ADERIU = { ...LIBERADA, insight_alert_email_enabled: true };
const abrir = async (inicial: Partial<Notificacoes> = {}, cortada = false) => {
  const srv = servidor(inicial);
  srv.conta.cortada = cortada;
  await abrirNotificacoes(srv);
  await waitFor(() => expect(screen.getByText(cortada ? PLANO : "Resumo mensal")).toBeTruthy());
  return srv;
};
const desligarAgora = () =>
  act(async () => {
    fireEvent.press(screen.getByRole("button", { name: "Desligar agora" }));
    for (let i = 0; i < 5; i++) await drenar();
  });
/** O GET passa a responder 500 quando `falhar.get` liga: a releitura depois de uma escrita falha. */
const comGetFalhando = async (srv: ReturnType<typeof servidor>) => {
  const falhar = { get: false };
  await abrirNotificacoes(srv, { "/settings/1/notifications": (o) => (falhar.get && o.method !== "PATCH" ? resposta(500, { detail: "x" }) : srv.rota(o)) });
  return falhar;
};
const anunciou = (texto: string) => jest.mocked(AccessibilityInfo.announceForAccessibility).mock.calls.some(([t]) => t === texto);

describe("Avisos do Piggy — a linha", () => {
  it("A1: `notice` nulo esconde a linha; com `notice` aparece desligada com o texto do servidor, e ligar manda só o campo", async () => {
    await abrir();
    expect(screen.queryByLabelText(TITULO_AVISO)).toBeNull();
    expect(screen.queryByText(ALCANCE)).toBeNull();
    screen.unmount();
    preparar();

    await abrir(LIBERADA);
    expect(ligada(TITULO_AVISO)).toBe(false);
    expect(screen.getByText(ALCANCE)).toBeTruthy();
    await trocar(TITULO_AVISO, true);
    await waitFor(() => expect(ligada(TITULO_AVISO)).toBe(true));
    expect(patches()).toEqual([{ insight_alert_email_enabled: true }]);
  });

  it.each<[string, Partial<Notificacoes>, { travado: boolean; selo?: string }]>([
    ["sem adesão, tudo em ordem", {}, { travado: false }],
    ["sem adesão, sem plano", { insight_alert_email_available: false }, { travado: true, selo: PLUS_E_PRO }],
    ["sem adesão, Insights desligado", { insight_email_enabled: false }, { travado: true, selo: LIGUE_INSIGHTS }],
    ["sem e-mail", { email_notifications_available: false, insight_email_enabled: false }, { travado: true }],
    ["com adesão, com plano", { insight_alert_email_enabled: true }, { travado: false }],
    ["com adesão, carência", { insight_alert_email_enabled: true, insight_alert_email_available: false }, { travado: false }],
  ])("A2: matriz da linha — %s", (_n, d, esperado) => {
    expect(linhaDoAviso({ ...BASE, ...LIBERADA, ...d })).toMatchObject({ selo: undefined, ...esperado });
  });

  it("A2: na carência, com adesão, o estado do servidor aparece e desligar continua possível (corpo exato, depois relê)", async () => {
    const srv = await abrir({ ...ADERIU, insight_alert_email_available: false });
    expect(screen.getByText(PAUSADO)).toBeTruthy();
    expect(chave(TITULO_AVISO).props.disabled).toBe(false);
    const antes = gets();
    await trocar(TITULO_AVISO, false);
    await waitFor(() => expect(ligada(TITULO_AVISO)).toBe(false));
    expect(patches()).toEqual([{ insight_alert_email_enabled: false }]);
    await waitFor(() => expect(gets()).toBeGreaterThan(antes));
    expect(srv.s.insight_alert_email_enabled).toBe(false);
    expect(screen.queryByText(PAUSADO)).toBeNull();
    expect(screen.queryByText(FALHA)).toBeNull();
  });

  it("B1: a releitura falha depois de desligar na carência: a resposta completa do PATCH já leva as notas, e a tela não mente", async () => {
    const srv = servidor({ ...ADERIU, insight_alert_email_available: false });
    const falhar = await comGetFalhando(srv);
    await waitFor(() => expect(screen.getByText(PAUSADO)).toBeTruthy());
    falhar.get = true;
    await trocar(TITULO_AVISO, false);
    await act(async () => { for (let i = 0; i < 5; i++) await drenar(); });
    expect(ligada(TITULO_AVISO)).toBe(false);
    expect(screen.queryByText(PAUSADO)).toBeNull();
    expect(screen.queryByText(D5_ANTES)).toBeNull();
    expect(chave("Insights financeiros").props.accessibilityHint).toBeUndefined();
    expect(screen.queryByText(FALHA_CARREGAR)).toBeNull(); // o que está na tela é o estado inteiro do servidor
  });

  it("B1: resposta curta (cortada no meio) e a releitura falha: diz que desligou, sai da tela da conta com plano e diz que não carregou", async () => {
    const srv = servidor(ADERIU);
    const falhar = await comGetFalhando(srv);
    await waitFor(() => expect(screen.getByText("Resumo mensal")).toBeTruthy());
    srv.conta.cortada = true;
    falhar.get = true;
    await trocar(TITULO_AVISO, false);
    await waitFor(() => expect(screen.getByRole("button", { name: "Tentar de novo" })).toBeTruthy());
    expect(screen.getByText(FALHA_CARREGAR)).toBeTruthy();
    expect(screen.queryByText("Resumo mensal")).toBeNull(); // nada da tela de antes, que a resposta curta desmentiu
    expect(screen.getByText(DESLIGADOS)).toBeTruthy(); // o desligar deu certo
    expect(anunciou(DESLIGADOS)).toBe(true);
    expect(AccessibilityInfo.sendAccessibilityEvent).toHaveBeenCalledWith(expect.anything(), "focus"); // o interruptor tocado sumiu
    expect(screen.queryByText(FALHA)).toBeNull();
  });

  it("A2: sem plano e sem adesão a linha fica travada com o selo, e o toque não manda nada", async () => {
    await abrir({ ...LIBERADA, insight_alert_email_available: false });
    expect(chave(TITULO_AVISO).props.disabled).toBe(true);
    expect(screen.getByText(PLUS_E_PRO, { includeHiddenElements: true })).toBeTruthy(); // visível; para o leitor, só no hint (A10)
    await trocar(TITULO_AVISO, true);
    expect(patches()).toEqual([]);
  });
});

describe("Avisos do Piggy — D5 (desligar Insights leva a adesão)", () => {
  it("A4: com adesão a nota do servidor aparece sob Insights; desligar Insights mostra e anuncia 'desligados'", async () => {
    await abrir(ADERIU);
    expect(screen.getByText(D5_ANTES)).toBeTruthy();
    await trocar("Insights financeiros", false);
    await waitFor(() => expect(screen.getByText(DESLIGADOS_D5)).toBeTruthy());
    expect(anunciou(DESLIGADOS_D5)).toBe(true);
    expect(ligada(TITULO_AVISO)).toBe(false);
    expect(screen.queryByText(D5_ANTES)).toBeNull();
    expect(patches()).toEqual([{ insight_email_enabled: false }]);
    expect(AccessibilityInfo.sendAccessibilityEvent).not.toHaveBeenCalled(); // na fase pronta o foco fica onde a pessoa tocou
    await trocar("Resumo mensal", false); // a próxima escrita apaga a mensagem
    await waitFor(() => expect(ligada("Resumo mensal")).toBe(false));
    expect(screen.queryByText(DESLIGADOS_D5)).toBeNull();
  });

  it("A4: sem adesão antes não há nota nem mensagem; ligar Insights também não", async () => {
    await abrir(LIBERADA);
    expect(screen.queryByText(D5_ANTES)).toBeNull();
    await trocar("Insights financeiros", false);
    await waitFor(() => expect(ligada("Insights financeiros")).toBe(false));
    await trocar("Insights financeiros", true);
    await waitFor(() => expect(ligada("Insights financeiros")).toBe(true));
    expect(screen.queryByText(DESLIGADOS_D5)).toBeNull();
    expect(anunciou(DESLIGADOS_D5)).toBe(false);
  });
});

describe("Avisos do Piggy — conta cortada (D2)", () => {
  it("A5: 402 com a flag mostra o texto da margem DO SERVIDOR e 'Desligar agora' manda o corpo exato; resposta curta vira 'Avisos desligados.'", async () => {
    const srv = await abrir(ADERIU, true);
    expect(screen.getByText(MARGEM)).toBeTruthy(); // o texto do servidor falso ("2 horas"), não uma cópia local
    expect(screen.queryByLabelText(TITULO_AVISO)).toBeNull(); // sem interruptor
    await desligarAgora();
    expect(patches()).toEqual([{ insight_alert_email_enabled: false }]);
    await waitFor(() => expect(screen.getByText(DESLIGADOS)).toBeTruthy());
    expect(srv.s.insight_alert_email_enabled).toBe(false);
    expect(screen.queryByRole("button", { name: "Desligar agora" })).toBeNull();
    expect(screen.queryByText(MARGEM)).toBeNull(); // a releitura trouxe o 402 sem flag
    expect(screen.getByText(PLANO)).toBeTruthy();
    expect(screen.queryByText(FALHA)).toBeNull();
    expect(anunciou(DESLIGADOS)).toBe(true);
    expect(AccessibilityInfo.sendAccessibilityEvent).toHaveBeenCalledWith(expect.anything(), "focus");
    expect(patches()).toHaveLength(1);
  });

  it("A5: o botão some assim que o desligar responde, sem esperar a releitura (que fica presa aqui)", async () => {
    const seg = segurar();
    const srv = servidor(ADERIU);
    srv.conta.cortada = true;
    let prender = false;
    await abrirNotificacoes(srv, { "/settings/1/notifications": (o) => (prender && o.method !== "PATCH" ? seg.promessa.then(() => srv.rota(o)) : srv.rota(o)) });
    await waitFor(() => expect(screen.getByText(MARGEM)).toBeTruthy());
    prender = true;
    await desligarAgora();
    expect(screen.getByText(DESLIGADOS)).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Desligar agora" })).toBeNull();
    await act(async () => {
      seg.soltar();
      await drenar();
    });
  });

  it.each([
    ["402 sem adesão", { error: "subscription_required" }],
    ["plan_selection_required, mesmo com flag", { error: "plan_selection_required", insight_alert_email_enabled: true, insight_alert_email_lapsed_notice: MARGEM }],
    ["flag sem o texto (servidor sem a P13)", { error: "subscription_required", insight_alert_email_enabled: true }],
  ])("A5: %s: 'Escolha um plano' sem o bloco", async (_n, detail) => {
    await abrirNotificacoes(servidor({}, { get: () => resposta(402, { detail }) }));
    await waitFor(() => expect(screen.getByText(PLANO)).toBeTruthy());
    expect(screen.queryByRole("button", { name: "Desligar agora" })).toBeNull();
    expect(screen.queryByText(MARGEM)).toBeNull();
  });

  it("A6: cortada entre a leitura e o toque na linha: a resposta curta não é erro; relê e cai na fase `plano`", async () => {
    const srv = await abrir(ADERIU);
    srv.conta.cortada = true;
    await trocar(TITULO_AVISO, false);
    await waitFor(() => expect(screen.getByText(PLANO)).toBeTruthy());
    expect(patches()).toEqual([{ insight_alert_email_enabled: false }]);
    expect(srv.s.insight_alert_email_enabled).toBe(false);
    expect(screen.getByText(DESLIGADOS)).toBeTruthy();
    expect(screen.queryByText(FALHA)).toBeNull();
    expect(screen.queryByRole("button", { name: "Desligar agora" })).toBeNull();
    expect(screen.queryByText(FALHA_CARREGAR)).toBeNull();
  });

  it("A5: 'Desligar agora' com a rede caindo: o banner de erro aparece na fase `plano` e o bloco volta, ativo", async () => {
    const srv = servidor(ADERIU);
    srv.conta.cortada = true;
    let cair = false;
    await abrirNotificacoes(srv, { "/settings/1/notifications": (o) => (cair && o.method === "PATCH" ? Promise.reject(new TypeError("Network request failed")) : srv.rota(o)) });
    await waitFor(() => expect(screen.getByText(MARGEM)).toBeTruthy());
    cair = true;
    await desligarAgora();
    await waitFor(() => expect(screen.getByText(FALHA)).toBeTruthy());
    const botao = screen.getByRole("button", { name: "Desligar agora" });
    expect(botao.props.accessibilityState?.disabled).toBeFalsy();
    expect(srv.s.insight_alert_email_enabled).toBe(true);
    expect(screen.queryByText(DESLIGADOS)).toBeNull();
  });

  it("A8: 402 numa escrita, e a releitura falha: a tela sai dos dados velhos (a conta foi cortada) e diz que não carregou", async () => {
    const srv = servidor({});
    const falhar = await comGetFalhando(srv);
    await waitFor(() => expect(screen.getByText("Resumo mensal")).toBeTruthy());
    srv.conta.cortada = true;
    falhar.get = true;
    await trocar("Resumo mensal", false);
    await waitFor(() => expect(screen.getByText(FALHA_CARREGAR)).toBeTruthy());
    expect(screen.queryByText("Resumo mensal")).toBeNull();
  });

  it("A8: 402 numa escrita qualquer relê e acha a flag: aparece o bloco", async () => {
    const srv = await abrir(ADERIU);
    srv.conta.cortada = true;
    await trocar("Resumo mensal", false);
    await waitFor(() => expect(screen.getByText(MARGEM)).toBeTruthy());
    expect(screen.getByRole("button", { name: "Desligar agora" })).toBeTruthy();
    expect(patches()).toEqual([{ monthly_report_enabled: false }]);
  });
});

describe("Avisos do Piggy — erros ao ligar", () => {
  it("A7: o plano cai entre a leitura e o toque: 403 `insights` mostra o texto do aviso, nunca o do semanal", async () => {
    const srv = await abrir(LIBERADA);
    srv.s.insight_alert_email_available = false;
    await trocar(TITULO_AVISO, true);
    await waitFor(() => expect(screen.getByText(SO_PLUS_AVISO)).toBeTruthy());
    expect(screen.queryByText(/resumo semanal/)).toBeNull();
    await waitFor(() => expect(chave(TITULO_AVISO).props.disabled).toBe(true)); // a releitura trouxe o plano novo
  });

  it("A7: `pro_required` de um recurso que o app não conhece cai no genérico, nunca no texto de outro recurso", () => {
    const pro = (feature?: string) => new ErroDeApi(403, "pro_required", { detail: { error: "pro_required", feature } });
    expect(textoDaEscrita(pro("outro"))).toBe(FALHA_SALVAR);
    expect(textoDaEscrita(pro())).toBe(FALHA_SALVAR);
    expect(textoDaEscrita(pro("insights"))).toBe(SO_PLUS_AVISO); // âncora: o comparador reconhece o que conhece
  });

  it("A7: 409 mostra o texto do servidor", async () => {
    const srv = await abrir(LIBERADA);
    srv.s.insight_email_enabled = false; // desligado por fora entre a leitura e o toque
    await trocar(TITULO_AVISO, true);
    await waitFor(() => expect(screen.getByText("Ligue Insights financeiros primeiro.")).toBeTruthy());
    expect(ligada(TITULO_AVISO)).toBe(false);
  });

  it("A7: 403 da liberação cai no genérico e a releitura esconde a linha", async () => {
    const srv = await abrir(LIBERADA);
    srv.s.insight_alert_email_notice = null;
    await trocar(TITULO_AVISO, true);
    await waitFor(() => expect(screen.getByText(FALHA)).toBeTruthy());
    await waitFor(() => expect(screen.queryByLabelText(TITULO_AVISO)).toBeNull());
  });
});

describe("Avisos do Piggy — acessibilidade", () => {
  const opacidades = (texto: string) => {
    const lista: number[] = [];
    for (let n = screen.getByText(texto).parent; n; n = n.parent) {
      const o = StyleSheet.flatten(n.props.style)?.opacity;
      if (typeof o === "number") lista.push(o);
    }
    return lista;
  };

  it("A10: o hint dos interruptores leva o motivo da trava e os textos do servidor, e o texto longo fica fora da linha esmaecida", async () => {
    await abrir({ ...LIBERADA, insight_alert_email_available: false }); // linha travada (opacity 0,45)
    expect(chave(TITULO_AVISO).props.accessibilityHint).toBe(`${PLUS_E_PRO}. ${ALCANCE}`); // o selo, como no <label> do web
    // ...e por isso o selo visível sai da árvore de acessibilidade (lido uma vez só). O do Resumo semanal, sem hint, fica: notificacoes_tela.
    expect(screen.getByText(PLUS_E_PRO, { includeHiddenElements: true }).props).toMatchObject({ accessibilityElementsHidden: true, importantForAccessibility: "no-hide-descendants" });
    expect(screen.queryByText(PLUS_E_PRO)).toBeNull();
    expect(opacidades(TITULO_AVISO)).toContain(0.45); // âncora: a linha travada esmaece
    expect(opacidades(ALCANCE)).not.toContain(0.45);
    screen.unmount();
    preparar();

    await abrir({ ...ADERIU, insight_alert_email_available: false });
    expect(chave(TITULO_AVISO).props.accessibilityHint).toBe(`${ALCANCE} ${PAUSADO}`);
    expect(chave("Insights financeiros").props.accessibilityHint).toBe(D5_ANTES);
    screen.unmount();
    preparar();

    await abrir({ ...LIBERADA, insight_email_enabled: false });
    expect(chave(TITULO_AVISO).props.accessibilityHint).toBe(`${LIGUE_INSIGHTS}. ${ALCANCE}`);
  });
});
