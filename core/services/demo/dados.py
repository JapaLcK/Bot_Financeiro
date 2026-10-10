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
    "renda_mensal": "5200.00",
    "saldo_conta_corrente": "1840.50",
    # Pré-calculados (o modelo não faz a conta): este mês até o dia 20 × 30/20; renda − projetado.
    "gasto_projetado_fim_do_mes": "5072.40",
    "sobra_projetada_fim_do_mes": "127.60",
    "cartao": {"nome": "Cartão principal", "fatura_aberta": "1260.00", "vencimento": "dia 10"},
    "caixinha": {"nome": "Reserva de emergência", "guardado": "3200.00", "meta": "10000.00"},
    "assinaturas": [
        {"nome": "Streaming de vídeo", "valor_mensal": "39.90"},
        {"nome": "Streaming de música", "valor_mensal": "21.90"},
        {"nome": "Academia", "valor_mensal": "119.90"},
        {"nome": "Armazenamento na nuvem", "valor_mensal": "9.90"},
    ],
    "contas_a_pagar": [
        {"nome": "Internet", "valor": "99.90", "vencimento": "dia 12"},
        {"nome": "Energia", "valor": "180.00", "vencimento": "dia 15"},
    ],
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
            "rotulo": "mês passado",
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
