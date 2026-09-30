// GERADO — não edite; rode python scripts/gerar_tipos_api_v2.py; tests/test_api_v2_contrato.py compara.
export type Aviso = { recurso: "open_finance" | "tudo" };
export type CorpoErro = { code: string; message: string; details?: Array<DetalheErro> | null };
export type DetalheErro = { loc: Array<string | number>; msg: string; type: string };
export type ErroV2 = { error: CorpoErro };
export type Me = { plan_tier: "free" | "essencial" | "plus" | "pro" };
export type RotasGet = { "/me": Me };
export type RotasSSE = { "/eventos": Aviso };
