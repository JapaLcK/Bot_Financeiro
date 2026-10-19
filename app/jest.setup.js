// Estado de TODOS os dublês abaixo. As fábricas de `jest.mock` são içadas para o
// topo do arquivo, e um import cedo (o mock do reanimated carrega o css-interop)
// pode chamar um dublê antes de qualquer `const` daqui executar: lido de uma
// `const`, o estado seria `undefined`. Declaração de função sobe inteira, então
// DENTRO das fábricas leia sempre `mockEstado().x`, nunca uma `const`. O prefixo
// `mock` é exigência do Jest para referenciar de dentro da fábrica. O estado
// mora no `globalThis` (um por arquivo de teste) e nasce no 1º acesso.
function mockEstado() {
  return (globalThis.__mockSetup ??= {
    cofre: new Map(),
    falha: { escrita: false, apagar: false },
    atraso: { escrita: null },
    fonte: { carregado: true, erro: null },
    acessibilidade: { valor: false, ouvintes: new Set() },
    app: { atual: "active", ouvintes: new Set() },
    push: { permissao: "denied", promptResponde: "granted", token: "apns-teste", ambiente: null, resposta: null, ouvintes: new Set(), ouvintesToken: new Set(), atrasoDesregistro: null },
  });
}

// SecureStore é nativo: no Jest ele não existe. O dublê guarda em memória, que
// é o suficiente para o que os testes medem (o cliente de API, não o keychain).
const mockCofre = mockEstado().cofre;
global.__cofreDeTeste = mockCofre;
// A falha é injetada por SINALIZADOR lido dentro do dublê, não por
// reatribuição do método: o módulo sob teste importa o namespace, e trocar a
// propriedade depois do import não chega até ele (medido — o teste passava
// verde sem exercitar nada).
// Dois sinalizadores, e não um: gravar e apagar falham por motivos diferentes,
// e um teste que só consegue quebrar os dois juntos não distingue "o conserto
// limpou" de "nem o conserto conseguiu limpar".
const mockFalha = mockEstado().falha;
// Um portão na GRAVAÇÃO, para os testes conseguirem parar o tempo dentro da
// fila do cofre e provar o que acontece na janela entre conferir e gravar.
const mockAtraso = mockEstado().atraso;
global.__atrasarEscritaNoCofre = (p) => (mockAtraso.escrita = p);
global.__falharEscritaNoCofre = (v) => (mockFalha.escrita = v);
global.__falharApagarNoCofre = (v) => (mockFalha.apagar = v);
jest.mock("expo-secure-store", () => ({
  getItemAsync: async (k) => (mockEstado().cofre.has(k) ? mockEstado().cofre.get(k) : null),
  setItemAsync: async (k, v) => {
    if (mockEstado().atraso.escrita) {
      const espera = mockEstado().atraso.escrita;
      mockEstado().atraso.escrita = null;
      await espera;
    }
    if (mockEstado().falha.escrita) throw new Error("keychain recusou");
    mockEstado().cofre.set(k, v);
  },
  deleteItemAsync: async (k) => {
    if (mockEstado().falha.apagar) throw new Error("keychain recusou apagar");
    mockEstado().cofre.delete(k);
  },
}));

// `Constants.expoConfig` vem do app.config.ts em tempo de build; no Jest ele
// chega vazio. O cliente de API lê `extra.apiUrl` de lá, então sem este dublê
// TODO teste do cliente falha na primeira linha — e por um motivo que não tem
// nada a ver com o que ele mede.
// `executionEnvironment`/`scheme`: o binário de dev (não o Expo Go), que é o
// caso em que `services/openFinance.ts` manda o `app_scheme`.
jest.mock("expo-constants", () => ({
  __esModule: true,
  ExecutionEnvironment: { Bare: "bare", Standalone: "standalone", StoreClient: "storeClient" },
  default: {
    executionEnvironment: "bare",
    expoConfig: { extra: { apiUrl: "http://backend.teste" }, version: "0.1.0", scheme: "pigbank-dev" },
  },
}));

// Sentry e PostHog não inicializam sem chave (é o desenho), mas o import
// carrega binding nativo. Dublê no-op mantém o teste sobre o cliente de API.
jest.mock("@sentry/react-native", () => ({ init: jest.fn() }));
jest.mock("posthog-react-native", () => ({
  __esModule: true,
  default: class { capture() {} identify() {} reset() {} },
}));

