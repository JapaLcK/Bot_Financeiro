import { act, render } from "@testing-library/react-native";

import { SessaoProvider, useSessao } from "@/features/auth/sessao";
import { redirectSystemPath } from "../../app/+native-intent";
import { capturarItemBancario, FalhaNoCofre, guardarCredenciais, guardarCredenciaisSe,
  iniciarTentativaBancaria, lerCredenciais, lerTentativaBancaria, limparCredenciais,
  limparSe, limparSessaoDe, trocarSe } from "@/storage/secure";
import { cofre, prepararCaso, rotear } from "./auth_apoio";
import { conferirRetornoOficial, dependencias, ITEM, JWT_OF, posts, SESSAO_OF, servidor } from "./open_finance_volta_apoio";

const CREDENCIAIS = { access: JWT_OF, refresh: "r1" };
const JWT_B = "eyJhbGciOiJIUzI1NiJ9.eyJqdGkiOiJzZXNzYW8tYiJ9.assinatura";
const apagar = cofre.delete.bind(cofre);
const tem = cofre.has.bind(cofre);

function montar() {
  let atual: ReturnType<typeof useSessao>;
  function Harness() { atual = useSessao(); return null; }
  render(<SessaoProvider><Harness /></SessaoProvider>);
  return { estado: () => atual.estado, sair: () => atual.sair() };
}

beforeEach(async () => {
  prepararCaso();
  rotear();
  await guardarCredenciais(CREDENCIAIS);
});
afterEach(() => jest.restoreAllMocks());

it("credencial recusada preserva tentativa A e callback válido recupera na mesma sessão", async () => {
  const a = (await iniciarTentativaBancaria(1, SESSAO_OF, []))!;
  const bruto = cofre.get("pb.of.tentativa");
  // Sem revogação remota, a mesma sessão continua válida no servidor dublê.
  rotear({ "/auth/logout": () => Promise.reject(new TypeError("offline")) });
  const sessao = montar();
  await act(async () => {});
  const falha = jest.spyOn(cofre, "delete").mockImplementation((chave) => {
    if (chave === "pb.credenciais") throw new Error("keychain recusou credencial");
    return apagar(chave);
  });
  let saiu: boolean | undefined;
  await act(async () => { saiu = await sessao.sair(); });
  expect(saiu).toBe(false);
  expect(sessao.estado()).toEqual({ fase: "autenticado" });
  expect(await lerCredenciais()).toEqual(CREDENCIAIS);
  expect(cofre.get("pb.of.tentativa")).toBe(bruto);
  expect(await lerTentativaBancaria(1)).toEqual(a);
  falha.mockRestore();

  redirectSystemPath({ path: `pigbank://open-finance-volta/${a.tentativa_id}?itemId=${ITEM}`, initial: true });
  expect(await lerTentativaBancaria(1)).toMatchObject({ tentativa_id: a.tentativa_id, item_id: ITEM });
  servidor({});
  const retorno = dependencias();
  await conferirRetornoOficial(ITEM, retorno.d, a.tentativa_id);
  expect(retorno.ultimo()).toMatchObject({ fase: "conectado" });
  expect(posts()).toHaveLength(1);
  expect(await lerTentativaBancaria(1)).toBeNull();
  expect(await lerCredenciais()).toEqual(CREDENCIAIS);
});

it("cleanup recusado desloga; marcador residual não é adotado sem credencial ou por nova JTI", async () => {
  const a = (await iniciarTentativaBancaria(1, SESSAO_OF, []))!;
  const bruto = cofre.get("pb.of.tentativa");
  const sessao = montar();
  await act(async () => {});
  const falha = jest.spyOn(cofre, "delete").mockImplementation((chave) => {
    if (chave === "pb.of.tentativa") throw new Error("keychain recusou marcador");
    return apagar(chave);
  });
  let saiu: boolean | undefined;
  await act(async () => { saiu = await sessao.sair(); });
  expect(saiu).toBe(true);
  expect(sessao.estado()).toEqual({ fase: "anonimo" });
  expect(await lerCredenciais()).toBeNull();
  expect(cofre.get("pb.of.tentativa")).toBe(bruto);
  expect(await lerTentativaBancaria(1)).toBeNull();
  await capturarItemBancario(ITEM, a.tentativa_id);
  expect(cofre.get("pb.of.tentativa")).toBe(bruto);
  falha.mockRestore();

  await guardarCredenciais({ access: JWT_B, refresh: "r-b" });
  expect(await lerTentativaBancaria(1)).toBeNull();
  await capturarItemBancario(ITEM, a.tentativa_id);
  expect(cofre.get("pb.of.tentativa")).toBe(bruto);
  const b = (await iniciarTentativaBancaria(1, "sessao-b", []))!;
  await capturarItemBancario(ITEM, a.tentativa_id);
  expect(await lerTentativaBancaria(1)).toEqual(b);
  await capturarItemBancario(ITEM, b.tentativa_id);
  expect(await lerTentativaBancaria(1)).toMatchObject({ tentativa_id: b.tentativa_id, item_id: ITEM });
});

