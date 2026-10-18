import type { Conexao } from "@/api/schemas/openFinance";
import type { BancoLido } from "@/api/schemas/painel";

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

/** Estados em que o usuário tem de agir (`ConnectionStatus`: aviso e `Reconectar`/perdido). */
const PEDEM_ACAO = ["needs_user_action", "item_missing"];
export type Panorama = { acoes: number; tudoEmDia: boolean };
export const panorama = (conexoes: Conexao[]): Panorama => ({
  acoes: conexoes.filter((c) => PEDEM_ACAO.includes(c.ui.state)).length,
  tudoEmDia: conexoes.length > 0 && conexoes.every((c) => c.ui.state === "updated"),
});
