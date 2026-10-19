import type { Conector, Conexao } from "@/api/schemas/openFinance";
import type { BancoLido } from "@/api/schemas/painel";
import { semAcento } from "@/features/painel/composicaoContas";

export type Item = { conexao: Conexao; banco: BancoLido | undefined };

/**
 * Junta o snapshot (`/open-finance/{uid}`) com `/api/app/bancos` pelo id da conexão (`conexao_id` = `id` local), nunca por posição nem por nome: dois "Nubank"
 * são duas conexões. Quem não veio em `bancos` (conexão recém-conectada, ou que migrou por reconexão) fica sem `banco`, e a tela escreve "—". SEM aritmética de dinheiro.
 */
export function juntar(conexoes: Conexao[], bancos: BancoLido[]): Item[] {
  const porId = new Map(bancos.map((b) => [b.conexao_id, b]));
  return conexoes.map((conexao) => ({ conexao, banco: porId.get(conexao.id) }));
}

/** `logo_url` é dado de terceiro e o aparelho o busca: só https no CDN de ícones da Pluggy (medido em produção: 4 de 4 são SVG ali). Outro host, http, `user@host`, porta ou query = sem logo (a inicial). */
const LOGO_PLUGGY = /^https:\/\/cdn\.pluggy\.ai\/[\w.~/-]+$/;
export const logoSeguro = (url: string | null | undefined): string | undefined => (url && LOGO_PLUGGY.test(url) ? url : undefined);

/**
 * Disponíveis para conectar: os populares, NESTA ordem (cópia do POPULARES de webapp/src/dashboard/lib/bancos.ts; tests/frontend/bancos_paridade.test.mjs compara),
 * que a conta ainda não tem. O NOME só escolhe a ordem; "já conectado" é pelo ID (`institution_id` do snapshot × `id` do conector): o gêmeo Open Finance de um banco tem nome igual e id diferente, e é outra conexão.
 */
export const POPULARES = ["itau", "bradesco", "banco do brasil", "santander", "caixa", "inter", "c6", "nubank", "btg", "mercado pago"];
export function disponiveis(todos: Conector[], conectados: (string | null | undefined)[], max = 5): Conector[] {
  const tem = new Set(conectados);
  const porPosicao = new Map<number, Conector>(); // um por posição: o de nome exato vence o parecido ("Itaú" × "Itaú Personnalité")
  for (const c of todos) {
    if (tem.has(String(c.id))) continue;
    const nome = semAcento(c.name).trim();
    const pos = POPULARES.findIndex((k) => new RegExp(`\\b${k}\\b`).test(nome));
    if (pos < 0) continue;
    const atual = porPosicao.get(pos);
    if (!atual || (nome === POPULARES[pos] && semAcento(atual.name).trim() !== POPULARES[pos])) porPosicao.set(pos, c);
  }
  return [...porPosicao.entries()].sort((a, b) => a[0] - b[0]).slice(0, max).map(([, c]) => c);
}
