import { lerRetorno } from "@/features/auth/google";

/**
 * `lerRetorno`: a URL que o `openAuthSessionAsync` devolve. Só
 * `pigbank://auth?<um parâmetro conhecido>` vale; o resto é `invalido` e a
 * tela mostra o aviso genérico (linha 5c da máquina).
 */
describe("lerRetorno", () => {
  it.each([
    ["pigbank://auth?code=abc123", { tipo: "code", valor: "abc123" }],
    ["pigbank://auth?onboarding=gso_x-Y_z", { tipo: "onboarding", valor: "gso_x-Y_z" }],
    ["pigbank://auth?code=a%2Bb", { tipo: "code", valor: "a+b" }],
    ["pigbank://auth?erro=cancelado", { tipo: "erro", valor: "cancelado" }],
    ["pigbank://auth?erro=falha", { tipo: "erro", valor: "falha" }],
    ["pigbank://auth?erro=email_nao_verificado", { tipo: "erro", valor: "email_nao_verificado" }],
    ["pigbank://auth?erro=conta_em_exclusao", { tipo: "erro", valor: "conta_em_exclusao" }],
  ])("%s → reconhecido", (url, esperado) => {
    expect(lerRetorno(url)).toEqual(esperado);
  });

  // Controle negativo (medido): trocar a checagem do prefixo exato por
  // `url.includes("?")` deixa vermelhos os casos de scheme, host e caminho.
  it.each([
    "pigbankai://auth?code=abc", // o app antigo
    "https://pigbank.app/auth?code=abc",
    "pigbank://authx?code=abc",
    "pigbank://auth.evil.com?code=abc",
    "pigbank://evil?code=abc",
    "pigbank://auth/x?code=abc",
    "pigbank:auth?code=abc",
    "PIGBANK://auth?code=abc",
    "pigbank://auth",
    "pigbank://auth?",
    "pigbank://auth?code=",
    "pigbank://auth?=abc",
    "pigbank://auth?code",
    "pigbank://auth?code=a&onboarding=b",
    "pigbank://auth?code=a#b",
    "pigbank://auth?token=abc",
    "pigbank://auth?erro=expirou", // não existe mais: cai no genérico
    "pigbank://auth?erro=toString",
    "pigbank://auth?erro=",
    "pigbank://auth?code=%E0%A4%A", // percent-encoding quebrado
  ])("%s → invalido", (url) => {
    expect(lerRetorno(url)).toEqual({ tipo: "invalido" });
  });
});
