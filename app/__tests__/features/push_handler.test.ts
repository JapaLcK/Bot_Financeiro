/**
 * Decisão P2-A do dono: com o app aberto, o push mostra o aviso no topo e toca o som, sem badge.
 * O handler é registrado quando o módulo `Push` carrega, e só no iOS. O registro de módulos é isolado:
 * o `expo-notifications` dublado de cada `require` é o mesmo que o `Push` daquele registro enxerga.
 */
/* eslint-disable @typescript-eslint/no-require-imports -- `isolateModules` é síncrono: só `require` carrega o módulo no registro isolado. */
function carregar(so: "ios" | "android" = "ios") {
  let notificacoes!: typeof import("expo-notifications");
  jest.isolateModules(() => {
    jest.replaceProperty(require("react-native").Platform, "OS", so); // o `react-native` deste registro, não o do teste
    notificacoes = require("expo-notifications");
    require("@/features/push/Push");
  });
  return jest.mocked(notificacoes.setNotificationHandler);
}

afterEach(() => jest.restoreAllMocks());

it("com o app aberto: banner, lista e som ligados; badge desligado", async () => {
  const definir = carregar();
  expect(definir).toHaveBeenCalledTimes(1);
  const handler = definir.mock.calls[0]![0]!;
  expect(await handler.handleNotification({} as never)).toEqual({
    shouldShowBanner: true,
    shouldShowList: true,
    shouldPlaySound: true,
    shouldSetBadge: false,
  });
});

it("Android: nenhum handler é registrado", () => {
  expect(carregar("android")).not.toHaveBeenCalled();
});
