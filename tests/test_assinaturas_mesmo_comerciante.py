"""Dois serviços do mesmo comerciante cobrando no mesmo período (iCloud e Apple
Music, ambos "APPLE.COM/BILL") saem separados; grupos sequenciais continuam um
item só (`test_reajuste_partido_em_dois_grupos_vira_um_item`). Banco real."""
from datetime import date
from decimal import Decimal

from _apoio_assinaturas import HOJE, conta, mensais, rp, semeia
from core.services.assinaturas import listar_assinaturas
from db.of_recurring import marcar


def _apple(uid):
    icloud = mensais("ic", [-14.9] * 3, desc="APPLE.COM/BILL")
    music = mensais("am", [-21.9] * 3, ultima=date(2026, 9, 12), desc="APPLE.COM/BILL")
    semeia(uid, [conta("acc-1", icloud + music, tipo="CREDIT")],
           [rp("APPLE.COM/BILL", -14.9, icloud), rp("APPLE.COM/BILL", -21.9, music)])


def test_dois_servicos_simultaneos_saem_separados(user_id):
    _apple(user_id)
    lista = listar_assinaturas(user_id, HOJE)
    assert [(x["chave"], x["valor"], x["valor_anterior"], x["meses"]) for x in lista["servicos"]] == [
        ("apple com bill", Decimal("21.9"), None, 3), ("apple com bill", Decimal("14.9"), None, 3)]
    assert (lista["total_mensal"], lista["chaves"]) == (Decimal("36.8"), ["apple com bill"])


def test_ignorar_a_chave_esconde_os_dois(user_id):
    _apple(user_id)
    marcar(user_id, "apple com bill", "ignorar")
    lista = listar_assinaturas(user_id, HOJE)
    assert (lista["servicos"], lista["outras"], lista["total_mensal"]) == ([], [], 0)
    assert lista["chaves"] == ["apple com bill"]  # continua marcável (o "desfazer")
