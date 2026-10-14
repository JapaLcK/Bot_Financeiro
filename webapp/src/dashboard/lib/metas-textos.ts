// A cópia da tela de metas. Os dois mapas são exaustivos sobre o enum do contrato
// (api-v2.gen.ts): código novo no servidor reprova o `tsc` do build até ganhar texto aqui.
// Código que chegar fora do mapa em runtime sai cru (`rotulo`), nunca some. A ORDEM das chaves
// é a prioridade da frase quando há mais de um motivo.
import type { CustoMensal, Meta, Ritmo } from "./api-v2.gen";
import { ERRO_VALOR, monthName } from "./format.js";
import { ErroApi, FUSO } from "./v2";
import { rotulo } from "../parts/Selos";

// Quantos dias de histórico o servidor exige para medir o ritmo (`RITMO_DIAS_MINIMOS` em db/metas_ritmo.py).
export const DIAS_MINIMOS = 7;
export const MESES_PADRAO = 6; // db.metas.MESES_RESERVA

export type Frase = { texto: string; acao?: "planos" | "banco" };
type MotivoRitmo = Ritmo["motivos"][number];
type MotivoCusto = CustoMensal["motivos"][number];

export const FRASE_RITMO: Record<MotivoRitmo, Frase | ((dias: number | null) => Frase)> = {
  plano_sem_investimentos: { texto: "Seu plano atual não inclui investimentos.", acao: "planos" },
  sem_banco_conectado: { texto: "Conecte um banco pra eu acompanhar.", acao: "banco" },
  fonte_indisponivel: { texto: "Não acho mais essa fonte. Ela pode ter sido desconectada: edite a meta e escolha outra." },
  conexao_pausada: { texto: "A conexão com o banco está pausada. Reconecte pra eu voltar a ler.", acao: "banco" },
  nenhum_investimento: { texto: "Não encontrei investimentos nos seus bancos conectados." },
  historico_incompleto: { texto: "Não consigo medir seu ritmo: um dos seus bancos não confirma a leitura dos investimentos. Reconectar o banco costuma resolver.", acao: "banco" },
  historico_curto: (dias) => ({
    texto: dias === null
      ? "Ainda estou juntando histórico dos seus bancos. Em alguns dias eu calculo seu ritmo."
      : `Ainda estou juntando histórico (${dias} de ${DIAS_MINIMOS} dias). Volto a calcular seu ritmo a partir de ${DIAS_MINIMOS} dias.`,
  }),
  sem_historico_da_fonte: { texto: "Pra saldo de conta eu ainda não meço ritmo. Só o que está investido tem histórico." },
  saldo_ausente: { texto: "O banco não mandou o saldo de alguma posição." },
  outra_moeda: { texto: "Parte do dinheiro está em outra moeda e ficou de fora." },
  banco_desatualizado: { texto: "Seu banco não atualiza há um tempo, então o número pode estar velho." },
  moeda_presumida: { texto: "Considerei em reais uma posição sem moeda informada." },
  conta_fora_do_ultimo_sync: { texto: "Uma conta não veio na última atualização do banco." },
};

export const FRASE_CUSTO: Record<MotivoCusto, string> = {
  plano_sem_custo_mensal: "Seu plano não calcula as contas fixas. Defina o valor você mesmo.",
  sem_banco_conectado: "Conecte um banco pra eu calcular suas contas fixas.",
  sem_recorrencias: "Ainda não achei contas fixas nos seus bancos.",
  recorrencias_banco_nao_lidas: "Não consegui ler as cobranças do banco agora.",
  recorrencia_valor_invalido: "Uma cobrança veio com um valor estranho do banco. Não dá pra calcular agora.",
};

const primeiro = <T extends string>(mapa: Record<T, unknown>, motivos: readonly string[]) =>
  (Object.keys(mapa) as T[]).find((k) => motivos.includes(k));

// Por que o ritmo não tem número. Código fora do mapa sai cru; lista vazia, a frase genérica.
export function fraseRitmo(r: Ritmo): Frase {
  const k = primeiro(FRASE_RITMO, r.motivos);
  if (!k) return { texto: r.motivos.length ? rotulo(r.motivos[0]) : "Ainda não consigo medir seu ritmo." };
  const f = FRASE_RITMO[k];
  return typeof f === "function" ? f(r.dias_de_historico) : f;
}

// No convite e no formulário da reserva não há campo de valor: o "Definir o valor eu mesmo" só existe
// no cartão dela, então a frase aponta para lá em vez de mandar fazer o que a tela não deixa.
const SEM_CUSTO_ANTES_DO_CARTAO = "Seu plano não calcula as contas fixas. Depois de salvar a reserva, defina o valor no cartão dela.";

