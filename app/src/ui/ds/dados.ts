/**
 * Dados FALSOS das telas-modelo (`app/app/_ds/{inicio,lista,formulario}.tsx`)
 * — nunca importados fora do catálogo de dev.
 */

export const TRANSACOES_FALSAS = [
  { descricao: "Mercado São Luiz", categoria: "Mercado", data: "Hoje, 14:32", centavos: 18990, tipo: "saida" as const },
  { descricao: "Salário", categoria: "Renda", data: "Ontem", centavos: 450000, tipo: "entrada" as const },
  { descricao: "Uber", categoria: "Transporte", data: "Seg, 08:10", centavos: 2350, tipo: "saida" as const },
];

export const CONEXAO_FALSA = {
  label: "Parcial",
  detalhe: "Alguns lançamentos podem estar desatualizados.",
};

/** "Lazer" de propósito sem item em `ITENS_LISTA_FALSOS`: prova o `EmptyState` da tela de lista ao filtrar por ela. */
export const CATEGORIAS_FALSAS = ["Mercado", "Transporte", "Renda", "Lazer"];

export const ITENS_LISTA_FALSOS = [
  { titulo: "Mercado São Luiz", subtitulo: "Hoje, 14:32", categoria: "Mercado" },
  { titulo: "Farmácia Popular", subtitulo: "Ontem", categoria: "Mercado" },
  { titulo: "Uber", subtitulo: "Seg, 08:10", categoria: "Transporte" },
  { titulo: "Salário", subtitulo: "01/09", categoria: "Renda" },
];
