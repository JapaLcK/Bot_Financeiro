import { digitosParaCentavos, falado, partes, TETO_CENTAVOS } from "@/ui/dinheiro";

describe("partes", () => {
  it("formata com milhar e dois dígitos de centavo", () => {
    expect(partes(123456789)).toEqual({ negativo: false, inteiro: "1.234.567", centavos: "89" });
  });

  it("centavo sozinho ganha zero à esquerda", () => {
    expect(partes(5)).toEqual({ negativo: false, inteiro: "0", centavos: "05" });
  });

  it("zero e -0 não são negativos", () => {
    expect(partes(0)?.negativo).toBe(false);
    expect(partes(-0)?.negativo).toBe(false);
  });

  it("negativo separa sinal do módulo", () => {
    expect(partes(-1230)).toEqual({ negativo: true, inteiro: "12", centavos: "30" });
  });

  it.each([NaN, Infinity, -Infinity, 1.5, 2 ** 53, 1e21])("%p não é inteiro seguro → null", (v) => {
    expect(partes(v)).toBeNull();
  });

  it("o maior inteiro seguro ainda formata", () => {
    expect(partes(Number.MAX_SAFE_INTEGER)).not.toBeNull();
  });
});

describe("digitosParaCentavos — digitação", () => {
  it.each([
    ["0,001", 1],
    ["0,012", 12],
    ["0,123", 123],
    ["1,2", 12], // resultado de apagar um dígito de "1,23"
    ["0,0", 0],
    ["", 0],
  ])("%s → %d", (entrada, esperado) => {
    expect(digitosParaCentavos(entrada)).toBe(esperado);
  });
});

describe("digitosParaCentavos — colar (decisão 1: dígito a dígito)", () => {
  it.each([
    ["R$ 1.234,56", 123456],
    ["1234.56", 123456],
    ["12,5", 125],
    ["50", 50],
    ["-50", 50],
    ["−50", 50], // U+2212
    ["0000000000001", 1],
  ])("%s → %d", (entrada, esperado) => {
    expect(digitosParaCentavos(entrada)).toBe(esperado);
  });
});

describe("digitosParaCentavos — teto e lista branca", () => {
  it("no teto exato aceita", () => {
    expect(digitosParaCentavos("9999999999")).toBe(TETO_CENTAVOS);
  });

  it("um dígito a mais que o teto rejeita", () => {
    expect(digitosParaCentavos("10000000000")).toBeNull();
    expect(digitosParaCentavos("99999999999")).toBeNull();
  });

  it.each(["١٢٣", "１２３", "abc", "1e21", "12​3"])("%s (fora da lista branca) → null", (entrada) => {
    expect(digitosParaCentavos(entrada)).toBeNull();
  });

  it("string absurdamente longa não trava e é rejeitada", () => {
    const inicio = Date.now();
    expect(digitosParaCentavos("9".repeat(100_000))).toBeNull();
    expect(Date.now() - inicio).toBeLessThan(1000);
  });
});

describe("digitosParaCentavos — lista branca de espaço (achado 4 / M1)", () => {
  it.each([
    ["1234,56\r\n", 123456], // \r antes do \n, como cola texto do Windows
    ["1 234,56", 123456], // NBSP como separador de milhar (M1: já existia, sem teste que exercitasse dentro de um valor inteiro)
    ["1 234,56", 123456], // narrow no-break space (M1, mesmo motivo)
    ["1 234,56", 123456], // thin space
    ["1 234,56", 123456], // figure space
    ["R$ 1.234,56", 123456], // NBSP depois do "R$", como o copiar/colar real produz
  ])("%s → %d", (entrada, esperado) => {
    expect(digitosParaCentavos(entrada)).toBe(esperado);
  });
});

describe("digitosParaCentavos — lista branca não é \\s", () => {
  it.each([
    ["1﻿23", "zero-width no-break space (BOM)"],
    ["1　23", "ideographic space"],
  ])("%p (%s) → null", (entrada, _motivo) => {
    expect(digitosParaCentavos(entrada)).toBeNull();
  });
});

describe("falado", () => {
  it.each([
    [1, "1 centavo"],
    [100, "1 real"],
    [0, "zero reais"],
    [30, "30 centavos"],
    [1200, "12 reais"],
    [123456789, "1234567 reais e 89 centavos"],
  ])("falado(%d) = %s", (centavos, esperado) => {
    expect(falado(centavos)).toBe(esperado);
  });

  it("mesma regra de invalidez de partes", () => {
    expect(falado(NaN)).toBeNull();
  });
});
