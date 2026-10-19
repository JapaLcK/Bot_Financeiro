/** `sair(capturada)` (exclusão de conta) em voo não engole o Sair de OUTRA conta que entrou nesse meio. */
import { act, render } from "@testing-library/react-native";

import { SessaoProvider, useSessao, type EstadoSessao } from "@/features/auth/sessao";
import { guardarCredenciais, lerCredenciais } from "@/storage/secure";

import { gravador, prepararCaso, resposta, respirar, rotear, S, segurar } from "./auth_apoio";

const B = { access: "access-b", refresh: "rt_b" };
let sessao: ReturnType<typeof useSessao>;
function Captura({ onEstado }: { onEstado: (e: EstadoSessao) => void }) {
  sessao = useSessao();
  onEstado(sessao.estado);
  return null;
}

beforeEach(prepararCaso);

it("sair(capturadaA) pendurado, B entra e toca Sair: o Sair de B sai de verdade; soltar o logout de A não quebra nada", async () => {
  await guardarCredenciais(S);
  const logoutA = segurar();
  const logouts: string[] = [];
  rotear({
    "/auth/logout": async (o) => {
      const auth = (o.headers as Record<string, string>)["Authorization"];
      logouts.push(auth);
      if (auth === "Bearer rt_s") await logoutA.promessa;
      return resposta(200, {});
    },
  });
  const { aplicados, aplicar } = gravador<EstadoSessao>();
  render(<SessaoProvider><Captura onEstado={aplicar} /></SessaoProvider>);
  await act(async () => {});

  let saidaA: Promise<unknown> = Promise.resolve();
  await act(async () => { saidaA = sessao.sair(S); await respirar(); });

  await act(async () => { await guardarCredenciais(B); sessao.autenticar(); });
  let rB: unknown;
  await act(async () => { rB = await sessao.sair(); });

  expect(rB).toBe(true);
  expect(logouts).toContain("Bearer rt_b");
  expect(aplicados.at(-1)).toEqual({ fase: "anonimo" });
  await expect(lerCredenciais()).resolves.toBeNull();

  await act(async () => { logoutA.soltar(); await saidaA; });
  expect(aplicados.at(-1)).toEqual({ fase: "anonimo" });
});
