// GERADO — não edite; rode python scripts/gerar_tipos_api_v2.py; tests/test_api_v2_contrato.py compara.
export type Aviso = { recurso: "open_finance" | "tudo" };
export type Carteira = { saldo: string; motivos: Array<"carteira_nao_confirmada" | "conciliacao_pendente" | "movimentos_pendentes" | "especie_incompleta"> };
export type Conta = { id: number; instituicao: string | null; nome: string | null; saldo: string | null; moeda: string; no_total: boolean; conexao: "updated" | "partial" | "updating" | "error_recoverable" | "needs_user_action" | "item_missing" | "paused" | "removed" | "no_accounts"; sincronizado_em: string | null; motivos: Array<"banco_desatualizado" | "saldo_ausente" | "moeda_presumida" | "conta_fora_do_ultimo_sync" | "outra_moeda" | "conexao_pausada"> };
export type Contas = { total: string; motivos: Array<"carteira_nao_confirmada" | "conciliacao_pendente" | "movimentos_pendentes" | "especie_incompleta" | "banco_desatualizado" | "saldo_ausente" | "moeda_presumida" | "conta_fora_do_ultimo_sync" | "outra_moeda" | "conexao_pausada">; fora_do_total: number; carteira: Carteira; contas: Array<Conta> };
export type CorpoErro = { code: string; message: string; details?: Array<DetalheErro> | null };
export type DetalheErro = { loc: Array<string | number>; msg: string; type: string };
export type ErroV2 = { error: CorpoErro };
export type Me = { plan_tier: "free" | "essencial" | "plus" | "pro" };
export type NovoPerfil = { perfil: "economizar" | "investir" | "controlar" | "dividas" | "autonomo" | "padrao" };
export type Perfil = { perfil: "economizar" | "investir" | "controlar" | "dividas" | "autonomo" | "padrao" | null };
export type RotasGet = { "/contas": Contas; "/me": Me; "/perfil": Perfil };
export type RotasPut = { "/perfil": { corpo: NovoPerfil; resposta: Perfil } };
export type RotasSSE = { "/eventos": Aviso };
