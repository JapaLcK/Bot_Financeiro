"""core/services/demo/dados.py — a persona FICTÍCIA do "Testar o Piggy".

Não é dado de ninguém: quem testa pergunta sobre a "Ana", que não existe. Valores
em string decimal (o modelo lê o JSON; o teste soma com Decimal). Meses com rótulo
relativo, sem data real, para o exemplo não envelhecer. Sem marca de banco.
"""
from __future__ import annotations

LIMITE_MSGS = 8  # perguntas por teste; lido por db/demo_funnel.py e pelos textos do wa_demo
# Código do clique (sem I, L, O, U). Fonte única: demo_funnel, wa_demo e o check de demo_sessions.
ALFABETO = "23456789ABCDEFGHJKMNPQRSTVWXYZ"
CODIGO_PADRAO = f"[{ALFABETO}]{{6}}"

PERSONA = {
    "nome": "Ana",
    "hoje": "dia 20",  # referencial: todo vencimento abaixo é depois dele
    "renda_mensal": "5200.00",
    "saldo_conta_corrente": "1840.50",
    # Pré-calculados (o modelo não faz a conta): este mês até o dia 20 × 30/20; renda − projetado.
    "gasto_projetado_fim_do_mes": "5072.40",
    "sobra_projetada_fim_do_mes": "127.60",
    "veredito_do_mes": "fecha no azul, apertado",  # sinal da sobra projetada; compra simulada não muda
    "cartao": {"nome": "Cartão principal", "fatura_aberta": "1260.00", "vencimento": "dia 25"},
    # faltam = meta − guardado; meses_guardando_500 = faltam ÷ 500 arredondado PARA CIMA (13,6 → 14).
    "caixinha": {"nome": "Reserva de emergência", "guardado": "3200.00", "meta": "10000.00",
                 "faltam": "6800.00", "meses_guardando_500": 14},
    "assinaturas": [
        {"nome": "Streaming de vídeo", "valor_mensal": "39.90"},
        {"nome": "Streaming de música", "valor_mensal": "21.90"},
        {"nome": "Academia", "valor_mensal": "119.90"},
        {"nome": "Armazenamento na nuvem", "valor_mensal": "9.90"},
    ],
    "contas_a_pagar": [
        {"nome": "Internet", "valor": "99.90", "vencimento": "dia 22"},
        {"nome": "Energia", "valor": "180.00", "vencimento": "dia 28"},
    ],
    # total = fatura do cartão + contas_a_pagar (1260,00 + 99,90 + 180,00);
    # saldo_apos_pagar_tudo = saldo_conta_corrente − total (1840,50 − 1539,90).
    "contas_a_vencer_este_mes": {
        "itens": [
            {"nome": "Fatura do cartão principal", "valor": "1260.00", "vencimento": "dia 25"},
            {"nome": "Internet", "valor": "99.90", "vencimento": "dia 22"},
            {"nome": "Energia", "valor": "180.00", "vencimento": "dia 28"},
        ],
        "total": "1539.90",
        "saldo_apos_pagar_tudo": "300.60",
        "proxima_receita": "dia 5 do mês que vem (nenhuma receita até o fim do mês)",
    },
    # assinaturas_por_ano = soma de `assinaturas` (191,60) × 12.
    "assinaturas_por_ano": "2299.20",
    # total_mensal = as 4 assinaturas (191,60) + Internet 99,90 + Energia 180,00; anual = × 12.
    "cobrancas_recorrentes": {
        "itens": [
            {"nome": "Streaming de vídeo", "valor_mensal": "39.90"},
            {"nome": "Streaming de música", "valor_mensal": "21.90"},
            {"nome": "Academia", "valor_mensal": "119.90"},
            {"nome": "Armazenamento na nuvem", "valor_mensal": "9.90"},
            {"nome": "Internet", "valor_mensal": "99.90"},
            {"nome": "Energia (valor médio)", "valor_mensal": "180.00"},
        ],
        "total_mensal": "471.50",
        "total_anual": "5658.00",
    },
    # delivery de este mês (até o dia 20): ticket_medio = valor ÷ pedidos (410 ÷ 14);
    # percentual_dos_gastos = valor ÷ total_gastos de este mês (410 ÷ 3381,60).
    "delivery_este_mes": {"valor": "410.00", "pedidos": 14, "ticket_medio": "29.29",
                          "percentual_dos_gastos": "12.1"},
    # O mês passado até o MESMO dia 20: a única base justa para comparar com `este mês`
    # (parcial). variacao = este mês − isto (3381,60 − 2790,00 = +591,60; ÷ 2790 = +21,2%).
    "mes_passado_ate_o_dia_20": {
        "total_gastos": "2790.00",
        "por_categoria": {
            "moradia": "1500.00", "mercado": "560.00", "delivery": "250.00",
            "transporte": "150.00", "assinaturas": "191.60", "lazer": "90.00", "outros": "48.40",
        },
        "variacao_mesmo_periodo": {
            "valor": "591.60", "percentual": "21.2",
            "maiores_altas": {"lazer": "190.00", "delivery": "160.00"},
        },
    },
    "meses": [
        {
            "rotulo": "este mês (até o dia 20)",
            "receitas": "5200.00",
            "total_gastos": "3381.60",
            "por_categoria": {
                "moradia": "1500.00", "mercado": "620.00", "delivery": "410.00",
                "transporte": "230.00", "assinaturas": "191.60", "lazer": "280.00", "outros": "150.00",
            },
        },
        {
            "rotulo": "mês passado (mês inteiro)",
            "receitas": "5200.00",
            "total_gastos": "4091.60",
            "por_categoria": {
                "moradia": "1500.00", "mercado": "890.00", "delivery": "520.00",
                "transporte": "310.00", "assinaturas": "191.60", "lazer": "420.00", "outros": "260.00",
            },
        },
        {
            "rotulo": "retrasado",
            "receitas": "5200.00",
            "total_gastos": "3901.70",
            "por_categoria": {
                "moradia": "1500.00", "mercado": "850.00", "delivery": "480.00",
                "transporte": "290.00", "assinaturas": "181.70", "lazer": "360.00", "outros": "240.00",
            },
        },
    ],
}
