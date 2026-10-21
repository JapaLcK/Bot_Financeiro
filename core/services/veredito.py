"""Veredito determinístico do simulador de compra: montado pelo código, nunca pelo modelo.

Função pura sobre a snapshot (o isolamento por usuário já vem dela). Estados: `risco_identificado`
("não recomendo", com dia e valor), `dados_insuficientes`, `dados_desatualizados` (atualizar ou
conferir resolve) e `sem_risco_identificado` (dados confiáveis e nenhum risco achado: NÃO é "cabe",
que fica desligado até a Etapa 3 de `docs/plano-piggy-assistente-contextual.md`). A regra, em ordem:
saldo ausente, motivo estrutural, risco no pior caso, risco que só some com uma dúvida a favor.
"""
from __future__ import annotations

import os

from core.services import cashflow
from core.services.cashflow_contract import FONTE_VARIAVEL, Snapshot, Ocorrencia, centavos
from core.services.cashflow_forecast import trajetoria_com_hoje

# "Atualizar ou conferir resolve": estes motivos viram `dados_desatualizados`, os demais `dados_insuficientes`.
DESATUALIZADOS = frozenset({"banco_desatualizado", "conta_fora_do_ultimo_sync", "recorrencias_banco_nao_lidas",
                            "conciliacao_a_conferir", "declaracao_bancaria_a_conferir"})
DUVIDA_RECENTE = "realizacao_recente_a_conferir"  # fixo ou receita manual dos últimos dias, sem marcador
GENERICO = "Há um dado a conferir antes de concluir."

# Fonte única do texto dos bloqueios (código -> frase de ação). {N} = horas de `BANCO_VELHO`.
TEXTO = {
    "saldo_indisponivel": "Não consegui ler seu saldo de hoje.",
    "carteira_nao_confirmada": "Confirme quanto você tem na Carteira hoje.",
    "banco_desatualizado": 'Um banco não atualiza há mais de {N} horas ou não está em "Atualizado". Atualize os bancos.',
    "conta_fora_do_ultimo_sync": "Uma conta não veio na última atualização do banco.",
    "conexao_pausada": "Uma conexão com banco está pausada.",
    "saldo_ausente": "O banco não mandou o saldo de uma conta.",
    "moeda_presumida": "Não sei a moeda de uma conta.",
    "outra_moeda": "Uma conta em outra moeda fica fora da conta.",
    "especie_incompleta": "O dinheiro em espécie dos bancos ainda não entra na conta.",
    "bancos_excluidos": "Os saldos dos bancos ficaram fora desta conta.",
    "cobertura_bancaria_nao_comprovada": "Ainda não consigo garantir que li tudo do banco.",
    "cobertura_fatura_nao_comprovada": "Ainda não consigo garantir que a fatura do banco está completa.",
    "recorrencia_banco_estimada": "Algumas cobranças vêm do histórico do banco e são estimadas.",
    "recorrencia_banco_atrasada": "Uma cobrança do banco está atrasada em relação ao histórico.",
    "recorrencia_banco_pode_repetir_manual": "Uma cobrança do banco pode já estar nos seus gastos fixos.",
    "recorrencia_banco_conta_fora_da_base": "Uma cobrança do banco é de uma conta que ficou fora da conta.",
    "recorrencia_banco_interrompida": "Uma cobrança do banco parou de se repetir.",
    "recorrencia_banco_igual_a_fixo_manual": "Uma cobrança do banco é igual a um gasto fixo seu.",
    "recorrencias_banco_nao_lidas": "As cobranças do banco não foram lidas nas últimas {N} horas.",
    "acao_financeira_pendente": "Você tem um pedido esperando confirmação na conversa. Responda antes.",
    "valor_recorrente_estimado": "Um gasto fixo tem valor estimado. Confirme o valor.",
    "valor_boleto_estimado": "Um boleto tem valor estimado. Confirme o valor.",
    "valor_recorrente_desconhecido": "Um gasto ou receita fixa está sem valor.",
    "valor_boleto_desconhecido": "Um boleto está sem valor.",
    "realizacao_passada_desconhecida": "Não sei se os gastos e receitas fixas de antes já aconteceram.",  # a janela o substitui aqui
    "calendario_recorrente_desconhecido": "Um gasto ou receita fixa está sem data válida.",
    "data_boleto_desconhecida": "Um boleto está sem vencimento.",
    "instancia_recorrente_a_conferir": "Um boleto de gasto fixo precisa de conferência.",
    "calendario_fatura_presumido": "Não sei o vencimento de uma fatura de cartão.",
    "valor_fatura_a_conferir": "O valor de uma fatura de cartão não fecha. Confira.",
    "moeda_fatura_desconhecida": "Uma fatura de cartão está em outra moeda ou sem moeda.",
    "pagamento_fatura_reflexo_desconhecido": "Não sei se um pagamento de fatura já saiu do seu saldo.",
    "conciliacao_a_conferir": "Confira as movimentações do banco que esperam conciliação.",
    "conciliacao_pendente": "Há movimentações do banco esperando conciliação.",
    "movimentos_pendentes": "Há movimentações do banco ainda pendentes.",
    "declaracao_bancaria_a_conferir": "Confira os gastos que você declarou como pagos pelo banco.",
    "realizacao_boleto_a_conferir": "Estes boletos já foram pagos?",
    "realizacao_fatura_a_conferir": "Estas faturas já foram pagas?",
    DUVIDA_RECENTE: "Estes já caíram ou já foram cobrados?",
    "data_parcela_presumida": "Quando vence a primeira parcela? Considerei um mês depois da compra.",
    "data_despesa_mensal_presumida": "Quando começa a despesa mensal nova? Considerei um mês depois da compra.",
    # Só melhoram a conta (não bloqueiam o "não recomendo"); o "cabe" vai usá-los.
    "receita_nao_garantida": "Uma receita fixa prevista pode não vir.",
    "incorporacao_cartao_nao_comprovada": "Um gasto fixo no cartão pode não estar na fatura.",
    "parcelas_futuras_incompletas": "Faltam parcelas futuras de uma compra no cartão do banco.",
    "cartao_manual_cobertura_incompleta": "Compras recentes no cartão manual podem não estar lançadas.",
    "gastos_variaveis_nao_estimados": "Ainda não tenho histórico para estimar seus gastos do dia a dia.",
}


