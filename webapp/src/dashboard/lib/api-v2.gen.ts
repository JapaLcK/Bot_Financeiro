// GERADO — não edite; rode python scripts/gerar_tipos_api_v2.py; tests/test_api_v2_contrato.py compara.
export type Assinatura = { chave: string; nome: string; categoria: string | null; valor: number; valor_anterior: number | null; reajuste_em: string | null; dia: number; proxima: string; ultima: string; desde: string; meses: number; meio: Meio; status: "ativa" | "possivelmente_cancelada"; marcada: boolean };
export type Assinaturas = { servicos: Array<Assinatura>; outras: Array<Assinatura>; total_mensal: number; total_anual: number };
export type Aviso = { recurso: "open_finance" | "tudo" };
export type CorpoErro = { code: string; message: string; details?: Array<DetalheErro> | null };
export type DetalheErro = { loc: Array<string | number>; msg: string; type: string };
export type ErroV2 = { error: CorpoErro };
export type MarcaIn = { chave: string; status: "assinatura" | "ignorar" | "nenhuma" };
export type Me = { plan_tier: "free" | "essencial" | "plus" | "pro" };
export type Meio = { tipo: "cartao" | "conta"; nome: string; final: string | null };
export type RotasGet = { "/assinaturas": Assinaturas; "/me": Me };
export type RotasSSE = { "/eventos": Aviso };
export type RotasPost = { "/assinaturas/marca": { corpo: MarcaIn; resposta: Assinaturas } };
