/**
 * Leitor no app: as rotas que escrevem são FECHADAS a ele também por router.push (Stack.Protected e Tabs.Protected), e o layout do
 * Organizar não é gravado na chave do dono. NEGATIVOS medidos: (rotas) `guard={!leitor}` -> `guard` sempre verdadeiro em
 * app/(app)/_layout.tsx, e em (painel)/_layout.tsx para o Piggy; (organizar) tirar o `if (sessaoRef.current.leitor) return` do provider.
 */
import { router } from "expo-router";
import { act, fireEvent, screen, waitFor } from "expo-router/testing-library";

import { cofre, NO_MES_DO_TESTE, prepararCaso } from "./auth_apoio";
import { abrir, escritas } from "./leitor_apoio";
import { desligarTrava, drenar } from "./open_finance_volta_rota_apoio";

jest.setTimeout(30_000);
beforeEach(() => { NO_MES_DO_TESTE(); prepararCaso(); desligarTrava(); });
afterEach(() => jest.restoreAllMocks());

const FECHADAS = ["/configuracoes", "/conexoes", "/seguranca", "/ferramentas", "/agentes", "/perfil", "/notificacoes", "/meta", "/simulador-compra", "/conectar-banco", "/exportar-dados", "/piggy"];
const empurrar = async (rota: string) => { await act(async () => { router.push(rota as never); await drenar(); }); };

describe("rotas fechadas ao leitor", () => {
  it("leitor: router.push de cada rota que escreve não chega nela, e nenhuma escrita sai (nem DELETE /ai/pending do Piggy)", async () => {
    await abrir(true, "/resumo");
    for (const rota of FECHADAS) {
      await empurrar(rota);
      expect(screen).not.toHavePathname(rota);
    }
    expect(escritas()).toEqual([]);
  });

  it("controle positivo: o dono chega a Configurações, Ferramentas e ao Piggy, e o Piggy dispara o DELETE /ai/pending da abertura", async () => {
    await abrir(false, "/resumo");
    await empurrar("/configuracoes");
    expect(screen).toHavePathname("/configuracoes");
    await empurrar("/ferramentas");
    expect(screen).toHavePathname("/ferramentas");
    await empurrar("/piggy");
    await waitFor(() => expect(screen).toHavePathname("/piggy"));
    await waitFor(() => expect(escritas()).toContain("DELETE /ai/pending"));
  });
});

describe("Organizar", () => {
  const organizar = async (leitor: boolean) => {
    await abrir(leitor, "/resumo");
    fireEvent.press(screen.getByRole("button", { name: "Organizar" }));
    await act(async () => { fireEvent.press(await screen.findByRole("button", { name: "Subir Saldo previsto" })); await drenar(); });
  };
  const guardados = () => [...cofre.keys()].filter((k) => k.startsWith("pigbank.painel.v1."));

  it("leitor: reorganizar vale na tela e NÃO grava na chave de layout do dono", async () => {
    await organizar(true);
    expect(guardados()).toEqual([]);
  });

  it("controle positivo: o dono grava o layout", async () => {
    await organizar(false);
    await waitFor(() => expect(guardados()).toHaveLength(1));
  });
});
