// Os textos do guia (parts/Guia.tsx) que moram no cliente: o roteiro e as falas de cada passo
// vêm do servidor (api/v2/guia.py); aqui, a orientação de cada motivo e o nome da aba de destino.
import type { Passo } from "../lib/api-v2.gen";

export const OF = "/settings?view=open-finance";
export const MOTIVO: Record<NonNullable<Passo["motivo"]>, { texto: string; link?: string }> = {
  sem_dados: { texto: "Ainda não chegou gasto do seu banco neste mês nem no anterior. Conecta um banco e esse número aparece aqui.", link: "Conectar banco" },
  sincronizando: { texto: "Seu banco está sincronizando agora. Daqui a pouco esse número aparece aqui." },
  conexao_com_erro: { texto: "A conexão com o seu banco deu erro, e esse número não chega. Dá uma olhada nela.", link: "Ver conexão" },
};

// Para onde o balão manda tocar, pelo que a pessoa VÊ na aba destacada: o texto visível dela
// (o `innerText` pula o que o CSS esconde: o rótulo do menu lateral some entre 761 e 1180 px,
// shell.css) ou, só com o ícone, o `title` dele. "em Gastos", "no Piggy" (o Piggy é masculino),
// "no Resumo"; o rótulo de mais de uma palavra vai entre aspas, sem artigo: "em “Para onde vai”".
// A barra de conversa do desktop (D9) é ela mesma: o placeholder já é uma instrução.
const EM: Record<string, string> = { Resumo: "no", Gastos: "em", Piggy: "no" };
export const destino = (el: HTMLElement) => {
  if (el.matches('[data-guia="piggy.pergunta"]')) return "na barra de conversa";
  const nome = el.innerText.trim();
  if (!nome) return `no ícone “${el.title || el.getAttribute("aria-label")}”`;
  return EM[nome] ? `${EM[nome]} ${nome}` : `em “${nome}”`;
};
