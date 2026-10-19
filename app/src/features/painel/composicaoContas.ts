import { contraste } from "@/ui/contraste";
import { coresBanco, type Paleta } from "@/ui/tokens";
import type { Segmento } from "@/ui/componentes/BarraComposicao";
import type { Dados } from "./provider";

export const semAcento = (s: string) => s.normalize("NFD").replace(/[̀-ͯ]/g, "").toLowerCase();

/** Cor do banco se for conhecido E passar 3:1 sobre o card; senão a escala de tinta do tema, ciclando pelo índice. */
export function corDoBanco(instituicao: string, indice: number, cores: Paleta): string {
  const tinta = [cores.ink, cores.inkMuted, cores.inkFaint], nome = semAcento(instituicao);
  const chave = Object.keys(coresBanco).find((k) => new RegExp(`\\b${k}\\b`).test(nome)), cor = chave && coresBanco[chave];
  return cor && contraste(cor, cores.surface) >= 3 ? cor : tinta[indice % tinta.length]!;
}

/**
 * LISTA BRANCA de motivos que NÃO mexem no valor do saldo: `outra_moeda` (a conta já fica fora do total) e os da
 * Carteira (db/contas_hoje.py MOTIVOS_CARTEIRA). Qualquer outro motivo (banco_desatualizado, saldo_ausente,
 * conta_fora_do_ultimo_sync, conexao_pausada, moeda_presumida OU um código novo do backend) esconde a barra: número
 * incerto não tem cara de exato. `d.motivos` já é a união dos motivos de todas as contas, não precisa olhar `c.motivos`.
 */
export const MOTIVOS_SEM_EFEITO_NO_SALDO = ["outra_moeda", "carteira_nao_confirmada", "conciliacao_pendente", "movimentos_pendentes", "especie_incompleta"];

/**
 * Composição do "Disponível agora": Carteira (> 0) + contas BRL no total com saldo > 0. Em centavos inteiros, só
 * desenha se a soma fechar EXATAMENTE com `total` (saldo negativo, conta fora da barra etc. a fariam mentir) e se
 * todo motivo do bloco for sem efeito no saldo. Menos de 2 segmentos não é composição.
 */
export function segmentosDeContas(d: Dados["contas"], cores: Paleta): Segmento[] {
  const dentro = d.contas.filter((c) => c.moeda === "BRL" && c.no_total && c.saldo !== null && c.saldo > 0);
  if (!d.motivos.every((m) => MOTIVOS_SEM_EFEITO_NO_SALDO.includes(m))) return [];
  const segs = [...(d.carteira.saldo > 0 ? [{ valor: d.carteira.saldo, cor: corDoBanco("", 0, cores) }] : []),
    ...dentro.map((c, i) => ({ valor: c.saldo!, cor: corDoBanco(c.instituicao ?? "", i + 1, cores) }))];
  return segs.length < 2 || segs.reduce((s, x) => s + x.valor, 0) !== d.total ? [] : segs;
}
