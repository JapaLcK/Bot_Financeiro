// Perguntas da faixa do Piggy no topo do Resumo. `head` é o convite (a voz do Piggy);
// `ask` é o que vai para o chat em nome do usuário. `ask: null` abre o chat sem enviar.
// As do perfil pesam mais no sorteio; os insights do dia entram junto (parts/PiggyBand.tsx).

export const COMMON = [
  { key: "economizar-mes", head: "Bora achar onde dá pra economizar este mês?", ask: "Onde eu consigo economizar este mês?" },
  { key: "saber-hoje", head: "O que você quer saber hoje?", ask: null },
  { key: "semana", head: "Quer ver pra onde foi seu dinheiro na última semana?", ask: "Pra onde foi meu dinheiro nos últimos 7 dias?" },
  { key: "meta", head: "Quanto falta pra sua próxima meta?", ask: "Quanto falta pra minha próxima meta e quando eu chego lá?" },
  { key: "normal", head: "Seu mês tá acima ou abaixo do normal?", ask: "Meu gasto deste mês tá acima ou abaixo do normal?" },
  { key: "assinaturas", head: "Tem alguma assinatura que você nem usa mais?", ask: "Quais assinaturas eu pago todo mês?" },
  { key: "fim-de-semana", head: "Dá pra curtir o fim de semana sem estourar?", ask: "Quanto posso gastar no fim de semana sem apertar o resto do mês?" },
  { key: "maior-gasto", head: "Qual foi seu maior gasto do mês?", ask: "Qual foi meu maior gasto este mês?" },
  { key: "resumo", head: "Quer o seu mês resumido em 3 linhas?", ask: "Me dá um resumo do meu mês em 3 linhas." },
  { key: "sobrar", head: "Vai sobrar dinheiro até o fim do mês?", ask: "Vai sobrar dinheiro até o fim do mês?" },
];

export const BY_PROFILE = {
  economizar: [
    { key: "cortar-delivery", head: "Quanto você guardaria cortando metade do delivery?", ask: "Quanto eu guardaria por mês cortando metade do delivery?" },
    { key: "meta-economia", head: "Bora montar uma meta de economia pro mês?", ask: "Me ajuda a montar uma meta de economia pra este mês?" },
  ],
  investir: [
    { key: "carteira-cdi", head: "Sua carteira tá rendendo bem perto do CDI?", ask: "Minha carteira está rendendo bem comparada ao CDI?" },
    { key: "quanto-investir", head: "Quanto dá pra investir este mês sem aperto?", ask: "Quanto eu consigo investir este mês sem apertar as contas?" },
    { key: "reserva", head: "Sua reserva de emergência já tá no tamanho certo?", ask: "Minha reserva de emergência está no tamanho certo?" },
  ],
  controlar: [
    { key: "dia-semana", head: "Em que dia da semana você mais gasta?", ask: "Em que dia da semana eu mais gasto?" },
    { key: "categoria-cresceu", head: "Qual categoria mais cresceu este mês?", ask: "Qual categoria de gasto mais cresceu este mês?" },
  ],
  dividas: [
    { key: "fatura-cabe", head: "Quando sua fatura volta a caber no mês?", ask: "Quando minha fatura volta a caber no meu mês?" },
    { key: "antecipar", head: "Vale a pena antecipar alguma parcela?", ask: "Vale a pena eu antecipar alguma parcela?" },
    { key: "comprometido", head: "Quanto do seu mês já tá comprometido?", ask: "Quanto do meu mês já está comprometido com contas e parcelas?" },
  ],
  autonomo: [
    { key: "mes-fraco", head: "Quanto guardar num mês bom pra cobrir um fraco?", ask: "Quanto preciso guardar num mês bom pra cobrir um mês fraco?" },
    { key: "renda-variou", head: "Quanto sua renda variou nos últimos meses?", ask: "Quanto minha renda variou nos últimos 6 meses?" },
    { key: "no-azul", head: "Seu mês tá fechando no azul até agora?", ask: "Meu mês está fechando no azul até agora?" },
  ],
};

/** Sorteio com peso, sem repetir `last` quando há outra opção. */
export function pick(options, last, rand = Math.random) {
  const pool = options.length > 1 ? options.filter((o) => o.key !== last) : options;
  const total = pool.reduce((s, o) => s + o.weight, 0);
  let r = rand() * total;
  for (const o of pool) if ((r -= o.weight) < 0) return o;
  return pool[pool.length - 1];
}