it("leitura do marcador recusada não impede logout com credenciais apagadas", async () => {
  await iniciarTentativaBancaria(1, SESSAO_OF, []);
  const sessao = montar();
  await act(async () => {});
  jest.spyOn(cofre, "has").mockImplementation((chave) => {
    if (chave === "pb.of.tentativa") throw new Error("keychain recusou leitura do marcador");
    return tem(chave);
  });
  await expect(lerTentativaBancaria(1)).rejects.toThrow("leitura do marcador");
  let saiu: boolean | undefined;
  await act(async () => { saiu = await sessao.sair(); });
  expect(saiu).toBe(true);
  expect(sessao.estado()).toEqual({ fase: "anonimo" });
  expect(await lerCredenciais()).toBeNull();
  expect(cofre.get("pb.of.tentativa")).toBeUndefined();
});

it.each(["limparSessaoDe", "limparSe", "limparCredenciais"])("%s mantém sucesso quando só cleanup falha", async (caller) => {
  await iniciarTentativaBancaria(1, SESSAO_OF, []);
  jest.spyOn(cofre, "delete").mockImplementation((chave) => {
    if (chave === "pb.of.tentativa") throw new Error("cleanup falhou");
    return apagar(chave);
  });
  if (caller === "limparSessaoDe") {
    await trocarSe("r1", { access: JWT_OF, refresh: "r2" });
    await expect(limparSessaoDe(JWT_OF, "r1")).resolves.toBe(true);
  } else if (caller === "limparSe") await expect(limparSe("r1")).resolves.toBe(true);
  else await expect(limparCredenciais()).resolves.toBeUndefined();
  expect(await lerCredenciais()).toBeNull();
  expect(await lerTentativaBancaria(1)).toBeNull();
});

it("limpezas superadas preservam credencial e tentativa da sessão atual", async () => {
  const a = await iniciarTentativaBancaria(1, SESSAO_OF, []);
  const exclusao = jest.spyOn(cofre, "delete");
  await expect(limparSessaoDe(JWT_B, "r-b")).resolves.toBe(false);
  await expect(limparSe("r-b")).resolves.toBe(false);
  expect(exclusao).not.toHaveBeenCalled();
  expect(await lerCredenciais()).toEqual(CREDENCIAIS);
  expect(await lerTentativaBancaria(1)).toEqual(a);
});

it("rollback com sessão anterior restaura credenciais e mantém sua tentativa", async () => {
  const a = await iniciarTentativaBancaria(1, SESSAO_OF, []);
  let vezes = 0;
  await expect(guardarCredenciaisSe(() => ++vezes === 1, { access: JWT_B, refresh: "r-b" })).resolves.toBe(false);
  expect(await lerCredenciais()).toEqual(CREDENCIAIS);
  expect(await lerTentativaBancaria(1)).toEqual(a);
});

it.each([false, true])("rollback sem sessão anterior: falha de credencial=%s preserva contrato", async (falhaCredencial) => {
  const a = await iniciarTentativaBancaria(1, SESSAO_OF, []);
  const bruto = cofre.get("pb.of.tentativa");
  cofre.delete("pb.credenciais");
  jest.spyOn(cofre, "delete").mockImplementation((chave) => {
    if (chave === "pb.of.tentativa" || falhaCredencial) throw new Error("keychain recusou");
    return apagar(chave);
  });
  let vezes = 0;
  const rollback = guardarCredenciaisSe(() => ++vezes === 1, { access: JWT_B, refresh: "r-b" });
  if (falhaCredencial) {
    await expect(rollback).rejects.toBeInstanceOf(FalhaNoCofre);
    expect(await lerCredenciais()).toEqual({ access: JWT_B, refresh: "r-b" });
  } else {
    await expect(rollback).resolves.toBe(false);
    expect(await lerCredenciais()).toBeNull();
  }
  expect(cofre.get("pb.of.tentativa")).toBe(bruto);
  expect(await lerTentativaBancaria(1)).toBeNull();
  // A falha não quebra a fila: restaurar A torna apenas seu marcador legível.
  await guardarCredenciais(CREDENCIAIS);
  expect(await lerTentativaBancaria(1)).toEqual(a);
});
