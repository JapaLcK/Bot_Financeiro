import type { PerfilPainel } from "@/api/schemas/painel";
import type { Widget } from "./catalogo";
// Cópia de webapp/src/dashboard/lib/prompts.js (sem `head` e sem os `ask: null`). Teste de paridade: painel_conversa.test.tsx.
export type Pergunta = { key: string; topic: string; cat?: string; ask: string };
export const COMMON: Pergunta[] = [
 { key: "economizar-mes", topic: "categorias", ask: "Onde eu consigo economizar este mês?" },
 { key: "semana", topic: "lancamentos", ask: "Pra onde foi meu dinheiro nos últimos 7 dias?" },
 { key: "meta", topic: "metas", ask: "Quanto falta pra minha próxima meta e quando eu chego lá?" },
 { key: "normal", topic: "categorias", ask: "Meu gasto deste mês tá acima ou abaixo do normal?" },
 { key: "investido", topic: "investido", ask: "Quanto eu tenho investido?" },
 { key: "assinaturas", topic: "categoria", cat: "assinaturas", ask: "Quais assinaturas eu pago todo mês?" },
 { key: "fim-de-semana", topic: "saldo", ask: "Quanto posso gastar no fim de semana sem apertar o resto do mês?" },
 { key: "maior-gasto", topic: "lancamentos", ask: "Qual foi meu maior gasto este mês?" },
 { key: "resumo", topic: "categorias", ask: "Me dá um resumo do meu mês em 3 linhas." },
 { key: "sobrar", topic: "saldo", ask: "Vai sobrar dinheiro até o fim do mês?" },
];
export const BY_PROFILE: Partial<Record<PerfilPainel, Pergunta[]>> = {
 economizar: [
  { key: "cortar-delivery", topic: "categoria", cat: "delivery", ask: "Quanto eu guardaria por mês cortando metade do delivery?" },
  { key: "meta-economia", topic: "metas", ask: "Me ajuda a montar uma meta de economia pra este mês?" },
 ],
 investir: [
  { key: "carteira-cdi", topic: "investimentos", ask: "Minha carteira está rendendo bem comparada ao CDI?" },
  { key: "quanto-investir", topic: "saldo", ask: "Quanto eu consigo investir este mês sem apertar as contas?" },
  { key: "reserva", topic: "metas", ask: "Minha reserva de emergência está no tamanho certo?" },
 ],
 controlar: [
  { key: "dia-semana", topic: "lancamentos", ask: "Em que dia da semana eu mais gasto?" },
  { key: "categoria-cresceu", topic: "categoria", ask: "Qual categoria de gasto mais cresceu este mês?" },
 ],
 dividas: [
  { key: "fatura-cabe", topic: "fatura", ask: "Quando minha fatura volta a caber no meu mês?" },
  { key: "antecipar", topic: "fatura", ask: "Vale a pena eu antecipar alguma parcela?" },
  { key: "comprometido", topic: "fatura", ask: "Quanto do meu mês já está comprometido com contas e parcelas?" },
 ],
 autonomo: [
  { key: "mes-fraco", topic: "renda", ask: "Quanto preciso guardar num mês bom pra cobrir um mês fraco?" },
  { key: "renda-variou", topic: "renda", ask: "Quanto minha renda variou nos últimos 6 meses?" },
  { key: "no-azul", topic: "saldo", ask: "Meu mês está fechando no azul até agora?" },
 ],
};
/** Os 5 chips do estado vazio: perfil primeiro, depois COMMON (igual ao PiggyChat do web). */
export const sugestoes = (perfil: PerfilPainel) => [...(BY_PROFILE[perfil] ?? []), ...COMMON].slice(0, 5);
const POR_TOPICO: Record<string, Widget> = { categorias: "categorias", categoria: "categorias", lancamentos: "calendario", metas: "metas", investido: "wealth", investimentos: "rendimento", saldo: "hero", fatura: "fatura", renda: "renda" };
/** Widget real do app para o assunto do chip; `categoria` de assinaturas abre o widget de assinaturas. */
export const widgetDoTopico = (p: Pick<Pergunta, "topic" | "cat">): Widget | undefined => p.topic === "categoria" && p.cat === "assinaturas" ? "assinaturas" : POR_TOPICO[p.topic];
/** Widgets que dependem do mês do painel: só aparecem no mês atual. */
export const WIDGETS_DO_MES: Widget[] = ["categorias", "calendario"];
