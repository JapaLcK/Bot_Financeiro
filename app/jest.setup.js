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
const mockFalha = { escrita: false };
global.__falharEscritaNoCofre = (v) => (mockFalha.escrita = v);
jest.mock("expo-secure-store", () => ({
  getItemAsync: async (k) => (mockCofre.has(k) ? mockCofre.get(k) : null),
  setItemAsync: async (k, v) => {
    if (mockFalha.escrita) throw new Error("keychain recusou");
    mockCofre.set(k, v);
  },
  deleteItemAsync: async (k) => void mockCofre.delete(k),
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
