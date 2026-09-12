/* Rótulos de `tipo` de lançamento — FONTE ÚNICA (CLAUDE.md §0.7).
   Carregam este arquivo: dashboard.html (defer, antes do dashboard.js) e
   home.html (sem defer, como os vizinhos auth-refresh.js/modals.js — NÃO
   porque o boot inline precise: o `data-pb-boot` é o ÚLTIMO script do
   documento, e o renderGreeting só é alcançado depois de um await).
   O consumidor da home lê o mapa com `typeof` + `hasOwnProperty`, então este
   arquivo faltando degrada o rótulo, não derruba a página.
   `despesa`/`receita` em minúsculas de propósito: é exatamente o que o
   fallback `replaceAll("_", " ")` do dashboard.js já produzia. */
const LAUNCH_TYPE_LABELS = {
  deposito_caixinha: "dep. caixinha",
  saque_caixinha: "saque caixinha",
  aporte_investimento: "aporte invest.",
  resgate_investimento: "resgate invest.",
  transferencia_interna: "transf. interna",
  pagamento_fatura: "pgto. fatura",
  ajuste_saldo: "ajuste saldo",
  criar_caixinha: "criar caixinha",
  create_investment: "criar invest.",
  delete_pocket: "remover caixinha",
  delete_investment: "remover invest.",
  credito: "crédito",
  despesa: "despesa",
  receita: "receita",
};
