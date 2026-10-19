import { ErroDeApi, RenovacaoIndisponivel, RequisicaoSuperada } from "@/api/client";
import { SEM_SENHA } from "@/features/openFinance/volta";
import { hojeSP } from "./catalogo";
import { MESES_PADRAO, type MetaV2 } from "./metasTextos";
/**
 * Criar, editar e apagar meta pelo app: o corpo, o diff e o erro, em funções puras. É a cópia mínima do `MetaForm.tsx` e do `falhaDaEscrita` da web
 * (`webapp/src/dashboard`), com as frases encurtadas (mínimo de texto no app); `metas_escrita_paridade.test.ts` compara campos, códigos e limites.
 */
export const NOME_RESERVA = "Reserva de emergência"; // MetaForm.tsx da web
/** A folha vive FORA do `PainelProvider`: quando uma gravação termina com a folha já dispensada (e a aba já releu ao focar), ela avisa a aba por aqui, que relê as metas. */
const ouvintes = new Set<() => void>();
export const metasMudaram = () => ouvintes.forEach((f) => f());
export const aoMetasMudarem = (f: () => void) => { ouvintes.add(f); return () => { ouvintes.delete(f); }; };
export const NOME_MAX = 60, MESES_MIN = 1, MESES_MAX = 24; // api/v2/metas.py: `Nome` (max_length) e `_meses`
export const CAMPOS_META = ["nome", "tipo", "alvo", "meses", "data_alvo", "fonte"] as const;
export type CampoMeta = (typeof CAMPOS_META)[number];
export const ERRO_CAMPO: Record<CampoMeta, string> = { nome: `1 a ${NOME_MAX} letras`, tipo: "Escolha o tipo", alvo: "Informe o valor", meses: `${MESES_MIN} a ${MESES_MAX}`, data_alvo: "De hoje em diante", fonte: "Escolha a fonte" };
export type Fonte = { tipo: "investido" | "banco" | "conta"; banco: string | null; conta: number | null };
export type Campos = { nome: string; tipo: "meta" | "reserva"; centavos: number; meses: number; data: string | null; fonte: Fonte };
export const INVESTIDO: Fonte = { tipo: "investido", banco: null, conta: null };
export const CONTA_A_ESCOLHER: Fonte = { tipo: "conta", banco: null, conta: null };

/** A foto da meta como o formulário a edita. `meses` nulo (meta comum) vira o padrão, só para o − / + ter de onde partir: o diff nunca o manda. */
export const inicialDe = (m: MetaV2): Campos => ({
  nome: m.nome, tipo: m.tipo, centavos: m.alvo ?? 0, meses: m.meses ?? MESES_PADRAO, data: m.data_alvo,
  fonte: { tipo: m.fonte.tipo === "banco" || m.fonte.tipo === "conta" ? m.fonte.tipo : "investido", banco: m.fonte.banco ?? null, conta: m.fonte.conta_id ?? null },
});
/** Centavos inteiros → "1234.50" sem passar por float (o servidor só aceita `^[0-9]{1,9}(\.[0-9]{1,2})?$`). */
export const alvoTexto = (c: number) => `${Math.floor(c / 100)}.${String(c % 100).padStart(2, "0")}`;
const fonteCorpo = (f: Fonte) => f.tipo === "banco" ? { tipo: "banco", banco: f.banco } : f.tipo === "conta" ? { tipo: "conta", conta: f.conta } : { tipo: "investido" };
export const chaveFonte = (f: Fonte) => JSON.stringify(fonteCorpo(f));
/** Criar manda `meses` OU `alvo` (a reserva calcula o alvo: `alvo` nela é 422) e `data_alvo` só se houver. */
export function corpoCriar(c: Campos) {
  return { nome: c.nome.trim(), tipo: c.tipo, fonte: fonteCorpo(c.fonte), ...(c.tipo === "reserva" ? { meses: c.meses } : { alvo: alvoTexto(c.centavos) }), ...(c.data ? { data_alvo: c.data } : {}) };
}
/**
 * Editar manda SÓ o que mudou contra a foto de quando abriu (reenviar o objeto do GET dá 422: `meses: null` numa meta, `alvo` numa reserva); `null` em `data_alvo`
 * tira o prazo; reserva → meta manda `tipo: "meta"` + `alvo` e nunca `meses`. Nada mudou: `null` (o 422 "Nada para editar" não sai do app).
 */
