/**
 * `+native-intent.ts` é uma ALLOWLIST (#604): só passam a raiz, a volta do Open
 * Finance e, em `__DEV__`, os links de desenvolvimento. Aqui o filtro REAL
 * (`jest.requireActual`: o setup do Jest dubla o caminho cru do harness), em string.
 * Quem o roteador abre de verdade está em `native_intent_roteador.test.tsx`.
 */
const { redirectSystemPath } = jest.requireActual("../../app/+native-intent") as typeof import("../../app/+native-intent");

const BLOQUEADOS = [
  // as rotas de entrada e o retorno do Google, em todas as grafias que o roteador normaliza depois
  "pigbank://entrar", "pigbank://criar-conta", "pigbank://esqueci-senha", "pigbank://boas-vindas",
  "pigbank://auth?code=c", "pigbank://auth?onboarding=gso_x", "pigbank-dev://entrar", "pigbank-staging://auth?code=c",
  "pigbank:////entrar", "pigbank:///(auth)//entrar", "pigbank://(auth)/entrar", "PIGBANK://ENTRAR", "pigbank://%2e%2e/entrar",
  "/entrar", "/(auth)//entrar", "/(auth)/(auth)/entrar", "/%61uth?code=a", "/%65ntrar", "/./entrar", "/x/../entrar",
  // B1: `..` desempilha o host no roteador
  "pigbank://entrar/..", "pigbank://entrar/x/../..", "pigbank-dev://criar-conta/..", "pigbank://auth/..?code=c",
  // B2: userinfo/porta
  "pigbank://x@entrar", "pigbank://u:p@criar-conta", "pigbank://entrar:",
  // B3: percent malformado + `..`
  "pigbank:///%ZZ/../entrar", "/%ZZ/../entrar",
  // B4: barra invertida
  "/x/..\\entrar", "pigbank:\\\\entrar", "pigbank:\\entrar", "/\\entrar",
  // B5: indireção do dev client em PRODUÇÃO (aqui __DEV__ é testado separadamente)
  // B6: espaço, NUL, tab no fim
  "pigbank://entrar ", "pigbank://entrar\u0000", "/entrar\t", "pigbank://entrar%00",
  // qualquer outra tela e qualquer outro esquema
  "pigbank://resumo", "pigbank://seguranca", "/resumo", "pigbankai://auth?code=c", "https://pigbankai.com/entrar",
  "pigbank://open-finance-voltax", "pigbank://x/open-finance-volta", "https://open-finance-volta/", "PIGBANK://open-finance-volta",
  "pigbank://", "pigbank:///", "pigbank-dev:///",
  // esquema parecido ou link dentro de outro: a volta do Open Finance exige esquema fechado e âncora `^`
  "pigbank-evil://open-finance-volta", "pigbankx://open-finance-volta", "x?pigbank://open-finance-volta", "https://x/?u=pigbank://open-finance-volta",
  // caminho cru para as rotas de entrada (no app real só chega "/"; o harness antigo é dublado no setup)
  "/criar-conta", "/auth", "/auth?code=a", "/esqueci-senha", "/boas-vindas",
];

describe("allowlist: bloqueado", () => {
  it.each(BLOQUEADOS)("%j: null com o app aberto, '/' na abertura a frio", (path) => {
    expect(redirectSystemPath({ path, initial: false })).toBeNull();
    expect(redirectSystemPath({ path, initial: true })).toBe("/");
  });
});

describe("allowlist: passa", () => {
  it.each(["", "/"])("raiz %j: intacta", (path) => {
    expect(redirectSystemPath({ path, initial: false })).toBe(path);
    expect(redirectSystemPath({ path, initial: true })).toBe(path);
  });

  // O destino é MONTADO pelo filtro, não o texto do link: `/..` não leva a outra tela.
  it.each([
    ["pigbank://open-finance-volta?itemId=abc", "/open-finance-volta?itemId=abc"],
    ["pigbank-dev://open-finance-volta?itemId=abc", "/open-finance-volta?itemId=abc"],
    ["pigbank-staging://open-finance-volta", "/open-finance-volta"],
    ["pigbank://open-finance-volta/../entrar", "/open-finance-volta"],
    ["pigbank://open-finance-volta/x/../../auth?code=c", "/open-finance-volta"],
    ["pigbank://open-finance-volta#/entrar", "/open-finance-volta"],
    ["pigbank://open-finance-volta?x=/../entrar", "/open-finance-volta"],
    ["exp://192.168.0.5:8081/--/open-finance-volta?itemId=abc", "/open-finance-volta?itemId=abc"],
  ])("volta do Open Finance %s → %s", (path, esperado) => {
    expect(redirectSystemPath({ path, initial: true })).toBe(esperado);
  });
});

describe("desenvolvimento só em __DEV__", () => {
  const DEV = [
    "exp://192.168.0.5:8081", "exp://192.168.0.5:8081/--/entrar", "exp+pigbank://entrar",
    "exp+pigbank-dev://expo-development-client/?url=http%3A%2F%2F192.168.0.5%3A8081",
    "pigbank://expo-development-client/?url=pigbank%3A%2F%2Fentrar",
    "pigbank-dev://expo-development-client/?url=http%3A%2F%2F192.168.0.5%3A8081",
  ];
  const dev = (valor: boolean) => ((globalThis as { __DEV__?: boolean }).__DEV__ = valor);
  afterEach(() => dev(true));

  it.each(DEV)("%s: passa intacto em __DEV__", (path) => {
    dev(true);
    expect(redirectSystemPath({ path, initial: true })).toBe(path);
    expect(redirectSystemPath({ path, initial: false })).toBe(path);
  });

  it.each(DEV)("%s: bloqueado em produção", (path) => {
    dev(false);
    expect(redirectSystemPath({ path, initial: false })).toBeNull();
    expect(redirectSystemPath({ path, initial: true })).toBe("/");
  });

  it("exp+pigbankai:// e exps:// não são dev client do app", () => {
    dev(true);
    expect(redirectSystemPath({ path: "exp+pigbankai://entrar", initial: false })).toBeNull();
    expect(redirectSystemPath({ path: "exps://h/--/entrar", initial: false })).toBeNull();
  });
});
