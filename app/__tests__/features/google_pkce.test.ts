/**
 * PKCE do Google (#604): o app manda `code_challenge` no start e o
 * `code_verifier` na troca e no `complete-signup`. O backend falso confere
 * S256(verifier) == challenge com o `crypto` do Node (o oráculo NÃO é o código
 * testado): sem verifier, ou com o de outra tentativa, recusa como o real.
 */
import { createHash } from "crypto";

import { tocar, type EstadoEntrar } from "@/features/auth/entrar";
import { criarContaSocial } from "@/features/auth/cadastroSocial";
import { continuarComGoogle, desafioDe } from "@/features/auth/google";
import { lerCredenciais } from "@/storage/secure";

import { GENERICO, chamadas, cofre, credencialDe, gravador, prepararCaso, resposta } from "./auth_apoio";
import { CODIGO_INVALIDO, PENDENTE, abrirFolha, rotasGoogle, voltaDoGoogle } from "./google_apoio";

const s256 = (v: string) => createHash("sha256").update(v).digest("base64url");
const desafios = () => abrirFolha.mock.calls.map(([url]) => /code_challenge=([^&]*)$/.exec(url)?.[1]);

/** Backend falso com PKCE: o código só troca se o verifier da troca casar com o challenge do start. */
function backendComPkce() {
  const confere = (o: RequestInit) => {
    const v = (JSON.parse(String(o.body)) as { code_verifier?: string }).code_verifier;
    return v !== undefined && s256(v) === desafios().at(-1);
  };
  rotasGoogle({
    "/auth/google/exchange": (o) => (confere(o) ? resposta(200, credencialDe("ana@x.com")) : resposta(400, CODIGO_INVALIDO)),
    "/auth/google/complete-signup": (o) =>
      confere(o) ? resposta(200, credencialDe("bia@gmail.com")) : resposta(400, { detail: "Cadastro expirado. Inicie novamente o login com Google." }),
  });
}

beforeEach(() => {
  prepararCaso();
  rotasGoogle();
});

async function google() {
  const { aplicados, aplicar } = gravador<EstadoEntrar>();
  await tocar(() => continuarComGoogle(jest.fn()), aplicar);
  return aplicados;
}
const verificadorDaTroca = () =>
  chamadas().filter((c) => c.caminho === "/auth/google/exchange").map((c) => (c.corpo as { code_verifier?: string }).code_verifier);

describe("desafioDe", () => {
  it("vetor do RFC 7636, apêndice B", async () => {
    expect(await desafioDe("dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk")).toBe("E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM");
  });
  it("base64url sem padding, 43 caracteres, e sem '+' nem '/' (SHA-256 cujo base64 tem os dois)", async () => {
    // Procura por força bruta um verifier cujo base64 padrão tenha '+' e '/', para provar a troca de alfabeto.
    let v = "";
    for (let i = 0; i < 5000; i++) {
      v = `v${i}`.padEnd(43, "x");
      const b64 = createHash("sha256").update(v).digest("base64");
      if (b64.includes("+") && b64.includes("/")) break;
    }
    const d = await desafioDe(v);
    expect(d).toMatch(/^[A-Za-z0-9_-]{43}$/);
    expect(d).toBe(s256(v));
  });
});

describe("continuarComGoogle com PKCE", () => {
  it("o start leva o challenge, a troca leva o verifier que o gera, e a sessão é gravada", async () => {
    backendComPkce();
    voltaDoGoogle("pigbank://auth?code=code-ana");

    expect(await google()).toEqual([{ fase: "google" }]);

    const [desafio] = desafios();
    const [verificador] = verificadorDaTroca();
    expect(desafio).toMatch(/^[A-Za-z0-9_-]{43}$/);
    expect(verificador).toMatch(/^[A-Za-z0-9\-._~]{43,128}$/); // RFC 7636 §4.1, o mesmo padrão do backend
    expect(s256(verificador!)).toBe(desafio);
    await expect(lerCredenciais()).resolves.toEqual({ access: "access-ana", refresh: "rt_ana" });
  });

  it("o verifier vive só em memória: nem o cofre, nem a URL do start o carregam", async () => {
    backendComPkce();
    voltaDoGoogle("pigbank://auth?code=code-ana");
    await google();
    const [verificador] = verificadorDaTroca();
    expect(JSON.stringify([...cofre.entries()])).not.toContain(verificador);
    expect(abrirFolha.mock.calls[0]![0]).not.toContain(verificador);
  });

  it("segunda tentativa usa verifier e challenge novos", async () => {
    backendComPkce();
    voltaDoGoogle("pigbank://auth?code=code-ana");
    await google();
    await google();
    const [d1, d2] = desafios();
    const [v1, v2] = verificadorDaTroca();
    expect(d1).not.toBe(d2);
    expect(v1).not.toBe(v2);
    expect(s256(v2!)).toBe(d2);
  });

  it("cancelar a folha descarta a tentativa: nenhuma troca, e a seguinte não reaproveita o challenge", async () => {
    backendComPkce();
    voltaDoGoogle({ type: "cancel" });
    expect(await google()).toEqual([{ fase: "formulario" }]);
    expect(chamadas()).toEqual([]);
    const cancelada = desafios()[0]; // `voltaDoGoogle` zera o histórico do mock
    expect(cancelada).toBeDefined();

    voltaDoGoogle("pigbank://auth?code=code-ana");
    await google();
    const seguinte = desafios()[0];
    expect(seguinte).not.toBe(cancelada);
    expect(s256(verificadorDaTroca()[0]!)).toBe(seguinte);
  });

  it("422 (corpo recusado, ex.: verifier fora do RFC) NÃO é 'código inválido': genérico, sem o detalhe cru", async () => {
    rotasGoogle({ "/auth/google/exchange": () => resposta(422, { detail: [{ loc: ["body", "code_verifier"], msg: "String should match pattern" }] }) });
    voltaDoGoogle("pigbank://auth?code=code-ana");
    expect(await google()).toEqual([{ fase: "formulario", aviso: GENERICO }]);
    await expect(lerCredenciais()).resolves.toBeNull();
  });
});

