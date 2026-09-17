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

import { act, renderRouter, screen } from "expo-router/testing-library";

import { claro, escuro } from "@/ui/tokens";

declare const global: typeof globalThis & {
  __DEV__: boolean;
  __definirEstadoDaFonte: (v: { carregado?: boolean; erro?: Error | null }) => void;
};

/**
 * `renderRouter("./app", ...)` monta o roteador de VERDADE (o diretório real
 * de `app/app`, via `ExpoRoot`), não uma árvore de teste isolada — é o que
 * prova a guarda do `/_ds` de ponta a ponta, e não só a lógica de `if
 * (!__DEV__)` fora do contexto do roteador.
 */
describe("_layout", () => {
  const devOriginal = global.__DEV__;

  beforeEach(() => {
    // `montar()` da tela de Início dispara fetch ao abrir; sem um dublê aqui
    // ele falha, mas cai no caminho de erro da própria tela — não é o que
    // este arquivo mede, e não deve derrubar o teste.
    global.fetch = jest.fn().mockRejectedValue(new Error("rede indisponível no teste"));
  });

  afterEach(() => {
    global.__DEV__ = devOriginal;
    global.__definirEstadoDaFonte({ carregado: true, erro: null });
    estadoDoEsquema.valor = "light";
  });

  it("em produção (__DEV__=false) o catálogo /_ds não existe: a rota volta para a Início", async () => {
    global.__DEV__ = false;
    renderRouter("./app", { initialUrl: "/_ds" });
    await act(async () => {});
    expect(screen).toHavePathname("/");
  });

  it("em dev (__DEV__=true) /_ds abre o catálogo", async () => {
    global.__DEV__ = true;
    renderRouter("./app", { initialUrl: "/_ds" });
    await act(async () => {});
    expect(screen).toHavePathname("/_ds");
  });

  it("erro ao carregar a fonte não trava o app: a Início ainda renderiza", async () => {
    global.__definirEstadoDaFonte({ carregado: false, erro: new Error("fonte falhou") });
    renderRouter("./app", { initialUrl: "/" });
    await act(async () => {});
    expect(screen).toHavePathname("/");
    // Sem isto, a mutação `if (!fontesCarregadas)` (tirando `&& !erroFontes`)
    // passa verde: `screen.toJSON()` nunca é `null` mesmo com o app travado na
    // View de espera (o `SafeAreaProvider` do `ExpoRoot` garante isso), então
    // só o CONTEÚDO da Início prova que ela montou de verdade.
    expect(screen.getByText("Entre para continuar.")).toBeTruthy();
  });

  it("enquanto a fonte carrega, mostra a View de espera na cor do tema claro — não a tela de baixo, e não null", () => {
    global.__definirEstadoDaFonte({ carregado: false, erro: null });
    renderRouter("./app", { initialUrl: "/" });
    // Sem `await act()`: é o instantâneo ANTES da fonte terminar de carregar
    // que este teste mede. A tela de Início por trás (`ActivityIndicator`,
    // rótulo "Carregando") não pode ter chegado a montar.
    // `ExpoRoot` sempre envolve a árvore num `SafeAreaProvider`, então
    // `screen.toJSON()` nunca é `null` mesmo quando `_layout.tsx` retorna
    // `null` — o discriminador real é o CONTEÚDO dentro dele: a View de
    // espera aparece como filho; `null` deixa `children` vazio.
    const arvore = screen.toJSON() as {
      children: [{ props: { style: { backgroundColor: string } } }] | null;
    } | null;
    expect(arvore?.children).not.toBeNull();
    expect(screen.queryByLabelText("Carregando")).toBeNull();
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
});
