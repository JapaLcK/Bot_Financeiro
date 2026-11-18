/* Exceção do trial na Black Friday: o anual do Plus e do Pro é cobrado na hora. Uma frase só, para a / e a /precos.
   Só com a faixa da campanha visível (#oferta-faixa); acrescenta aos elementos de `seletor` que falam "7 dias".
   Idempotente: chamada por quem pinta a faixa e por quem reescreve o texto. */
window.PBExcecaoTrialBF = function (seletor) {
  if (document.getElementById("oferta-faixa")?.hidden !== false) return;
  for (const el of document.querySelectorAll(seletor)) {
    if (el.querySelector("[data-bf-trial]") || !/7 dias/.test(el.textContent)) continue;
    const t = document.createElement("span");
    t.dataset.bfTrial = "";
    t.textContent = " Exceção: o anual do Plus e do Pro na Black Friday é cobrado na hora, sem período grátis; nos outros planos, os 7 dias continuam.";
    el.append(t);
  }
};
