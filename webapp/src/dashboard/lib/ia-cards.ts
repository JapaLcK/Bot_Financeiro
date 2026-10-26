// Os recursos dos cards `resumo`, `categorias`, `calendario` (`detalhes`) e `periodo` do chat. Não são da /api/v2 (o `api-v2.gen.ts` não os gera): saem de
// `api/nativo/mes_detalhes.py` e entram no bloco do stream. Escritos à mão, no mesmo formato do gerador (dinheiro é texto). A lista de campos é comparada
// com o Pydantic por tests/test_ai_chat_cards_fixtures_painel.py, e as fixtures (tests/fixtures/ai_chat_recurso_detalhes.json, ai_chat_bloco_periodo.json) são o dump dele.
import type { Lancamento } from "./api-v2.gen";

export type MesDetalhes = {
  mes: string; ate: string; entrou: string; saiu: string;
  categorias: Array<{ categoria: string | null; valor: string; quantidade: number }>;
  origens: { cartao: string; banco: string; carteira: string; registro_antigo: string };
  dias: Array<{ dia: string; entrou: string; saiu: string }>;
  guardado: { aportes: string; saques: string; liquido: string; cobertura: "movimentos_registrados" };
  motivos: string[]; fora_do_total: number; criterio_dias: "data_do_lancamento_ou_compra_na_fatura_do_mes";
};

export type Periodo = {
  inicio: string; fim: string; ate: string; entrou: string; saiu: string; saldo: string;
  meses: Array<{ mes: string; entrou: string; saiu: string }>;
  categorias: Array<{ categoria: string | null; valor: string; quantidade: number; percentual: number; maiores: Lancamento[] }>;
  motivos: string[]; historico_desde: string | null; fora_do_total: number;
};

// O recurso `rendimento` do card `rendimento` (`api/nativo/rendimento.py`, Pydantic): dinheiro e `pct` em texto. Numa declaração só, com os aninhados inline, para o teste de
// campos (tests/test_ai_chat_cards_fixtures_painel.py) achar todos.
export type Rendimento = {
  tipo: "contratado"; motivos: string[];
  itens: Array<{ nome: string | null; instituicao: string | null; taxa: string | null; tipo_taxa: string | null; observado_em: string | null; motivos: string[] }>;
  rendimentos: null | {
    saldo: string | null; quantidade: number; mes: { valor: string | null; pct: string | null; sem_dado: number }; total: { valor: string | null; pct: string | null; sem_dado: number };
    mes_desde: string; sincronizado_em: string | null; motivos: string[];
    categorias: Array<{
      tipo: string; rotulo: string; saldo: string | null; quantidade: number; mes: { valor: string | null; pct: string | null; sem_dado: number }; total: { valor: string | null; pct: string | null; sem_dado: number };
      ativos: Array<{
        nome: string | null; instituicao: string | null; logo_proprio: string | null; saldo: string | null;
        mes: { valor: string | null; pct: string | null; motivo: string | null }; total: { valor: string | null; pct: string | null; motivo: string | null };
      }>;
    }>;
  };
};

/** O guarda de objeto dos contratos dos cards (nenhum, lista ou texto passam). */
export const obj = (x: unknown): x is Record<string, unknown> => !!x && typeof x === "object" && !Array.isArray(x);
