import { ErroDeApi, RenovacaoIndisponivel, RequisicaoSuperada } from "@/api/client";
import { SEM_SENHA } from "@/features/openFinance/volta";
import { alvoTexto, codigoDe } from "./metasEscrita";
/**
 * Simulador de compra pelo app: o corpo do POST e a falha, em funções puras. É a cópia mínima de `corpoDe`/`falhaDe` do `SimuladorCompra.tsx` da web (`webapp/src/dashboard/widgets`),
 * com as frases encurtadas (mínimo de texto no app); `simulador_textos_paridade.test.ts` compara as CHAVES (campos e códigos), não as frases. Regra de negócio não se repete aqui: o servidor recusa (422).
 */
export const CAMPOS = ["nome", "preco", "entrada", "parcelas", "juros_mensal_pct", "data_compra", "custos_unicos", "despesa_mensal_nova"] as const;
export type Campo = (typeof CAMPOS)[number];
/** Os que ficam em "Mais opções" (a seção só abre sozinha quando algum deles erra). */
export const CAMPOS_EXTRAS: readonly Campo[] = ["entrada", "juros_mensal_pct", "custos_unicos", "despesa_mensal_nova"];
/** Dinheiro em centavos (vindo do `AmountInput`); `parcelas` e `juros` são o texto digitado; `data` é AAAA-MM-DD ou nada (nada = hoje). */
export type Cen = { nome: string; preco: number; entrada: number; custos: number; despesa: number; parcelas: string; juros: string; data: string | null };
export const VAZIO: Cen = { nome: "", preco: 0, entrada: 0, custos: 0, despesa: 0, parcelas: "", juros: "", data: null };
export const ERRO_CAMPO: Record<Campo | "reserva_minima", string> = {
  nome: "1 a 60 letras", preco: "Informe o preço", entrada: "Confira o valor", parcelas: "1 a 420", juros_mensal_pct: "0 a 20, até 4 casas",
  data_compra: "De hoje em diante", custos_unicos: "Confira o valor", despesa_mensal_nova: "Confira o valor", reserva_minima: "Confira o valor",
};
/** Código estável do servidor (`type` do detalhe) → frase. O campo vem no `loc`; o `msg` do servidor nunca é lido (fala com a IA). `valor_invalido` fica de fora de propósito: cai no genérico do cenário, como no web. */
export const TEXTO_CODIGO: Record<string, string> = {
  entrada_maior_que_preco: "Maior que o preço", entrada_parcial_sem_parcelas: "Informe as parcelas ou tire a entrada", entrada_igual_ao_preco: "Igual ao preço: nada a parcelar",
  juros_sem_parcelas: "Informe as parcelas para usar juros", data_no_passado: "De hoje em diante", data_fora_do_calendario: "Data inválida",
  financiado_pequeno_demais: "Parcelas demais para esse valor", nomes_repetidos: "Nomes repetidos", dinheiro_casas: "Até 2 casas",
};
export const SEM_PLANO = "Simulador indisponível no seu plano.";
export const GENERICO = "Não deu para calcular. Tente de novo.";
const CENARIO = "Confira este cenário.";

/** Só o `preco` vai sempre; o resto só com valor. Dinheiro em texto exato ("1234.50"), nunca float. Nada de `null` nem chave extra (o servidor é `extra="forbid"`). */
export function corpoDe(reserva: number, cens: Cen[]) {
  return {
    ...(reserva > 0 && { reserva_minima: alvoTexto(reserva) }),
    cenarios: cens.map((c) => {
      const juros = c.juros.trim().replace(",", "."), parcelas = c.parcelas.trim();
      return {
        nome: c.nome.trim(), preco: alvoTexto(c.preco),
        ...(c.entrada > 0 && { entrada: alvoTexto(c.entrada) }), ...(c.custos > 0 && { custos_unicos: alvoTexto(c.custos) }), ...(c.despesa > 0 && { despesa_mensal_nova: alvoTexto(c.despesa) }),
        ...(juros && { juros_mensal_pct: juros }), ...(c.data && { data_compra: c.data }),
        // Só 1 a 3 dígitos viram número; o resto segue como TEXTO e o servidor recusa (422). `Number("9".repeat(400))` é Infinity, que o JSON grava como `null` = à vista em silêncio.
        ...(parcelas && { parcelas: /^\d{1,3}$/.test(parcelas) ? Number(parcelas) : parcelas }),
      };
    }),
  };
}

export type Falha = { geral: string; reserva?: string; cenarios: Partial<Record<Campo | "cenario", string>>[]; motivo?: "superada" | "plano" | "portao" };
/** A falha em texto curto; nunca a `message` do servidor. O POST só calcula (não grava): rede e 5xx são "tente de novo", sem resultado perdido. `SessaoExpirada` quem chama trata antes. */
export function falhaDe(e: unknown, n: number): Falha {
  const vazio = Array.from({ length: n }, (): Falha["cenarios"][number] => ({})), com = (geral: string, motivo?: Falha["motivo"]): Falha => ({ geral, cenarios: vazio, ...(motivo && { motivo }) });
  if (e instanceof RenovacaoIndisponivel) return com(GENERICO);
  if (e instanceof RequisicaoSuperada) return com(GENERICO, "superada"); // antes do 409 genérico: a folha era de outra conta
  if (!(e instanceof ErroDeApi) || e.status === 0 || e.status >= 500 || e.status < 400) return com(GENERICO); // inclui `ContratoInvalido` (status 200)
  const code = codigoDe(e);
  if (code === "pro_required") return com(SEM_PLANO, "plano");
  if (code === "password_required") return com(SEM_SENHA);
  if (e.status === 402) return com(GENERICO, "portao");
  if (code === "open_finance_onboarding_required") return com("Conecte seu banco para simular.");
  if (e.status === 429) return com("Muitas simulações. Espere um pouco.");
  if (e.status !== 422) return com(GENERICO);
  const cenarios = vazio.map((): Falha["cenarios"][number] => ({})), detalhes = (e.corpo as { error?: { details?: { loc?: unknown[]; type?: unknown }[] } } | null | undefined)?.error?.details ?? [];
  let geral = "Confira os campos.", reserva: string | undefined;
  for (const d of Array.isArray(detalhes) ? detalhes : []) {
    const loc = Array.isArray(d?.loc) ? d.loc : [], frase = typeof d?.type === "string" && Object.hasOwn(TEXTO_CODIGO, d.type) ? TEXTO_CODIGO[d.type] : undefined;
    if (loc[1] === "reserva_minima") { reserva = ERRO_CAMPO.reserva_minima; continue; }
    const i = loc[1] === "cenarios" && typeof loc[2] === "number" ? loc[2] : -1, k = loc[3], c = cenarios[i];
    if (!c) { if (frase) geral = frase; continue; }
    if (typeof k === "string" && (CAMPOS as readonly string[]).includes(k)) c[k as Campo] = frase ?? ERRO_CAMPO[k as Campo]; else c.cenario = frase ?? CENARIO;
  }
  return { geral, ...(reserva && { reserva }), cenarios };
}
