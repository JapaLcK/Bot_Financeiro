"""A fronteira de confiança do nome de cartão (#323, segunda rodada do P1).

`credit_cards.name` é `text` sem restrição e `create_card` só recusava nome
VAZIO. Quem entrava pela rota HTTP já batia no limite de 80 caracteres, mas o
bot e a IA chamam `db.create_card` direto — e nome grande guardado hoje é CPU
gasta em toda resposta de pendência de crédito amanhã (o teto de leituras de
`core/handlers/credit.py` saía do maior nome do próprio usuário).

`db.cards.validate_card_name` é a fonte única do limite (§0.7): as duas rotas
passaram a importá-lo em vez de repetir o 80, e `core/handlers/credit.py`
deriva dele o teto em tokens. A coerência entre os dois está medida em
`test_pendencia_credito_leituras_limite.py::test_o_teto_em_tokens_cobre_todo_nome_que_a_fronteira_aceita`.

CONTROLE NEGATIVO V — tire o `if len(n) > MAX_CARD_NAME_LEN` de
`validate_card_name`. VERMELHOS:

    test_create_card_recusa_nome_acima_do_teto
    test_renomear_recusa_nome_acima_do_teto

CONTROLE POSITIVO — `test_nome_real_longo_e_criado_e_ainda_casa_na_resposta`:
sem ele o grupo passaria numa validação que recusa TUDO, que é pior que o bug.

Rodar:  .venv/bin/python -m pytest tests/test_card_name_limite.py -q
"""

import pytest

import db
from _pendencia_credito_helpers import novo_uid
from core.handlers.credit import _card_name_da_resposta
from db.cards import (MAX_CARD_NAME_LEN, get_or_create_open_finance_card,
                      update_card_meta, validate_card_name)

def _contas_of(uid: int) -> tuple[int, int]:
    """Duas contas de crédito do Open Finance — a FK de `credit_cards` exige
    que elas existam de verdade."""
    from db.connection import get_conn
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "insert into open_finance_connections (user_id, provider, "
                "provider_item_id, status, institution_id, institution_name) "
                "values (%s, 'pluggy', %s, 'UPDATED', '612', 'Nubank') "
                "returning id",
                (uid, f"item-{uid}"),
            )
            con = cur.fetchone()["id"]
            ids = []
            for i in (1, 2):
                cur.execute(
                    "insert into open_finance_accounts (connection_id, "
                    "provider_account_id, name, type) values (%s, %s, 'Cartao', "
                    "'CREDIT') returning id",
                    (con, f"acc-{i}"),
                )
                ids.append(cur.fetchone()["id"])
        conn.commit()
    return ids[0], ids[1]


_NO_LIMITE = "N" * MAX_CARD_NAME_LEN
_ACIMA = "N" * (MAX_CARD_NAME_LEN + 1)


def test_create_card_recusa_nome_acima_do_teto():
    uid = novo_uid()
    with pytest.raises(ValueError, match="nome_muito_longo"):
        db.create_card(uid, _ACIMA, 10, 17)
    assert db.list_cards(uid) == []


def test_create_card_aceita_o_nome_exatamente_no_teto():
    """O limite é inclusivo — off-by-one aqui vira recusa de nome legítimo."""
    uid = novo_uid()
    card_id = db.create_card(uid, _NO_LIMITE, 10, 17)
    assert db.get_card_by_id(uid, card_id)["name"] == _NO_LIMITE


def test_renomear_recusa_nome_acima_do_teto():
    """Renomear escreve a MESMA coluna: validar só na criação deixaria a porta
    aberta por um `PATCH /cards/{uid}/{id}`."""
    uid = novo_uid()
    card_id = db.create_card(uid, "Nubank", 10, 17)
    with pytest.raises(ValueError, match="nome_muito_longo"):
        update_card_meta(uid, card_id, name=_ACIMA)
    assert db.get_card_by_id(uid, card_id)["name"] == "Nubank"


def test_nome_vazio_continua_recusado():
    with pytest.raises(ValueError, match="vazio"):
        validate_card_name("   ")


def test_open_finance_apara_em_vez_de_recusar():
    """Terceiro caminho de escrita, e o único que não pode levantar: o nome vem
    do provedor durante o sync e abortar a importação por um campo cosmético
    seria pior. O que se garante é que o nome FINAL cabe no teto, sufixo de
    desempate incluído."""
    uid = novo_uid()
    acc1, acc2 = _contas_of(uid)
    card_id = get_or_create_open_finance_card(uid, acc1, "Banco " * 200, None)
    nome = db.get_card_by_id(uid, card_id)["name"]
    assert len(nome) <= MAX_CARD_NAME_LEN, len(nome)
    assert nome.startswith("Banco Banco")

    # Segunda conta OF com o MESMO nome do provedor: o desempate acrescenta
    # sufixo, e o resultado tem de continuar dentro do teto.
    outro = get_or_create_open_finance_card(uid, acc2, "Banco " * 200, None)
    nome2 = db.get_card_by_id(uid, outro)["name"]
    assert outro != card_id
    assert len(nome2) <= MAX_CARD_NAME_LEN, (nome2, len(nome2))


def test_nome_real_longo_e_criado_e_ainda_casa_na_resposta():
    """POSITIVO do grupo: o nome mais longo que aparece de verdade (29
    caracteres, 7 tokens) passa pela fronteira E continua casando numa resposta
    de pendência, com o prefixo conversacional inteiro."""
    uid = novo_uid()
    card_id = db.create_card(uid, "Cartao Do Banco Do Brasil S A", 10, 17)
    assert _card_name_da_resposta(
        uid, "a fatura do cartao do banco do brasil s a") == card_id


