import type { PerfilPainel } from "@/api/schemas/painel";
export const TITULOS = { contas: "Contas", hero: "Saldo previsto", resumo: "Resumo do mês", categorias: "Para onde vai", calendario: "Dia a dia", simulador: "Simulador", compromissos: "Próximos 30 dias", piggy: "Piggy notou", metas: "Metas e caixinhas", patrimonio: "Patrimônio", fatura: "Fatura do cartão", wealth: "Onde está o dinheiro", renda: "Renda mês a mês", rendimento: "Rendimento contratado", parcelas: "Parcelas futuras", assinaturas: "Assinaturas" };
export type Widget = keyof typeof TITULOS;
export const PADRAO: Widget[] = ["contas", "hero", "resumo", "categorias", "calendario", "simulador", "compromissos", "piggy", "metas", "patrimonio"];
export const PERFIS: { id: PerfilPainel; label: string; preset: Widget[] }[] = [
  { id: "padrao", label: "Padrão", preset: PADRAO },
  { id: "economizar", label: "Economizar", preset: ["contas", "resumo", "metas", "piggy", "categorias", "assinaturas", "simulador", "compromissos"] },
  { id: "investir", label: "Investir", preset: ["contas", "patrimonio", "rendimento", "wealth", "simulador", "metas", "resumo", "piggy"] },
  { id: "controlar", label: "Controlar gastos", preset: ["contas", "categorias", "calendario", "resumo", "compromissos", "assinaturas", "fatura", "piggy"] },
  { id: "dividas", label: "Sair das dívidas", preset: ["contas", "parcelas", "fatura", "compromissos", "hero", "resumo", "categorias", "piggy"] },
  { id: "autonomo", label: "Autônomo", preset: ["contas", "renda", "hero", "resumo", "compromissos", "metas", "categorias", "piggy"] },
];
export const preset = (p: PerfilPainel) => PERFIS.find((x) => x.id === p)!.preset;
export function sanitizarLayout(raw: unknown, p: PerfilPainel): Widget[] {
  if (!Array.isArray(raw)) return [...preset(p)];
  return [...new Set(raw)].filter((v): v is Widget => typeof v === "string" && Object.hasOwn(TITULOS, v));
}
export const chaveLayout = (uid: number, p: PerfilPainel) => `pigbank.painel.v1.u${uid}.${p}`;
export function mesAtual() { return new Intl.DateTimeFormat("sv-SE", { timeZone: "America/Sao_Paulo", year: "numeric", month: "2-digit" }).format(new Date()); }
export function mesesAnteriores(mes: string, quantidade = 12) { const ano = Number(mes.slice(0, 4)), numero = Number(mes.slice(5, 7)); return Array.from({ length: quantidade }, (_, i) => { const d = new Date(Date.UTC(ano, numero - 1 - i, 1)); return d.toISOString().slice(0, 7); }); }
export const nomeMes = (mes: string) => new Intl.DateTimeFormat("pt-BR", { month: "long", year: "numeric", timeZone: "UTC" }).format(new Date(`${mes}-01T12:00:00Z`));

/** Datas civis do banco não passam pelo fuso do dispositivo. */
export function dataPtBr(dia: string | null) {
  if (!dia || !/^\d{4}-\d{2}-\d{2}$/.test(dia)) return "Data não informada";
  return `${dia.slice(8)}/${dia.slice(5, 7)}/${dia.slice(0, 4)}`;
}
export function indicadorMeta(indicador: string) {
  const rotulos: Record<string, string> = { no_target: "Sem objetivo definido", achieved: "Objetivo alcançado", on_track: "No ritmo para alcançar", ahead: "Adiantada em relação ao objetivo", behind: "Abaixo do ritmo necessário", tight: "Prazo apertado", active: "Meta em andamento", no_deadline: "Sem prazo definido" };
  return rotulos[indicador] ?? "Ritmo da meta a conferir";
}
