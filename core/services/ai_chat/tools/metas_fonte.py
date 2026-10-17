"""
core/services/ai_chat/tools/metas_fonte.py — de onde a meta nova acompanha o dinheiro (PR C4), para `metas_escrita.py`.

Sem texto: "Tudo investido" (o padrão do painel). Com texto: IGUALDADE (nunca contenção) com as opções do PRÓPRIO
usuário, as mesmas do painel: tudo investido, cada banco e cada conta em reais. Primeiro o rótulo exato; se nada casar,
o nome da conta ou o do banco dela. Mais de uma opção em qualquer camada: pergunta. Sem investimentos no plano só
sobram as contas. Não achou, ambíguo ou sem plano e sem fonte: `Recusa` com a lista das opções.
"""
from __future__ import annotations

from typing import Any

from core.services.plan_service import plan_gate_ok
from utils_text import normalize_text

_INVESTIDO = "acompanhando tudo que você tem investido"


class Recusa(Exception):
    """Pedido que não dá: a mensagem já é a frase para o usuário."""


def chave(nome: Any) -> str:
    return normalize_text(str(nome or "")) or str(nome or "").strip().casefold()  # nome só de emoji normaliza para ""


def fonte(user_id: int, texto: Any):
    """`(Fonte, frase)`. Sem texto: "Tudo investido" (o padrão do painel). Com texto: IGUALDADE com as
    opções do PRÓPRIO usuário (as do painel: tudo investido, cada banco, cada conta em reais)."""
    from api.v2.contas import contas
    from api.v2.investido import investido_
    from api.v2.metas import Fonte
    plano, pedida = plan_gate_ok(user_id, "investments"), chave(texto) if texto else ""
    if plano and not pedida:
        return Fonte(tipo="investido"), _INVESTIDO
    opcoes = []  # (rótulo, Fonte, frase, apelidos: só a conta tem — o nome dela e o do banco)
    if plano:
        opcoes.append(("Tudo investido", Fonte(tipo="investido"), _INVESTIDO, ()))
        opcoes += [(f"Investido · {p.banco}", Fonte(tipo="banco", banco=p.banco),
                    f"acompanhando o que você tem investido em {p.banco}", ()) for p in investido_(uid=user_id).por_banco]
    for c in contas(uid=user_id).contas:
        if c.moeda == "BRL":
            r = " · ".join(x for x in (c.instituicao, c.nome) if x) or "Conta"
            opcoes.append((r, Fonte(tipo="conta", conta=c.id), f"acompanhando o saldo da conta {r}",
                           tuple(chave(x) for x in (c.nome, c.instituicao) if x)))
    achou = [o for o in opcoes if pedida and pedida in {chave(o[0]), chave(o[0].removeprefix("Investido · "))}]
    if not achou:  # a tool diz "nome do banco ou da conta": sem rótulo exato, vale o nome da conta ou o do banco
        achou = [o for o in opcoes if pedida and pedida in o[3]]
    if len(achou) == 1:
        return achou[0][1], achou[0][2]
    lista = ", ".join(o[0] for o in opcoes) or "nenhuma conta em reais conectada"
    if not plano and not pedida:
        raise Recusa(f"🐷 Seu plano atual não inclui investimentos. Me diz de qual conta a meta acompanha o saldo: {lista}.")
    raise Recusa(f"🐷 {'Tem mais de uma opção com esse nome' if achou else 'Não achei essa fonte'}. Opções: {lista}. Me diz qual.")
