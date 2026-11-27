/**
 * Leitor: duas guardas que se medem sem montar o app. (1) "Atualizar agora" (POST) do aviso de banco velho some, e fica só o texto.
 * (2) O acesso bancário do leitor não passa pelo onboarding do Open Finance. NEGATIVOS medidos: (1) tirar o ramo `useLeitor()` de
 * corpoAvisoBanco.tsx; (2) tirar o `if (p.somente_leitura) return` de acesso.ts.
 */
import { render, screen } from "@testing-library/react-native";

import { carregarAcessoBancario } from "@/features/openFinance/acesso";
import { CorpoAvisoBanco } from "@/features/painel/corpoAvisoBanco";
import { TemaProvider } from "@/ui/tema";
import { guardarCredenciais } from "@/storage/secure";

import { chamadas, fetchFalso, prepararCaso, resposta } from "./auth_apoio";

const estado = { leitor: false };
jest.mock("@/features/auth/sessao", () => ({ useLeitor: () => estado.leitor }));
jest.mock("@/features/painel/usarAtualizarBanco", () => ({ useAtualizarBanco: () => ({ acao: { tipo: "atualizar" }, maquina: { fase: "ocioso" }, atualizar: jest.fn() }) }));
jest.mock("@/features/painel/navegacao", () => ({ useNavegacaoPainel: () => ({ navegar: jest.fn(), entrando: false }) }));

beforeEach(() => prepararCaso());

describe("aviso de banco desatualizado", () => {
  const montar = (leitor: boolean) => { estado.leitor = leitor; render(<TemaProvider esquema="light"><CorpoAvisoBanco bancos={["Nubank"]} /></TemaProvider>); };

  it("leitor: só o texto; sem 'Atualizar agora' nem outro botão", () => {
    montar(true);
    expect(screen.getByText(/Os dados de Nubank estão desatualizados/)).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Atualizar agora" })).toBeNull();
    expect(screen.queryAllByRole("button")).toHaveLength(0);
  });

  it("controle positivo: o dono vê 'Atualizar agora'", () => {
    montar(false);
    expect(screen.getByRole("button", { name: "Atualizar agora" })).toBeTruthy();
  });
});

describe("acesso bancário", () => {
  const abrir = async (leitor: boolean) => {
    await guardarCredenciais({ access: "access-s", refresh: "rt_s" });
    fetchFalso.mockImplementation(async (url: string) => {
      const path = new URL(url).pathname;
      if (path === "/auth/me") return resposta(200, { user_id: 1, app_access: true, ...(leitor ? { somente_leitura: true } : {}) });
      if (path === "/onboarding/open-finance") return resposta(200, { ok: true, completed: false, completed_at: null });
      return resposta(200, { ok: true, of_banks_max: 3, em_uso: 0, pode_adicionar: true, code: null, message: null });
    });
    return carregarAcessoBancario();
  };

  it("leitor: vai direto ao início e o onboarding nem é consultado, mesmo incompleto", async () => {
    expect((await abrir(true)).fase).toBe("inicio");
    expect(chamadas().some((c) => c.caminho === "/onboarding/open-finance")).toBe(false);
  });

  it("controle positivo: o dono com onboarding incompleto consulta o marco e cai em 'conectar'", async () => {
    expect((await abrir(false)).fase).toBe("conectar");
    expect(chamadas().some((c) => c.caminho === "/onboarding/open-finance")).toBe(true);
  });
});
