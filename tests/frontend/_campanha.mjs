/** Mock da `campanha` do /billing/plans-config. tests/test_oferta_campanha_lacunas.py compara com `campanha_config()`. */
const ate = " só até 30 de novembro.";
const txt = (nome, a, mes, cheioA) => ({
  anual: a, anual_mes: mes, linha_anual: `${a} no 1º ano, depois ${cheioA} por ano`,
  frase_anual: `12 meses de ${nome} pelo preço de 6, ${a} no plano anual,${ate}` });
// Só o anual tem oferta: `monthly`/`pct_mensal` são null. `na_hora` = sem trial.
const p = (annual) => ({ pct: 40, pct_mensal: null, monthly: null, annual, pix: annual, na_hora: true });
export const CAMP = {
  desconto_pct: 40, trial: false,
  precos: { plus: p(11940), pro: p(29940) },
  textos: { plus: txt("Plus", "R$ 119,40", "R$ 9,95", "R$ 199"), pro: txt("Pro", "R$ 299,40", "R$ 24,95", "R$ 499") },
  faixa: "Black Friday do PigBank. 12 meses de Plus pelo preço de 6, só até segunda, 30/11.",
};
export const CFG = (campanha) => ({ essencial_available: true, plus_available: true, pro_available: true, trial_days: 7,
  pix_annual_available: false, campanha });
