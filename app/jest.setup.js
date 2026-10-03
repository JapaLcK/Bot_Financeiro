// SecureStore é nativo: no Jest ele não existe. O dublê guarda em memória, que
// é o suficiente para o que os testes medem (o cliente de API, não o keychain).
// O prefixo `mock` no nome é exigência do Jest: a fábrica do `jest.mock` é
// içada para antes das declarações, e só variáveis assim prefixadas podem ser
// referenciadas dentro dela.
const mockCofre = new Map();
global.__cofreDeTeste = mockCofre;
// A falha é injetada por SINALIZADOR lido dentro do dublê, não por
// reatribuição do método: o módulo sob teste importa o namespace, e trocar a
// propriedade depois do import não chega até ele (medido — o teste passava
// verde sem exercitar nada).
// Dois sinalizadores, e não um: gravar e apagar falham por motivos diferentes,
// e um teste que só consegue quebrar os dois juntos não distingue "o conserto
// limpou" de "nem o conserto conseguiu limpar".
const mockFalha = { escrita: false, apagar: false };
// Um portão na GRAVAÇÃO, para os testes conseguirem parar o tempo dentro da
// fila do cofre e provar o que acontece na janela entre conferir e gravar.
const mockAtraso = { escrita: null };
global.__atrasarEscritaNoCofre = (p) => (mockAtraso.escrita = p);
global.__falharEscritaNoCofre = (v) => (mockFalha.escrita = v);
global.__falharApagarNoCofre = (v) => (mockFalha.apagar = v);
jest.mock("expo-secure-store", () => ({
  getItemAsync: async (k) => (mockCofre.has(k) ? mockCofre.get(k) : null),
  setItemAsync: async (k, v) => {
    if (mockAtraso.escrita) {
      const espera = mockAtraso.escrita;
      mockAtraso.escrita = null;
      await espera;
    }
    if (mockFalha.escrita) throw new Error("keychain recusou");
    mockCofre.set(k, v);
  },
  deleteItemAsync: async (k) => {
    if (mockFalha.apagar) throw new Error("keychain recusou apagar");
    mockCofre.delete(k);
  },
}));

// `Constants.expoConfig` vem do app.config.ts em tempo de build; no Jest ele
// chega vazio. O cliente de API lê `extra.apiUrl` de lá, então sem este dublê
// TODO teste do cliente falha na primeira linha — e por um motivo que não tem
// nada a ver com o que ele mede.
jest.mock("expo-constants", () => ({
  __esModule: true,
  default: { expoConfig: { extra: { apiUrl: "http://backend.teste" }, version: "0.1.0" } },
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
const mockFonte = { carregado: true, erro: null };
global.__definirEstadoDaFonte = (v) => Object.assign(mockFonte, v);
jest.mock("expo-font", () => ({
  useFonts: () => [mockFonte.carregado, mockFonte.erro],
}));

// AccessibilityInfo: `motion.test.ts` prova `useReduzirMovimento` controlando
// a leitura e disparando o evento, sem sistema operacional real por trás.
const mockAcessibilidade = { valor: false, ouvintes: new Set() };
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
    isReduceMotionEnabled: () => Promise.resolve(mockAcessibilidade.valor),
    addEventListener: (_evento, fn) => {
      mockAcessibilidade.ouvintes.add(fn);
      return { remove: () => mockAcessibilidade.ouvintes.delete(fn) };
    },
    // `Toast` (PR C2) anuncia cada mensagem — sem espiã aqui a chamada
    // lançaria "não é uma função" no Jest (o módulo real é nativo).
    announceForAccessibility: jest.fn(),
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
    randomUUID: jest.fn(() => nodeCrypto.randomUUID()),
    digestStringAsync: jest.fn(async (algoritmo, dado) =>
      nodeCrypto.createHash(algoritmo.replace("-", "").toLowerCase()).update(dado).digest("hex"),
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
const mockAppState = { atual: "active", ouvintes: new Set() };
global.__definirAppState = (v) => (mockAppState.atual = v);
global.__dispararAppState = (v) => {
  mockAppState.atual = v;
  mockAppState.ouvintes.forEach((fn) => fn(v));
};
jest.mock("react-native/Libraries/AppState/AppState", () => ({
  __esModule: true,
  default: {
    get currentState() {
      return mockAppState.atual;
    },
    isAvailable: true,
    addEventListener: (evento, fn) => {
      if (evento !== "change") return { remove: () => undefined };
      mockAppState.ouvintes.add(fn);
      return { remove: () => mockAppState.ouvintes.delete(fn) };
    },
  },
}));

// A tampa nativa (`modules/tampa`) não existe no Jest: `requireNativeModule`
// lançaria. Espiãs globais; `bloqueio_tampa.test.tsx` testa o `tampa.ts` real
// com `jest.requireActual`.
jest.mock("@/features/bloqueio/tampa", () => ({
  descobrir: jest.fn(),
  pular: jest.fn(() => Promise.resolve()),
}));
