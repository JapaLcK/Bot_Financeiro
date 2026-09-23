/**
 * `SessaoProvider`: boot, `autenticar`/`expirou`/`sair`/`tentarDeNovo`, e o
 * invariante (cofre tem credencial ⇔ provider autenticado). Com os serviços
 * reais (`services/auth.ts`), `fetch` falso e o dublê do cofre.
 *
 * O grupo "SessaoExpirada desloga e RenovacaoIndisponivel não" usa a tela
 * `app/(app)/index.tsx` de verdade — é ela quem decide, ao chamar `perfil()`,
 * se a falha é fim de sessão ou instabilidade passageira.
 */
import { act, fireEvent, render } from "@testing-library/react-native";
import { Pressable } from "react-native";
import { SafeAreaProvider } from "react-native-safe-area-context";

import { SessaoProvider, useSessao, type EstadoSessao } from "@/features/auth/sessao";
import { guardarCredenciais, lerCredenciais } from "@/storage/secure";
import { TemaProvider } from "@/ui/tema";

import { METRICAS_DE_TESTE } from "../ui/_render";
import Inicio from "../../app/(app)/index";
import { falharApagar, falharLeitura, gravador, prepararCaso, resposta, rotear, S, segurar } from "./auth_apoio";

function Harness({ onEstado }: { onEstado: (e: EstadoSessao) => void }) {
  const sessao = useSessao();
  onEstado(sessao.estado);
  return (
    <>
      <Pressable testID="sair" onPress={sessao.sair} />
      <Pressable testID="tentar" onPress={sessao.tentarDeNovo} />
      <Pressable testID="autenticar" onPress={sessao.autenticar} />
    </>
  );
}

function montar(comTelaAutenticada = false) {
  const { aplicados, aplicar } = gravador<EstadoSessao>();
  const utilitarios = render(
    <SafeAreaProvider initialMetrics={METRICAS_DE_TESTE}>
      <TemaProvider esquema="light">
        <SessaoProvider>
          <Harness onEstado={aplicar} />
          {comTelaAutenticada ? <Inicio /> : null}
        </SessaoProvider>
      </TemaProvider>
    </SafeAreaProvider>,
  );
  return { ...utilitarios, aplicados };
}

const respirar = () => new Promise((r) => setTimeout(r, 0));

beforeEach(prepararCaso);

describe("SessaoProvider — boot", () => {
  it("sem sessão no cofre: verificando -> anonimo", async () => {
    rotear();
    const { aplicados } = montar();
    expect(aplicados[0]).toEqual({ fase: "verificando" });
    await act(async () => {});
    expect(aplicados.at(-1)).toEqual({ fase: "anonimo" });
  });

  it("com sessão no cofre: verificando -> autenticado", async () => {
    await guardarCredenciais(S);
    rotear();
    const { aplicados } = montar();
    await act(async () => {});
    expect(aplicados.at(-1)).toEqual({ fase: "autenticado" });
  });

  it("cofre ilegível: verificando -> erro; tentarDeNovo refaz a checagem", async () => {
    falharLeitura(true);
    const { aplicados, getByTestId } = montar();
    await act(async () => {});
    expect(aplicados.at(-1)).toEqual({
      fase: "erro",
      mensagem: "Não conseguimos abrir sua sessão neste aparelho. Tente de novo.",
    });

    falharLeitura(false);
    rotear();
    fireEvent.press(getByTestId("tentar"));
    expect(aplicados.at(-1)).toEqual({ fase: "verificando" });
    await act(async () => {});
    expect(aplicados.at(-1)).toEqual({ fase: "anonimo" });
  });
});

describe("SessaoProvider — ações", () => {
  it("autenticar() vira autenticado", async () => {
    rotear();
    const { aplicados, getByTestId } = montar();
    await act(async () => {});
    fireEvent.press(getByTestId("autenticar"));
    expect(aplicados.at(-1)).toEqual({ fase: "autenticado" });
  });

  it("sair() com sucesso limpa o cofre e vira anonimo — invariante de pé", async () => {
    await guardarCredenciais(S);
    rotear();
    const { aplicados, getByTestId } = montar();
    await act(async () => {});
    fireEvent.press(getByTestId("sair"));
    await act(respirar);
    expect(aplicados.at(-1)).toEqual({ fase: "anonimo" });
    await expect(lerCredenciais()).resolves.toBeNull();
  });

  it("toque duplo em Sair: só UM /auth/logout sai", async () => {
    await guardarCredenciais(S);
    const portao = segurar();
    let chamadasLogout = 0;
    rotear({
      "/auth/logout": async () => {
        chamadasLogout += 1;
        await portao.promessa;
        return resposta(200, {});
      },
    });
    const { getByTestId } = montar();
    await act(async () => {});

    fireEvent.press(getByTestId("sair"));
    fireEvent.press(getByTestId("sair"));
    portao.soltar();
    await act(respirar);

    expect(chamadasLogout).toBe(1);
  });
});

