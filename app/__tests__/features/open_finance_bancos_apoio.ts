// Apoio das suítes de Bancos conectados (lista, dinheiro e logo): o servidor de teste e as fixtures. Não é `.test.tsx`: o jest só roda `*.test.ts?(x)`.
import type { Conexao } from "@/api/schemas/openFinance";
// O contrato LIDO do arquivo que o pytest valida contra o Pydantic (PR 1); nunca copiado (CLAUDE.md §0.7).
import contrato from "../../../tests/fixtures/app_bancos.json";
import { resposta, rotear, type Rota } from "./auth_apoio";
import { VIVO } from "./open_finance_volta_apoio";

// O SVG do logo vem do CDN: aqui a rede é o `fetchFalso` (rota por caminho) e o `react-native-svg` é o real.
const SVG = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10"><circle cx="5" cy="5" r="4"/></svg>';
export const logoRota = () => ({ ok: true, status: 200, text: async () => SVG }) as unknown as Response;

export const banco = (id: number, nome: string, extra: Partial<Conexao> = {}): Conexao => ({ ...VIVO, id, provider_item_id: `item_${id}`, institution_name: nome, logo_url: null, ...extra });
export const NUBANK = banco(11, "Nubank"), INTER = banco(12, "Inter");
export const HIDDEN = { includeHiddenElements: true };
export const DINHEIRO = /R\$\s?\d/;
type Bancos = { status?: number; corpo?: unknown; cai?: boolean };
export function servidor(lista: Conexao[], bancos: Bancos = {}, rotas: Record<string, Rota> = {}) {
  rotear({
    "/open-finance/1": () => resposta(200, { connections: lista }),
    "/open-finance/1/limite": () => resposta(200, { ok: true, of_banks_max: 2, em_uso: lista.length, pode_adicionar: lista.length < 2, code: null, message: null }),
    "/open-finance/1/connections/11": () => { lista = lista.filter((c) => c.id !== 11); return resposta(200, { ok: true, deleted: 1 }); },
    "/api/app/bancos": () => { if (bancos.cai) throw new TypeError("rede fora"); return resposta(bancos.status ?? 200, bancos.corpo ?? contrato); },
    "/assets/connector-icons/201.svg": logoRota, "/assets/connector-icons/202.svg": logoRota,
    ...rotas,
  });
}