// expo-haptics é nativo: no Jest a chamada precisa existir como espiã (para o
// teste conferir SE disparou), não travar por módulo ausente. Usado pelos
// componentes de PR C1 (Chip, SegmentedControl, Input).
jest.mock("expo-haptics", () => ({
  selectionAsync: jest.fn(() => Promise.resolve()),
  notificationAsync: jest.fn(() => Promise.resolve()),
  NotificationFeedbackType: { Success: "success", Warning: "warning", Error: "error" },
}));

// expo-system-ui é nativo, e todo `TemaProvider` o chama (ver `ui/tema.tsx`):
// o dublê fica aqui, e não num teste só, para que quem monta o `_layout.tsx`
// real também não rode o módulo de verdade. `tema.test.tsx` espia esta espiã.
jest.mock("expo-system-ui", () => ({
  setBackgroundColorAsync: jest.fn(() => Promise.resolve()),
}));

// expo-font: controlável por teste. `layout.test.tsx` precisa dos três
// estados do `_layout.tsx` (carregando, carregado, erro) sem depender de TTF
// de verdade — o padrão default é "carregado", o caso comum.
const mockFonte = mockEstado().fonte;
global.__definirEstadoDaFonte = (v) => Object.assign(mockFonte, v);
jest.mock("expo-font", () => ({
  useFonts: () => [mockEstado().fonte.carregado, mockEstado().fonte.erro],
}));

// AccessibilityInfo: `motion.test.ts` prova `useReduzirMovimento` controlando
// a leitura e disparando o evento, sem sistema operacional real por trás.
const mockAcessibilidade = mockEstado().acessibilidade;
global.__definirReduzirMovimento = (v) => {
  mockAcessibilidade.valor = v;
};
global.__dispararReduzirMovimento = (v) => {
  mockAcessibilidade.valor = v;
  mockAcessibilidade.ouvintes.forEach((fn) => fn(v));
};
jest.mock("react-native/Libraries/Components/AccessibilityInfo/AccessibilityInfo", () => ({
  // O módulo real é `export default AccessibilityInfo`; `react-native/index.js`
  // lê `require(caminho).default` (getter, ver `get AccessibilityInfo()`).
  // Sem o `default`, o dublê fica invisível e `AccessibilityInfo` chega
  // `undefined` em quem importa de `"react-native"` — foi o que aconteceu
  // antes deste comentário existir.
  __esModule: true,
  default: {
    isReduceMotionEnabled: () => Promise.resolve(mockEstado().acessibilidade.valor),
    addEventListener: (_evento, fn) => {
      mockEstado().acessibilidade.ouvintes.add(fn);
      return { remove: () => mockEstado().acessibilidade.ouvintes.delete(fn) };
    },
    // `Toast` (PR C2) anuncia cada mensagem — sem espiã aqui a chamada
    // lançaria "não é uma função" no Jest (o módulo real é nativo).
    announceForAccessibility: jest.fn(),
    // `PilhaDeAvisos` move o foco do leitor quando o aviso visível sai da lista.
    sendAccessibilityEvent: jest.fn(), // (setAccessibilityFocus está deprecada no RN 0.86)
  },
}));

// expo-web-browser é nativo (`ASWebAuthenticationSession`). O "Continuar com
// Google" (`features/auth/google.ts`) só lê o que `openAuthSessionAsync`
// devolve: cada teste diz o retorno com `jest.mocked(...).mockResolvedValue`.
jest.mock("expo-web-browser", () => ({ openAuthSessionAsync: jest.fn() }));

// expo-apple-authentication é nativo (`ASAuthorizationController`). O
// "Continuar com a Apple" (`features/auth/apple.ts`) só lê o que `signInAsync`
// devolve: cada teste diz o retorno com `jest.mocked(...)`. O botão do sistema
// vira um `Pressable` com o rótulo que o VoiceOver lê no nativo, e repassa as
// props (estilo, tipo, raio) para o teste conferir. Fora da fábrica: o babel
// do Nativewind reescreve o `createElement`, e a fábrica não pode citar o import dele.
function mockBotaoApple(props) {
  const { Pressable } = require("react-native");
  return require("react").createElement(Pressable, {
    ...props,
    accessibilityRole: "button",
    accessibilityLabel: "Continuar com a Apple",
  });
}
jest.mock("expo-apple-authentication", () => {
  return {
    signInAsync: jest.fn(),
    formatFullName: (n) => [n.givenName, n.familyName].filter(Boolean).join(" "),
    AppleAuthenticationScope: { FULL_NAME: 0, EMAIL: 1 },
    AppleAuthenticationButtonType: { SIGN_IN: 0, CONTINUE: 1, SIGN_UP: 2 },
    AppleAuthenticationButtonStyle: { WHITE: 0, WHITE_OUTLINE: 1, BLACK: 2 },
    AppleAuthenticationButton: mockBotaoApple,
  };
});

