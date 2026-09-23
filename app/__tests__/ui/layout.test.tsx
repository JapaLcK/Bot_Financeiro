// Objeto mutável, não `jest.fn()` reatribuído por fora: uma `const` do tipo
// `jest.fn()` referenciada de dentro da fábrica do `jest.mock` (hoisted para
// o topo do arquivo, antes de QUALQUER outro código) lê a variável antes de
// ela ser inicializada quando outro import di mesmo arquivo é resolvido antes
// da linha do `const` — medido: o `useColorScheme()` real do `_layout`
// chegava `undefined` e derrubava o render com `renderRouter`. Fechar sobre um
// objeto (o padrão já usado em `jest.setup.js` para fonte/acessibilidade)
// não sofre disso: só o CAMPO muda, a referência ao objeto nunca muda.
const estadoDoEsquema: { valor: "light" | "dark" } = { valor: "light" };
jest.mock("react-native/Libraries/Utilities/useColorScheme", () => ({
  __esModule: true,
  default: () => estadoDoEsquema.valor,
}));

import { act, fireEvent, renderRouter, screen, waitFor } from "expo-router/testing-library";

import { guardarCredenciais, lerCredenciais } from "@/storage/secure";
import { claro, escuro } from "@/ui/tokens";

import { prepararCaso, resposta, rotear, S } from "../features/auth_apoio";

declare const global: typeof globalThis & {
  __DEV__: boolean;
  __definirEstadoDaFonte: (v: { carregado?: boolean; erro?: Error | null }) => void;
};

/**
 * `renderRouter("./app", ...)` monta o roteador de VERDADE (o diretório real
 * de `app/app`, via `ExpoRoot`), não uma árvore de teste isolada — é o que
 * prova a guarda do `/_ds` e do `Stack.Protected` de sessão de ponta a ponta.
 *
 * `waitFor`, não `act(async () => { await setTimeout... })`: a resolução do
 * `Stack.Protected` depois que o boot da sessão muda de fase atravessa mais
 * de um ciclo do scheduler do React Navigation — um único `setTimeout(0)`
 * içado manualmente NUNCA chega a assentar (medido: trava o teste inteiro, e
 * não só ele — os testes seguintes do mesmo arquivo perdem o test renderer).
 * `waitFor` resolve em uma única volta porque repete a checagem, cada uma
 * dentro do próprio `act()` dela.
 */
