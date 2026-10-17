import { contraste, PARES } from "@/ui/contraste";
import { ALFA_FUNDO_TOM, ALFA_WASH_SERIE, claro, escuro, acessoClaro, acessoEscuro, SERIE_META, tingir } from "@/ui/tokens";

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
    ["acesso claro", acessoClaro],
    ["acesso escuro", acessoEscuro],
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
    ["acesso claro", acessoClaro],
    ["acesso escuro", acessoEscuro],
  ])("todo valor da paleta %s é hex de 6 dígitos", (_nome, paleta) => {
    for (const valor of Object.values(paleta)) {
      expect(valor).toMatch(/^#[0-9A-F]{6}$/i);
    }
  });

  // O painel e as telas de acesso usam o MESMO rosa de botão primário em cada tema.
  it.each([
    ["claro", claro, acessoClaro],
    ["escuro", escuro, acessoEscuro],
  ])("acesso %s não sobrescreve acao/onAcao", (_nome, base, acesso) => {
    expect(acesso.acao).toBe(base.acao);
    expect(acesso.onAcao).toBe(base.onAcao);
  });

  // Chip do "ritmo de gastos" (`resumoMes.tsx`): texto no tom cheio sobre o MESMO tom em ALFA_FUNDO_TOM por cima do fundo do card.
  // `contraste()` só lê hex sólido, então o fundo composto é calculado aqui (mesmo alfa de 8 bits que o componente grava).
  // Só os temas do painel (claro/escuro): os de acesso são das telas de entrada e não têm chip.
  const sobre = (tom: string, fundo: string, alfa: number) => {
    const a = Math.round(alfa * 255) / 255, c = (h: string, i: number) => Number.parseInt(h.slice(i, i + 2), 16);
    return `#${[1, 3, 5].map((i) => Math.round(c(tom, i) * a + c(fundo, i) * (1 - a)).toString(16).padStart(2, "0")).join("")}`;
  };
  it.each([
    ["claro", claro, ALFA_FUNDO_TOM.light],
    ["escuro", escuro, ALFA_FUNDO_TOM.dark],
  ])("chip abaixo/acima (positive/warning sobre o próprio tom) alcança AA no tema %s, sobre o card e sobre o fundo", (_nome, paleta, alfa) => {
    for (const tom of ["positive", "warning"] as const) {
      for (const fundo of ["surface", "bg"] as const) {
        expect(contraste(paleta[tom], sobre(paleta[tom], paleta[fundo], alfa))).toBeGreaterThanOrEqual(4.5);
      }
    }
  });
  // Pilha de avisos (`pilhaAvisos.tsx`): fundo = a cor da borda (`fio`) em ALFA_FUNDO_TOM; ícone e pontos em `tinta` e a borda em `fio` (não-texto, mínimo 3:1); título e corpo em ink/inkMuted (texto, 4,5:1).
  // O aviso fica direto sobre o fundo da tela (`bg`), nunca dentro de um card: a borda rosa sobre o tingido de `surface` dá 2,99 no claro (medido em 2026-10-17), abaixo dos 3.
  it.each([
    ["claro", claro, ALFA_FUNDO_TOM.light],
    ["escuro", escuro, ALFA_FUNDO_TOM.dark],
  ])("aviso da pilha (decisão rosa e alerta âmbar) alcança o mínimo no tema %s, sobre o fundo da tela", (_nome, paleta, alfa) => {
    for (const [fio, tinta] of [["brand", "brandInk"], ["warning", "warning"]] as const) {
      const tingido = sobre(paleta[fio], paleta.bg, alfa);
      expect(contraste(paleta[tinta], tingido)).toBeGreaterThanOrEqual(3);
      expect(contraste(paleta[fio], tingido)).toBeGreaterThanOrEqual(3);
      expect(contraste(paleta.ink, tingido)).toBeGreaterThanOrEqual(4.5);
      expect(contraste(paleta.inkMuted, tingido)).toBeGreaterThanOrEqual(4.5);
    }
  });
  it("tingir grava o mesmo alfa de 8 bits que o teste compõe (06 no claro, 24 no escuro)", () => {
    expect(tingir(claro.warning, "light")).toBe(`${claro.warning}0f`); expect(tingir(escuro.warning, "dark")).toBe(`${escuro.warning}24`);
  });
  it("controle: 12% no claro reprovava (é por isso que o alfa do claro é menor)", () => {
    expect(contraste(claro.warning, sobre(claro.warning, claro.surface, 0.12))).toBeLessThan(4.5);
  });

  // Cor de série das pílulas de meta: barra e ícone (cor do dado, não texto) pedem 3:1 contra a pílula (`surfaceControl`), a trilha (`border`) e o próprio wash.
  // O claro não tem par na web (as da web dão 1,5 a 2,3 ali): preso por propriedade — matiz perto do escuro e longe dos estados, para a série nunca virar sinal de estado.
  const matiz = (h: string) => {
    const [r, g, b] = [1, 3, 5].map((i) => Number.parseInt(h.slice(i, i + 2), 16) / 255) as [number, number, number], mx = Math.max(r, g, b), d = mx - Math.min(r, g, b);
    const m = (mx === r ? ((g - b) / d) % 6 : mx === g ? (b - r) / d + 2 : (r - g) / d + 4) * 60; return (m + 360) % 360;
  };
  const arco = (a: string, b: string) => { const d = Math.abs(matiz(a) - matiz(b)); return Math.min(d, 360 - d); };
  const SERIES = [["claro", claro, SERIE_META.light], ["escuro", escuro, SERIE_META.dark]] as const;
  it.each(SERIES)("cor de série %s: 3:1 contra a pílula, a trilha e o wash a 12%%; 4 distintas e nenhuma é o brand", (_n, paleta, serie) => {
    expect(new Set(serie).size).toBe(4); expect(serie).not.toContain(paleta.brand);
    for (const c of serie) {
      for (const fundo of [paleta.surfaceControl, paleta.border, sobre(c, paleta.surfaceControl, ALFA_WASH_SERIE)]) expect(contraste(c, fundo)).toBeGreaterThanOrEqual(3);
    }
  });
  it.each(SERIES)("cor de série %s: a 25° ou mais de positive, warning, alert e danger do tema", (_n, paleta, serie) => {
    for (const c of serie) for (const estado of ["positive", "warning", "alert", "danger"] as const) expect(arco(c, paleta[estado])).toBeGreaterThanOrEqual(25);
  });
  it("cor de série: o claro tem o matiz do escuro de mesmo índice (até 12°)", () => {
    SERIE_META.light.forEach((c, i) => expect(arco(c, SERIE_META.dark[i]!)).toBeLessThanOrEqual(12));
  });
  it("controle: as cores escuras da web no claro reprovam (fúcsia #F0ABFC dá 1,53 contra a pílula)", () => {
    expect(contraste("#F0ABFC", claro.surfaceControl)).toBeLessThan(3); expect(contraste("#22D3EE", claro.surfaceControl)).toBeLessThan(3);
  });
});