# ─────────────────────────────────────────────────────────────────────────────
# Terceira rodada (#323): as duas REGRESSÕES que a validação acima criou.
#
# CONTROLE NEGATIVO W — tire o `if recusa: inferred_name = None` de
# `start_card_create_flow` E o `_recusa_nome_longo` do step
# `duplicate_card_name` (`core/handlers/credit.py`). VERMELHOS:
#
#     test_criar_cartao_com_nome_longo_recusa_na_entrada_e_deixa_responder
#     test_criar_cartao_inline_com_nome_longo_nao_devolve_erro_cru
#     test_substituto_longo_no_duplicado_recusa_e_mantem_a_pendencia
#
# CONTROLE NEGATIVO X — as duas metades do P2 são medidas SEPARADAMENTE, em
# `get_or_create_open_finance_card` (`db/cards.py`):
#   X1) volte o aparo com folga na query de adoção
#       (`full_name` → `full_name[:MAX_CARD_NAME_LEN - 15]`).  VERMELHO:
#           test_open_finance_adota_o_cartao_manual_de_nome_longo
#   X2) volte o primeiro candidato para o base aparado
#       (`for cand in (full_name, ...` → `(base_name, ...`).  VERMELHO:
#           test_open_finance_desempata_nome_longo_dentro_do_teto
#
# CONTROLE POSITIVO do grupo — cada um dos cinco continua o caminho legítimo
# depois da recusa (nome curto criado, segundo cartão criado, cartão adotado).
# Sem isso o grupo passaria num código que recusa/duplica tudo.
# ─────────────────────────────────────────────────────────────────────────────

from _pendencia_credito_helpers import diga  # noqa: E402
from conftest import promote_to_pro  # noqa: E402

_LONGO = "N" * 100
_SETENTA = "B" * 70


def test_criar_cartao_com_nome_longo_recusa_na_entrada_e_deixa_responder():
    """`criar cartão <100 chars>` sem dias: o nome inferido pulava o step `name`
    e só quebrava no `create_card`, DOIS steps depois — e o `except` de lá só
    pega `PlanLimitExceeded`."""
    uid = novo_uid()
    r1 = diga(uid, f"criar cartão {_LONGO}")
    assert "muito longo" in r1, r1
    assert str(MAX_CARD_NAME_LEN) in r1, r1
    assert db.list_cards(uid) == []

    # POSITIVO: a pendência ficou no step `name` — ele responde e o fluxo anda.
    assert "fecha" in diga(uid, "Nubank").lower()
    diga(uid, "10")
    assert "registrado com sucesso" in diga(uid, "17")
    assert [c["name"] for c in db.list_cards(uid)] == ["Nubank"]


def test_criar_cartao_inline_com_nome_longo_nao_devolve_erro_cru():
    """O caminho `fecha X vence Y` criava na hora dentro de um `except
    Exception` genérico: o usuário via o `nome_muito_longo:100` do banco."""
    uid = novo_uid()
    r = diga(uid, f"criar cartao {_LONGO} fecha 10 vence 17")
    assert "muito longo" in r, r
    assert "nome_muito_longo" not in r, r
    assert db.list_cards(uid) == []


def test_substituto_longo_no_duplicado_recusa_e_mantem_a_pendencia():
    """Step `duplicate_card_name`: o substituto é texto livre e ia direto pro
    `create_card` com os dias já no payload."""
    uid = promote_to_pro(novo_uid())   # o Gratis para em 1 cartão, e o teste é do NOME
    db.create_card(uid, "Nubank", 10, 17)
    assert "Já existe" in diga(uid, "criar cartao Nubank fecha 10 vence 17")

    r = diga(uid, _LONGO)
    assert "muito longo" in r, r
    assert "nome_muito_longo" not in r, r

    # POSITIVO: a pendência continua de pé no mesmo step, com os dias.
    assert "registrado com sucesso" in diga(uid, "Inter")
    assert sorted(c["name"] for c in db.list_cards(uid)) == ["Inter", "Nubank"]


def test_open_finance_adota_o_cartao_manual_de_nome_longo():
    """Nome de 70 caracteres que casa um cartão MANUAL: o aparo com folga de
    sufixo cortava 15 caracteres ANTES da query de adoção, ela não achava nada
    e o sync inseria um SEGUNDO cartão encurtado, derrotando a reconciliação."""
    uid = novo_uid()
    acc1, _ = _contas_of(uid)
    manual = db.create_card(uid, _SETENTA, 10, 17)
    adotado = get_or_create_open_finance_card(uid, acc1, _SETENTA, None)
    assert adotado == manual
    assert [c["name"] for c in db.list_cards(uid)] == [_SETENTA]


def test_open_finance_desempata_nome_longo_dentro_do_teto():
    """A folga do sufixo continua valendo — mas só ao montar candidato NOVO."""
    uid = novo_uid()
    acc1, acc2 = _contas_of(uid)
    primeiro = get_or_create_open_finance_card(uid, acc1, _SETENTA, None)
    assert db.get_card_by_id(uid, primeiro)["name"] == _SETENTA  # nome INTEIRO

    segundo = get_or_create_open_finance_card(uid, acc2, _SETENTA, None)
    assert segundo != primeiro
    nome2 = db.get_card_by_id(uid, segundo)["name"]
    assert nome2 != _SETENTA
    assert len(nome2) <= MAX_CARD_NAME_LEN, (nome2, len(nome2))