describe("cadastro social carrega o verifier até o complete-signup", () => {
  it("Google: o verifier do start atravessa cadastro-social e chega ao complete-signup", async () => {
    backendComPkce();
    voltaDoGoogle("pigbank://auth?onboarding=gso_bia");
    const [c] = await google();
    expect(c).toMatchObject({ fase: "cadastro-social", provedor: "google", token: "gso_bia", email: PENDENTE.email });

    const { aplicados, aplicar } = gravador<EstadoEntrar>();
    const { fase: _fase, ...dados } = c as Extract<EstadoEntrar, { fase: "cadastro-social" }>;
    await tocar(() => criarContaSocial(dados, "(11) 99999-8888", jest.fn()), aplicar);

    expect(aplicados).toEqual([{ fase: "criando-social", provedor: "google", token: "gso_bia", email: PENDENTE.email }]);
    const corpo = chamadas().find((x) => x.caminho === "/auth/google/complete-signup")!.corpo as { code_verifier: string };
    expect(s256(corpo.code_verifier)).toBe(desafios()[0]);
    await expect(lerCredenciais()).resolves.toEqual({ access: "access-bia", refresh: "rt_bia" });
  });

  it("verifier perdido/de outra tentativa: 'Cadastro expirado' volta ao formulário com o aviso de recomeçar", async () => {
    backendComPkce();
    voltaDoGoogle({ type: "cancel" });
    await google(); // só para haver um challenge registrado
    const estado = { provedor: "google" as const, token: "gso_bia", email: PENDENTE.email, nome: "Bia", verificador: "a".repeat(64) };
    const { aplicados, aplicar } = gravador<EstadoEntrar>();
    await tocar(() => criarContaSocial(estado, "(11) 99999-8888", jest.fn()), aplicar);
    expect(aplicados.at(-1)).toEqual({ fase: "formulario", aviso: "Cadastro expirado. Inicie novamente o login com Google." });
    await expect(lerCredenciais()).resolves.toBeNull();
  });

  it("falha que mantém em C (ex.: telefone) preserva o verifier para a nova tentativa", async () => {
    rotasGoogle({ "/auth/google/complete-signup": () => resposta(400, { detail: "Informe um número de WhatsApp válido com DDD." }) });
    const estado = { provedor: "google" as const, token: "gso_bia", email: PENDENTE.email, nome: "Bia", verificador: "b".repeat(64) };
    const { aplicados, aplicar } = gravador<EstadoEntrar>();
    await tocar(() => criarContaSocial(estado, "(11) 99999-8888", jest.fn()), aplicar);
    expect(aplicados.at(-1)).toMatchObject({ fase: "cadastro-social", verificador: "b".repeat(64) });
  });

  it("Apple não tem verifier: o corpo do complete-signup não leva code_verifier", async () => {
    rotasGoogle({ "/auth/apple/complete-signup": () => resposta(200, credencialDe("leo@x.com")) });
    const { aplicar } = gravador<EstadoEntrar>();
    await tocar(() => criarContaSocial({ provedor: "apple", token: "gso_leo", email: "l@x.com", nome: "Leo" }, "(11) 99999-8888", jest.fn()), aplicar);
    expect(Object.keys(chamadas()[0]!.corpo as object)).not.toContain("code_verifier");
  });
});