describe("(app)/index.tsx — Sair que falha no cofre expõe erro, sem deslogar em silêncio", () => {
  afterEach(() => falharApagar(false));

  it("CONTROLE POSITIVO — SecureStore recusa apagar: fica autenticado, mostra erro com 'Tentar de novo', e um novo Sair (sem falha) desloga", async () => {
    await guardarCredenciais(S);
    rotear();
    const { getByText } = montar(true);
    await act(respirar);
    expect(getByText("Olá, S")).toBeTruthy();

    falharApagar(true);
    await act(async () => {
      fireEvent.press(getByText("Sair"));
      await respirar();
    });

    // Nada de silêncio: erro visível, e o invariante (cofre com credencial ⇔
    // autenticado) continua de pé — a tela não afirma um logout que não
    // aconteceu.
    expect(getByText("Não conseguimos sair. Tente de novo.")).toBeTruthy();
    expect(getByText("Olá, S")).toBeTruthy();
    await expect(lerCredenciais()).resolves.toEqual(S);

    falharApagar(false);
    await act(async () => {
      fireEvent.press(getByText("Tentar de novo"));
      await respirar();
    });
    await expect(lerCredenciais()).resolves.toBeNull();
  });
});

describe("(app)/index.tsx — Sair disponível durante o carregamento (I-E)", () => {
  it("access vencido + /auth/refresh pendurado (nunca responde): 'Carregando' fica para sempre, mas Sair aparece e desloga", async () => {
    await guardarCredenciais({ access: "vencido", refresh: "rt_x" });
    rotear({
      "/auth/me": () => resposta(401, { detail: "expirado" }),
      // Nunca resolve nem rejeita — o mesmo cenário do achado (refresh
      // pendurado, sem timeout: `renovar()` não usa `comLimite`/sinal).
      "/auth/refresh": () => new Promise<Response>(() => {}),
    });
    const { getByLabelText, getByText, aplicados } = montar(true);
    await act(respirar);
    await act(respirar);

    // Ainda "carregando" — é o próprio cenário do achado.
    expect(getByLabelText("Carregando")).toBeTruthy();

    fireEvent.press(getByText("Sair"));
    await act(respirar);

    expect(aplicados.at(-1)).toEqual({ fase: "anonimo" });
    await expect(lerCredenciais()).resolves.toBeNull();
  });
});

describe("(app)/index.tsx — SessaoExpirada desloga, RenovacaoIndisponivel não (controle)", () => {
  it("SessaoExpirada em /auth/me: a sessão vira anonimo com aviso", async () => {
    await guardarCredenciais(S);
    rotear({
      "/auth/me": () => resposta(401, { detail: "expirado" }),
      "/auth/refresh": () => resposta(401, { detail: "invalid_refresh_token" }),
    });
    const { aplicados } = montar(true);
    await act(respirar);
    await act(respirar);

    expect(aplicados.at(-1)).toEqual({ fase: "anonimo", aviso: "Sua sessão expirou. Entre de novo." });
  });

  it("RenovacaoIndisponivel em /auth/me: a sessão CONTINUA autenticada, e a tela mostra erro local com retry/Sair", async () => {
    await guardarCredenciais(S);
    rotear({
      "/auth/me": () => resposta(401, { detail: "expirado" }),
      "/auth/refresh": () => resposta(503, {}),
    });
    const { aplicados, getByText } = montar(true);
    await act(respirar);
    await act(respirar);

    expect(aplicados.at(-1)).toEqual({ fase: "autenticado" });
    expect(getByText("Não conseguimos falar com o PigBank agora. Tente de novo.")).toBeTruthy();
    expect(getByText("Tentar de novo")).toBeTruthy();
  });
});