// Por que o custo mensal (e a reserva) não tem número; com valor, só o aviso de valor velho.
// `antesDoCartao`: quem chama ainda não tem o cartão da reserva (convite e formulário).
export function fraseCusto(c: CustoMensal, antesDoCartao = false): string | null {
  const k = primeiro(FRASE_CUSTO, c.motivos);
  if (c.valor !== null) return k === "recorrencias_banco_nao_lidas" ? `${FRASE_CUSTO[k]} O valor pode estar velho.` : null;
  if (k) return antesDoCartao && k === "plano_sem_custo_mensal" ? SEM_CUSTO_ANTES_DO_CARTAO : FRASE_CUSTO[k];
  return c.motivos.length ? rotulo(c.motivos[0]) : "Não consegui calcular suas contas fixas.";
}

// "2027-10" (ou "2027-10-05") → "outubro de 2027". Sem Date de string: o dia local não importa.
export const mesAno = (iso: string) => {
  const [y, m] = iso.split("-").map(Number);
  return `${monthName(new Date(y, m - 1, 1))} de ${y}`;
};

// Dinheiro é texto: zero ou negativo sem passar por float.
export const ehZero = (s: string) => !/[1-9]/.test(s);
export const ehNegativo = (s: string) => s.startsWith("-") && !ehZero(s);

// O fecho do ritmo: quando chega, e se é antes do prazo (comparação de "AAAA-MM" por texto).
export function fraseChegada(m: Meta): string | null {
  if (m.chega_em) {
    const dentro = m.data_alvo ? (m.chega_em <= m.data_alvo.slice(0, 7) ? ", dentro do prazo" : ", depois do prazo") : "";
    return `Nesse ritmo, chega em ${mesAno(m.chega_em)}${dentro}`;
  }
  const v = m.ritmo.valor;
  if (v !== null && (ehZero(v) || ehNegativo(v)) && m.falta !== null && !ehZero(m.falta)) return "Nesse ritmo, a meta não anda.";
  return null;
}

// "AAAA-MM-DD" de hoje no fuso do app (o `min` do campo de data): o dia do aparelho ou do UTC
// vira antes, das 21h às 24h em SP.
export const hojeNoFuso = () =>
  new Intl.DateTimeFormat("en-CA", { timeZone: FUSO, year: "numeric", month: "2-digit", day: "2-digit" }).format(new Date());

export const CAMPOS_META = ["nome", "tipo", "alvo", "meses", "data_alvo", "fonte"] as const;
export type CampoMeta = (typeof CAMPOS_META)[number];
export const ERRO_CAMPO: Record<CampoMeta, string> = {
  nome: "Informe um nome de 1 a 60 caracteres.",
  tipo: "Escolha entre meta e reserva de emergência.",
  alvo: ERRO_VALOR,
  meses: "Use de 1 a 24 meses.",
  data_alvo: "Escolha uma data de hoje em diante.",
  fonte: "Escolha de onde sai o dinheiro da meta.",
};

export type Falha = { geral: string; campos: Partial<Record<CampoMeta, string>>; acao?: "planos" | "senha" };
// A resposta de erro das escritas em texto humano; nunca a `message` do envelope nem JSON cru.
// A lista é recarregada por quem chama (onSettled), qualquer que seja a falha.
export function falhaDaEscrita(e: unknown, apagando = false): Falha {
  const perdida = `A resposta se perdeu. A meta pode ter sido ${apagando ? "apagada" : "salva"}: confira a lista antes de tentar de novo.`;
  if (!(e instanceof ErroApi) || e.status === 0 || e.status >= 500 || e.status < 400) return { geral: perdida, campos: {} };
  if (e.status === 422) {
    const campos: Falha["campos"] = {};
    for (const d of e.details ?? []) {
      const k = d.loc.find((x): x is CampoMeta => (CAMPOS_META as readonly unknown[]).includes(x));
      if (k) campos[k] = ERRO_CAMPO[k];
    }
    return { geral: Object.keys(campos).length ? "Confira os campos indicados. Seu rascunho foi mantido." : "Confira os dados da meta. Seu rascunho foi mantido.", campos };
  }
  if (e.code === "reserva_ja_existe") return { geral: "Você já tem uma reserva de emergência.", campos: {} };
  if (e.code === "meta_nao_encontrada") return { geral: "Essa meta não existe mais.", campos: {} };
  if (e.code === "pro_required") return { geral: "Seu plano atual não inclui investimentos. Dá para usar o saldo de uma conta.", campos: {}, acao: "planos" };
  if (e.code === "password_required") return { geral: "Crie sua senha para salvar suas metas. Depois de criar, volte para o painel novo.", campos: {}, acao: "senha" };
  if (e.status === 403) return { geral: "Recarregue a página antes de salvar de novo.", campos: {} };
  return { geral: "Não foi possível salvar. Seu rascunho foi mantido.", campos: {} };
}
