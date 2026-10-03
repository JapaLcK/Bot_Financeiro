// Os textos do guia (parts/Guia.tsx) que moram no cliente: o roteiro e as falas de cada passo
// vêm do servidor (api/v2/guia.py); aqui, a orientação de cada motivo e o nome da tela de destino.
import type { Passo } from "../lib/api-v2.gen";
import { route } from "../router";
import { ROTA } from "./guia-posicao";

export const OF = "/settings?view=open-finance";
export const MOTIVO: Record<NonNullable<Passo["motivo"]>, { texto: string; link?: string }> = {
  sem_dados: { texto: "Ainda não chegou gasto do seu banco neste mês nem no anterior. Conecta um banco e esse número aparece aqui.", link: "Conectar banco" },
  sincronizando: { texto: "Seu banco está sincronizando agora. Daqui a pouco esse número aparece aqui." },
  conexao_com_erro: { texto: "A conexão com o seu banco deu erro, e esse número não chega. Dá uma olhada nela.", link: "Ver conexão" },
};

// "pro Piggy", "pra Gastos": a preposição concorda com o nome da tela (route().short).
const PRA: Record<Passo["tela"], string> = { resumo: "pro", gastos: "pra", piggy: "pro" };
export const destino = (t: Passo["tela"]) => `${PRA[t]} ${route(ROTA[t]).short}`;
