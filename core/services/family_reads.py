"""core/services/family_reads.py — Plano Família: o ÚNICO caminho por onde o titular lê dado financeiro de um membro.

Ordem: resolve o ref (só membro ativo do grupo do titular) -> confere o consentimento (nada é gravado nem consultado
se faltar) -> consulta com o user_id do MEMBRO -> `log_read` confere de novo sob o lock do grupo e grava; só então o dado
sai. Revogação no meio da leitura: no_consent e o dado é descartado. Falha ao gravar: exceção e o dado não sai.
Sem cache: cada leitura refaz os dois SELECTs. Rota nova que leia dado de membro sem passar por aqui é vazamento.
"""
from typing import Callable, TypeVar

from db import family_consents as fc
from db.family_consents import Escopo
from db import family_views as views
from db.family import FamilyError
from utils_date import day_tz

T = TypeVar("T")


def ler_do_membro(viewer_uid: int, ref: str, escopo: Escopo, mes: str,
                  ler: Callable[[int], T]) -> T:
    """`mes` = "AAAA-MM" já validado. Erros (FamilyError): not_member (404), no_consent (403), month_out_of_range (422)."""
    banco = fc.ESCOPO_BANCO[escopo]
    membro = views.member_user_id(viewer_uid, ref)
    if membro is None:
        raise FamilyError("not_member")
    permissao = fc.active_consent(viewer_uid, membro, banco)
    if permissao is None:
        raise FamilyError("no_consent")
    entrou = day_tz(permissao["joined_at"])
    if mes < f"{entrou.year:04d}-{entrou.month:02d}":  # só vale a partir do mês em que entrou na família
        raise FamilyError("month_out_of_range")
    dado = ler(membro)
    fc.log_read(permissao["group_id"], viewer_uid, membro, banco, f"{escopo}:{mes}")
    return dado