def ligado() -> bool:
    """Chave de emergência, lida a cada pedido: desligada, o veredito sai `null` e o resto da resposta não muda."""
    return os.getenv("SIMULADOR_VEREDITO_ENABLED", "1").strip().lower() not in ("0", "false", "no", "off")


def _texto(codigo: str) -> str:
    from db.patrimonio import BANCO_VELHO  # fonte única do limite de 48 h; o veredito não mede idade
    return TEXTO.get(codigo, GENERICO).format(N=int(BANCO_VELHO.total_seconds() // 3600))


def _duvida(e: Ocorrencia) -> str:
    return next((m.codigo for m in e.motivos if m.direcao_do_erro in ("so_piora", "ambos")), DUVIDA_RECENTE)


def veredito(snapshot: Snapshot, extra: list[Ocorrencia], reserva, dias: int) -> dict:
    premissa = (f"Considerei que o previsto antes de {snapshot.conferencia_desde:%d/%m} já aconteceu."
                if snapshot.conferencia_desde else None)

    def saida(estado, grupos=None, data=None, valor=None):
        return {"estado": estado, "data": data, "valor": valor, "premissa": premissa,
                "bloqueios": [{"codigo": c, "texto": _texto(c), "itens": i} for c, i in (grupos or {}).items()]}

    def falta(grupos):
        return saida("dados_desatualizados" if all(c in DESATUALIZADOS for c in grupos) else "dados_insuficientes",
                     grupos)

    if snapshot.base["saldo"] is None:
        return saida("dados_insuficientes", {"saldo_indisponivel": []})
    # A janela substitui `realizacao_passada_desconhecida` (continua bloqueando o `project()`).
    estrutural = cashflow.estruturais(snapshot.motivos, {"realizacao_passada_desconhecida"})
    if estrutural:
        return falta({m.codigo: [] for m in estrutural})

    limite = centavos(reserva)
    eventos = [*snapshot.ocorrencias, *snapshot.conferencia, *extra]
    base, pior = cashflow.pior_caso_do_risco(snapshot, eventos)
    dia = trajetoria_com_hoje(snapshot.hoje, base, pior, dias, limite)["worst_day"]
    if dia is not None and dia["saldo_projetado"] < limite:
        return saida("risco_identificado", data=dia["date"], valor=dia["saldo_projetado"])

    # Sem risco no pior caso a favor. Se as dúvidas contra (saída que pode já ter saído a mais, entrada que
    # pode não ter vindo) o criariam, o veredito depende delas: pergunta em vez de concluir.
    contra = [e for e in eventos if e.fonte != FONTE_VARIAVEL
              and not (e.direcao == "entrada" and e.realizacao == "a_conferir")]
    primeiro = next((p["date"] for p in trajetoria_com_hoje(snapshot.hoje, snapshot.base, contra, dias, limite)
                     ["trajectory"] if p["abaixo_do_limite"]), None)
    if primeiro is None:
        return saida("sem_risco_identificado")
    grupos: dict[str, list] = {}
    for m in snapshot.motivos:  # ajuste de conciliação a favor entrou só no pior caso
        if (m.codigo in ("conciliacao_a_conferir", "declaracao_bancaria_a_conferir")
                and m.efeito_quantificado is not None and m.efeito_quantificado > 0):
            grupos.setdefault(m.codigo, [])
    for e in sorted(eventos, key=lambda e: e.data or snapshot.hoje):
        if (e.incluida and e.data is not None and e.valor is not None and e.realizacao != "realizada"
                and e.data.isoformat() <= primeiro and e.fonte != FONTE_VARIAVEL
                and (cashflow.saida_duvidosa(e) or e.direcao == "entrada" and e.realizacao == "a_conferir")):
            grupos.setdefault(_duvida(e), []).append({"nome": e.nome, "data": e.data.isoformat()})
    return falta(grupos)
