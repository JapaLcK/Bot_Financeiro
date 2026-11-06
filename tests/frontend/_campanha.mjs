/** Mock da `campanha` do /billing/plans-config. tests/test_oferta_campanha_lacunas.py compara com `campanha_config()`. */
const ate = " só até 30 de novembro.";
const txt = (nome, m, a, mes, cheioM, cheioA) => ({
  mensal: m, anual: a, anual_mes: mes,
  linha_mensal: `1º mês por ${m}, depois ${cheioM} por mês`, linha_anual: `${a} no 1º ano, depois ${cheioA} por ano`,
  frase_anual: `12 meses de ${nome} pelo preço de 6, ${a} no plano anual,${ate}`,
  frase_mensal: `1º mês do ${nome} por ${m}, depois ${cheioM} por mês. Só até 30 de novembro.` });
export const CAMP = {
  desconto_pct: 40, trial: false,
  precos: { plus: { pct: 40, monthly: 1194, annual: 11940, pix: 11940 }, pro: { pct: 40, monthly: 2994, annual: 29940, pix: 29940 } },
  textos: { plus: txt("Plus", "R$ 11,94", "R$ 119,40", "R$ 9,95", "R$ 19,90", "R$ 199"),
            pro: txt("Pro", "R$ 29,94", "R$ 299,40", "R$ 24,95", "R$ 49,90", "R$ 499") },
  frase: "Black Friday: 40% OFF nos planos Plus e Pro, anual a partir de R$ 119,40, só até 30 de novembro.",
  frase_assinante: "Black Friday: o anual no Pix sai com 40% OFF nos planos Plus e Pro, só até 30 de novembro.",
};
export const CFG = (campanha) => ({ essencial_available: true, plus_available: true, pro_available: true, trial_days: 7,
  pix_annual_available: false, campanha });