export function corpoEditar(id: number, c: Campos, i: Campos): ({ id: number } & Record<string, unknown>) | null {
  const corpo: { id: number } & Record<string, unknown> = { id }, converte = i.tipo === "reserva" && c.tipo === "meta";
  if (converte) corpo.tipo = "meta";
  if (c.nome.trim() !== i.nome) corpo.nome = c.nome.trim();
  if (c.tipo === "meta" && (converte || c.centavos !== i.centavos)) corpo.alvo = alvoTexto(c.centavos);
  if (c.tipo === "reserva" && c.meses !== i.meses) corpo.meses = c.meses;
  if (c.data !== i.data) corpo.data_alvo = c.data;
  if (chaveFonte(c.fonte) !== chaveFonte(i.fonte)) corpo.fonte = fonteCorpo(c.fonte);
  return Object.keys(corpo).length > 1 ? corpo : null;
}
/** A mesma régua do servidor, antes do envio. Na edição só valida o que mudou (um prazo já vencido, ou um banco que sumiu, não bloqueia salvar o nome). `i` nulo = meta nova. */
export function validar(c: Campos, i: Campos | null): Partial<Record<CampoMeta, string>> {
  const e: Partial<Record<CampoMeta, string>> = {}, nome = c.nome.trim();
  if ((!i || nome !== i.nome) && (!nome || nome.length > NOME_MAX)) e.nome = ERRO_CAMPO.nome;
  if (c.tipo === "meta" && (!i || i.tipo !== "meta" || c.centavos !== i.centavos) && c.centavos <= 0) e.alvo = ERRO_CAMPO.alvo;
  if (c.tipo === "reserva" && (!i || c.meses !== i.meses) && !(c.meses >= MESES_MIN && c.meses <= MESES_MAX)) e.meses = ERRO_CAMPO.meses;
  if (c.data && c.data !== i?.data && c.data < hojeSP()) e.data_alvo = ERRO_CAMPO.data_alvo;
  if ((!i || chaveFonte(c.fonte) !== chaveFonte(i.fonte)) && ((c.fonte.tipo === "banco" && !c.fonte.banco) || (c.fonte.tipo === "conta" && c.fonte.conta === null))) e.fonte = ERRO_CAMPO.fonte;
  return e;
}
/** "AAAA-MM-DD" ↔ Date do seletor nativo: sempre pelos componentes LOCAIS (meio-dia, fora da virada de fuso/horário de verão), nunca por `toISOString` (que é UTC). */
export const dataDoSeletor = (iso: string) => new Date(Number(iso.slice(0, 4)), Number(iso.slice(5, 7)) - 1, Number(iso.slice(8, 10)), 12);
export const isoDoSeletor = (d: Date) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;

export type Motivo = "campos" | "perdida" | "reserva_ja_existe" | "sumiu" | "plano" | "sem_senha" | "portao" | "outra";
export type Falha = { geral: string; campos: Partial<Record<CampoMeta, string>>; motivo: Motivo };
/** O código do envelope `{error:{code}}` da /api/app; o `{detail:{error}}` é o dos portões que saem antes do envelope. */
export function codigoDe(e: unknown): string | undefined {
  const c = e instanceof ErroDeApi ? (e.corpo as { error?: { code?: unknown }; detail?: { error?: unknown } } | null | undefined) : undefined, v = c?.error?.code ?? c?.detail?.error;
  return typeof v === "string" ? v : undefined;
}
/**
 * A falha de uma escrita em texto curto; nunca a `message` do servidor. Rede, tempo esgotado, 5xx e a conta trocada no ar são resultado DESCONHECIDO
 * (`perdida`): o servidor pode ter gravado, e quem chama não reenvia sozinho (o POST não é idempotente). A renovação de sessão falha ANTES de o pedido sair: não é `perdida`.
 */
export function falhaDaEscrita(e: unknown, apagando = false): Falha {
  const perdida: Falha = { geral: `Pode ter ${apagando ? "apagado" : "salvo"}. Confira a lista.`, campos: {}, motivo: "perdida" };
  if (e instanceof RenovacaoIndisponivel) return { geral: "Não foi possível salvar.", campos: {}, motivo: "outra" };
  if (!(e instanceof ErroDeApi) || e instanceof RequisicaoSuperada || e.status === 0 || e.status >= 500 || e.status < 400) return perdida;
  const code = codigoDe(e);
  if (e.status === 402) return { geral: "Não foi possível salvar.", campos: {}, motivo: "portao" }; // assinatura inativa: quem decide o portão é a releitura da aba (`falhou` do PainelProvider), a folha só fecha
  if (e.status === 422) {
    const campos: Falha["campos"] = {}, detalhes = (e.corpo as { error?: { details?: { loc?: unknown[] }[] } } | null | undefined)?.error?.details ?? [];
    for (const d of detalhes) { const k = d.loc?.find((x): x is CampoMeta => (CAMPOS_META as readonly unknown[]).includes(x)); if (k) campos[k] = ERRO_CAMPO[k]; }
    return { geral: "Confira os campos.", campos, motivo: "campos" };
  }
  if (code === "reserva_ja_existe") return { geral: "Você já tem uma reserva.", campos: {}, motivo: "reserva_ja_existe" };
  if (code === "meta_nao_encontrada") return { geral: "Essa meta não existe mais.", campos: {}, motivo: "sumiu" };
  if (code === "pro_required") return { geral: "Seu plano não inclui investimentos.", campos: {}, motivo: "plano" };
  if (code === "password_required") return { geral: SEM_SENHA, campos: {}, motivo: "sem_senha" };
  return { geral: "Não foi possível salvar.", campos: {}, motivo: "outra" };
}
