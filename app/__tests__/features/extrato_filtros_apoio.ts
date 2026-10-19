/** Apoio dos testes do Extrato: o botão "Filtros" abre a folha; nada muda na consulta antes de "Aplicar filtros". */
import { act, fireEvent, screen, within } from "expo-router/testing-library";
import { drenar } from "./open_finance_volta_rota_apoio";

export type Escolha = { tipo?: "Tudo" | "Entradas" | "Saídas"; origem?: "Todas" | "Banco" | "Cartão" | "Carteira"; min?: string; max?: string };
// Com a folha aberta, "Cartão" existe duas vezes (chip da tela e chip da folha): o toque de dentro da folha procura só nela.
const tocar = (nome: string, papel: "button" | "tab" = "button", dentro = false) => act(async () => { fireEvent.press((dentro ? within(screen.getByTestId("folha-sheet")) : screen).getByRole(papel, { name: nome })); await drenar(); });
export const abrirFiltros = () => tocar("Filtros");
/** `min`/`max` são os dígitos como o dedo digita ("1000" = R$ 10,00). */
export async function filtrar({ tipo, origem, min, max }: Escolha, aplicar = true) {
  await abrirFiltros();
  if (tipo) await tocar(tipo, "tab");
  if (origem) await tocar(origem, "button", true);
  if (min) fireEvent.changeText(screen.getByLabelText(/^Valor mínimo/), min);
  if (max) fireEvent.changeText(screen.getByLabelText(/^Valor máximo/), max);
  if (aplicar) await tocar("Aplicar filtros");
}
/** A ficha de um filtro ativo (o botão com X que o remove). */
export const ficha = (nome: string) => screen.queryByRole("button", { name: `Limpar filtro de ${nome}` });
/** Quantos filtros ativos (o botão é só ícone: a contagem são as fichas). */
export const nFichas = () => screen.queryAllByRole("button", { name: /^Limpar filtro/ }).length;
/** O chip de atalho da tela ("Tudo", "Entradas", "Saídas", "Cartão"); fora da folha. */
// Só o chip tem `selected` no estado: a ficha de mesmo texto ("Saídas") não é ele.
export const atalho = (nome: string) => screen.getAllByRole("button", { name: nome }).find((b) => b.props.accessibilityState?.selected !== undefined)!;