describe("_layout", () => {
  const devOriginal = global.__DEV__;

  beforeEach(() => {
    prepararCaso();
    rotear();
  });

  afterEach(() => {
    global.__DEV__ = devOriginal;
    global.__definirEstadoDaFonte({ carregado: true, erro: null });
    estadoDoEsquema.valor = "light";
  });

  it("em produção (__DEV__=false), sem sessão: /_ds não existe, vai para /entrar", async () => {
    global.__DEV__ = false;
    renderRouter("./app", { initialUrl: "/_ds" });
    await waitFor(() => expect(screen).toHavePathname("/entrar"));
  });

  it("em dev (__DEV__=true) /_ds abre o catálogo", async () => {
    global.__DEV__ = true;
    renderRouter("./app", { initialUrl: "/_ds" });
    await waitFor(() => expect(screen).toHavePathname("/_ds"));
  });

  it("erro ao carregar a fonte não trava o app: a tela de Entrar ainda renderiza", async () => {
    global.__definirEstadoDaFonte({ carregado: false, erro: new Error("fonte falhou") });
    renderRouter("./app", { initialUrl: "/" });
    await waitFor(() => expect(screen).toHavePathname("/entrar"));
    // Sem isto, a mutação `if (!fontesCarregadas)` (tirando `&& !erroFontes`)
    // passa verde: `screen.toJSON()` nunca é `null` mesmo com o app travado na
    // View de espera (o `SafeAreaProvider` do `ExpoRoot` garante isso), então
    // só o CONTEÚDO da tela de Entrar prova que ela montou de verdade.
    expect(screen.getByText("E-mail")).toBeTruthy();
  });

  it("enquanto a fonte carrega, mostra a View de espera na cor do tema claro — não a tela de baixo, e não null", () => {
    global.__definirEstadoDaFonte({ carregado: false, erro: null });
    renderRouter("./app", { initialUrl: "/" });
    // Sem `await`: é o instantâneo ANTES da fonte terminar de carregar que
    // este teste mede — nem a fonte, nem o boot da sessão podem ter chegado
    // a resolver.
    // `ExpoRoot` sempre envolve a árvore num `SafeAreaProvider`, então
    // `screen.toJSON()` nunca é `null` mesmo quando `_layout.tsx` retorna
    // `null` — o discriminador real é o CONTEÚDO dentro dele: a View de
    // espera aparece como filho; `null` deixa `children` vazio.
    const arvore = screen.toJSON() as {
      children: [{ props: { style: { backgroundColor: string } } }] | null;
    } | null;
    expect(arvore?.children).not.toBeNull();
    expect(arvore?.children?.[0].props.style.backgroundColor).toBe(claro.bg);
  });

  it("enquanto a fonte carrega no tema escuro, a View de espera usa o fundo escuro", () => {
    estadoDoEsquema.valor = "dark";
    global.__definirEstadoDaFonte({ carregado: false, erro: null });
    renderRouter("./app", { initialUrl: "/" });
    const arvore = screen.toJSON() as {
      children: [{ props: { style: { backgroundColor: string } } }] | null;
    } | null;
    expect(arvore?.children?.[0].props.style.backgroundColor).toBe(escuro.bg);
  });

  describe("sessão decide a rota", () => {
    it("cold start sem sessão no cofre: vai para /entrar", async () => {
      renderRouter("./app", { initialUrl: "/" });
      await waitFor(() => expect(screen).toHavePathname("/entrar"));
    });

    it("cold start com sessão no cofre: fica em /", async () => {
      await guardarCredenciais(S);
      renderRouter("./app", { initialUrl: "/" });
      // Pathname troca assim que a sessão vira "autenticado" — ANTES de
      // `(app)/index.tsx` terminar de buscar `/auth/me`. `waitFor` no texto
      // final cobre as duas esperas com uma polling só.
      await waitFor(() => expect(screen.getByText("Olá, S")).toBeTruthy());
      expect(screen).toHavePathname("/");
    });

    it("login bem-sucedido troca o pathname de /entrar para /", async () => {
      renderRouter("./app", { initialUrl: "/entrar" });
      await waitFor(() => expect(screen).toHavePathname("/entrar"));

      fireEvent.changeText(screen.getByLabelText("E-mail"), "ana@x.com");
      fireEvent.changeText(screen.getByLabelText("Senha"), "s3nha");
      await act(async () => fireEvent.press(screen.getByRole("button", { name: "Entrar" })));

      await waitFor(() => expect(screen.getByText("Olá, Ana")).toBeTruthy());
      expect(screen).toHavePathname("/");
    });

    it("/auth/me terminal (401 + refresh 401) manda para /entrar com o aviso da sessão expirada", async () => {
      await guardarCredenciais(S);
      rotear({
        "/auth/me": () => resposta(401, { detail: "expirado" }),
        "/auth/refresh": () => resposta(401, { detail: "invalid_refresh_token" }),
      });
      renderRouter("./app", { initialUrl: "/" });
      await waitFor(() => expect(screen).toHavePathname("/entrar"));
      expect(screen.getByText("Sua sessão expirou. Entre de novo.")).toBeTruthy();
    });

    it("Sair leva de volta a /entrar com o cofre vazio", async () => {
      await guardarCredenciais(S);
      renderRouter("./app", { initialUrl: "/" });
      await waitFor(() => expect(screen.getByText("Olá, S")).toBeTruthy());

      await act(async () => fireEvent.press(screen.getByRole("button", { name: "Sair" })));

      await waitFor(() => expect(screen).toHavePathname("/entrar"));
      await expect(lerCredenciais()).resolves.toBeNull();
    });
  });
});