// expo-crypto é nativo. O digest usa o `crypto` do Node DE VERDADE, pelo nome
// do algoritmo pedido: o teste do nonce mede a relação hash × cru, e um
// algoritmo trocado daria outro hex.
jest.mock("expo-crypto", () => {
  const nodeCrypto = require("crypto");
  return {
    CryptoDigestAlgorithm: { SHA1: "SHA-1", SHA256: "SHA-256", SHA384: "SHA-384", SHA512: "SHA-512" },
    CryptoEncoding: { HEX: "hex", BASE64: "base64" },
    randomUUID: jest.fn(() => nodeCrypto.randomUUID()),
    getRandomBytes: jest.fn((n) => new Uint8Array(nodeCrypto.randomBytes(n))),
    digestStringAsync: jest.fn(async (algoritmo, dado, opcoes) =>
      nodeCrypto.createHash(algoritmo.replace("-", "").toLowerCase()).update(dado).digest(opcoes?.encoding ?? "hex"),
    ),
  };
});

// expo-local-authentication é nativo (LAContext). PADRÃO: aparelho sem código
// (NONE) — a trava fica inerte e nenhum teste antigo vê prompt. Quem testa a
// trava troca o retorno com `jest.mocked(...)`.
jest.mock("expo-local-authentication", () => ({
  SecurityLevel: { NONE: 0, SECRET: 1, BIOMETRIC: 2, BIOMETRIC_WEAK: 2, BIOMETRIC_STRONG: 3 },
  AuthenticationType: { FINGERPRINT: 1, FACIAL_RECOGNITION: 2, IRIS: 3 },
  getEnrolledLevelAsync: jest.fn(() => Promise.resolve(0)),
  supportedAuthenticationTypesAsync: jest.fn(() => Promise.resolve([])),
  authenticateAsync: jest.fn(() => Promise.resolve({ success: true })),
}));

// AppState: o dublê do RN não dispara eventos. Este guarda `currentState` num
// objeto (mesmo padrão do AccessibilityInfo acima: só o CAMPO muda) e dispara
// "change" para quem assinou. Outros eventos (memoryWarning, focus...) são
// aceitos e nunca disparam.
global.__definirAppState = (v) => (mockEstado().app.atual = v);
global.__dispararAppState = (v) => {
  mockEstado().app.atual = v;
  mockEstado().app.ouvintes.forEach((fn) => fn(v));
};
jest.mock("react-native/Libraries/AppState/AppState", () => ({
  __esModule: true,
  default: {
    get currentState() {
      return mockEstado().app.atual;
    },
    isAvailable: true,
    addEventListener: (evento, fn) => {
      if (evento !== "change") return { remove: () => undefined };
      mockEstado().app.ouvintes.add(fn);
      return { remove: () => mockEstado().app.ouvintes.delete(fn) };
    },
  },
}));

// A tampa nativa (`modules/tampa`) não existe no Jest: `requireNativeModule`
// lançaria. Espiãs globais; `bloqueio_tampa.test.tsx` testa o `tampa.ts` real
// com `jest.requireActual`.
jest.mock("@/features/bloqueio/tampa", () => ({
  temTampa: true,
  descobrir: jest.fn(),
  pular: jest.fn(() => Promise.resolve()),
}));

// O slider é nativo (UISlider): no Jest, um View que repassa as props, para o teste
// achar pelo rótulo e disparar `fireEvent(slider, "valueChange", n)`.
// O `require` fica dentro do componente: resolver "react-native" já na fábrica
// carrega o css-interop antes de o estado do setup existir (apontado pelo Codex).
jest.mock("@react-native-community/slider", () => ({
  __esModule: true,
  default: (props) => require("react").createElement(require("react-native").View, props),
}));

// Reanimated: o mock dele importa o worklets real (`loadUnpackers` quebra), então o do worklets vem ANTES.
// `entering`/`exiting` chegam como prop no nó host: é o que os testes de "reduzir movimento" leem.
jest.mock("react-native-worklets", () => require("react-native-worklets/src/mock"));
jest.mock("react-native-reanimated", () => require("react-native-reanimated/mock"));

