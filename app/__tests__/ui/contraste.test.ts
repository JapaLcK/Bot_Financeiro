import { contraste, PARES } from "@/ui/contraste";
import { claro, escuro } from "@/ui/tokens";

describe("contraste", () => {
  // Referência do plano: prova a fórmula contra dois valores medidos à mão,
  // um dos dois lados do teto AA (4,5) — não só o "deu maior que", mas o
  // "deu maior que bem perto do limite" e o "deu menor por pouco".
  it("#767676 sobre branco alcança AA (4,54)", () => {
    expect(contraste("#767676", "#FFFFFF")).toBeCloseTo(4.54, 1);
  });

  it("#777777 sobre branco reprova AA por pouco (4,48)", () => {
    const razao = contraste("#777777", "#FFFFFF");
    expect(razao).toBeCloseTo(4.48, 1);
    expect(razao).toBeLessThan(4.5);
  });

  it("é simétrico: a ordem dos dois tons não muda o resultado", () => {
    expect(contraste("#111113", "#FFFFFF")).toBeCloseTo(contraste("#FFFFFF", "#111113"), 10);
  });

  it.each([
    ["claro", claro],
    ["escuro", escuro],
  ])("todo par da tabela PARES alcança o mínimo no tema %s", (_nome, paleta) => {
    for (const par of PARES) {
      const razao = contraste(paleta[par.primeiro], paleta[par.segundo]);
      expect(razao).toBeGreaterThanOrEqual(par.minimo);
    }
  });

  // Par PROIBIDO do plano: brand sobre brandSoft dá 2,96 — abaixo até do
  // teto de não-texto (3). Documentado aqui para nunca entrar em PARES.
  it("brand sobre brandSoft reprova até o teto de não-texto (par proibido)", () => {
    expect(contraste(claro.brand, claro.brandSoft)).toBeLessThan(3);
  });

  // `contraste()` só sabe ler hex de 6 dígitos (comentário do próprio
  // arquivo): um `#EEE` ou um `rgb(...)` não dão erro, dão CONTA ERRADA em
  // silêncio (`Number.parseInt` de um hex de 3 dígitos lê dígitos que não
  // existem). Sem este teste, um token assim passaria os testes de PARES
  // acima com um número que não é o contraste real.
  it.each([
    ["claro", claro],
    ["escuro", escuro],
  ])("todo valor da paleta %s é hex de 6 dígitos", (_nome, paleta) => {
    for (const valor of Object.values(paleta)) {
      expect(valor).toMatch(/^#[0-9A-F]{6}$/i);
    }
  });
});
