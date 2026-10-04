// Os textos do guia (parts/Guia.tsx) que moram no cliente: o roteiro e as falas de cada passo
// vêm do servidor (api/v2/guia.py); aqui, a orientação de cada motivo e o nome da aba de destino.
import type { Passo } from "../lib/api-v2.gen";

export const OF = "/settings?view=open-finance";
export const MOTIVO: Record<NonNullable<Passo["motivo"]>, { texto: string; link?: string }> = {
  sem_dados: { texto: "Ainda não chegou gasto do seu banco neste mês nem no anterior. Conecta um banco e esse número aparece aqui.", link: "Conectar banco" },
  sincronizando: { texto: "Seu banco está sincronizando agora. Daqui a pouco esse número aparece aqui." },
  conexao_com_erro: { texto: "A conexão com o seu banco deu erro, e esse número não chega. Dá uma olhada nela.", link: "Ver conexão" },
};

// O nome que a pessoa lê na aba destacada: o texto dela (o rótulo do menu lateral, "Para onde
// vai", ou o da barra de baixo, "Gastos"; router.ts, `label` × `short`) ou, na barra de conversa
// do desktop, o placeholder ("Converse com o Piggy…").
export const rotulo = (el: HTMLElement) => el.textContent?.trim() || el.querySelector("input")?.placeholder || "";
// "Agora toca em Gastos", "no Piggy" (o Piggy é masculino), "no Resumo". O rótulo de mais de uma
// palavra vai entre aspas, sem artigo: "em “Para onde vai”", "em “Converse com o Piggy…”".
const EM: Record<string, string> = { Resumo: "no", Gastos: "em", Piggy: "no" };
export const destino = (nome: string) => (EM[nome] ? `${EM[nome]} ${nome}` : `em “${nome}”`);