// `expo/fetch` (stream do chat) é nativo: no Jest ele delega ao `globalThis.fetch`, que cada teste troca por `fetchFalso`.
// Função comum, não `jest.fn`: `jest.restoreAllMocks()` apaga a implementação de um `jest.fn` (Jest 29).
jest.mock("expo/fetch", () => ({ fetch: (...a) => globalThis.fetch(...a) }));
jest.mock("expo-clipboard", () => ({ setStringAsync: jest.fn(async () => true) }));

// +native-intent (#604, allowlist): o `ExpoRoot` do harness entrega o `initialUrl` SEM
// esquema ("/resumo"), forma que o app real nunca recebe (lá o link vem inteiro,
// "pigbank://…"). Com o filtro real, todo atalho `initialUrl` para uma tela seria barrado.
// Aqui só o caminho cru fora de `/open-finance-volta` passa intacto; o link com esquema e
// a volta do Open Finance usam o filtro de verdade. Os testes do filtro (`native_intent_*`)
// usam link com esquema, pelo harness fiel, ou `jest.requireActual`.
jest.mock("./app/+native-intent", () => {
  const real = jest.requireActual("./app/+native-intent");
  return {
    ...real,
    redirectSystemPath: (a) =>
      a.path.startsWith("/") && !a.path.startsWith("/open-finance-volta") ? a.path : real.redirectSystemPath(a),
  };
});

// expo-notifications e expo-application são nativos. Global porque `storage/secure.ts`
// importa a folha do push (`features/push/aparelho.ts`): todo teste que toca o cofre passa por
// aqui. Estado controlável por `__definirPush({ permissao, promptResponde, token, ambiente,
// atrasoDesregistro })`, `__responderNotificacao(resposta)` (o toque) e `__emitirToken(data)` (o token rolou); as espiãs são as do módulo.
// Padrão: permissão NEGADA (nem prompt nem registro: o resto da suíte não vê chamada de push), o
// prompt concede, o simulador (`ambiente: null`).
global.__definirPush = (v) => Object.assign(mockEstado().push, v);
global.__emitirToken = (data) => mockEstado().push.ouvintesToken.forEach((fn) => fn({ type: "ios", data }));
global.__responderNotificacao = (r) => {
  mockEstado().push.resposta = r;
  mockEstado().push.ouvintes.forEach((fn) => fn(r));
};
function mockPermissao() {
  const { permissao } = mockEstado().push;
  return { status: permissao, granted: permissao === "granted", canAskAgain: permissao === "undetermined" };
}
jest.mock("expo-notifications", () => ({
  DEFAULT_ACTION_IDENTIFIER: "expo.modules.notifications.actions.DEFAULT",
  setNotificationHandler: jest.fn(),
  getPermissionsAsync: jest.fn(async () => mockPermissao()),
  requestPermissionsAsync: jest.fn(async () => {
    const p = mockEstado().push;
    if (p.permissao === "undetermined") p.permissao = p.promptResponde;
    return mockPermissao();
  }),
  getDevicePushTokenAsync: jest.fn(async () => ({ type: "ios", data: mockEstado().push.token })),
  unregisterForNotificationsAsync: jest.fn(async () => {
    if (mockEstado().push.atrasoDesregistro) await mockEstado().push.atrasoDesregistro;
  }),
  // Troca de token com o app aberto: `__emitirToken(data)` dispara os ouvintes.
  addPushTokenListener: jest.fn((fn) => {
    mockEstado().push.ouvintesToken.add(fn);
    return { remove: () => mockEstado().push.ouvintesToken.delete(fn) };
  }),
  // Como o real: o `clear` emite o evento e o hook volta a `null`.
  clearLastNotificationResponse: jest.fn(() => {
    mockEstado().push.resposta = null;
    mockEstado().push.ouvintes.forEach((fn) => fn(null));
  }),
  // Como o real: começa `undefined`; no efeito lê a última resposta (ou `null`); resposta nova com o MESMO
  // identificador que a atual é descartada (`determineNextResponse`), e só `clear` zera o hook.
  useLastNotificationResponse: () => {
    const React = require("react");
    const [resposta, definir] = React.useState(undefined);
    React.useLayoutEffect(() => {
      const aplicar = (nova) =>
        definir((antes) => (!nova ? null : !antes || antes.notification.request.identifier !== nova.notification.request.identifier ? nova : antes));
      aplicar(mockEstado().push.resposta);
      mockEstado().push.ouvintes.add(aplicar);
      return () => mockEstado().push.ouvintes.delete(aplicar);
    }, []);
    return resposta;
  },
}));
jest.mock("expo-application", () => ({
  getIosPushNotificationServiceEnvironmentAsync: jest.fn(async () => mockEstado().push.ambiente),
}));
