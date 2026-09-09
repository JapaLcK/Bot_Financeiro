"""O quarto mecanismo da família #310/#317: a STRING que o Postgres não guarda.

Topo objeto, campo do tipo certo, string bem formada em JSON — e mesmo assim
500. Duas formas: **NUL** (`\\u0000`), que `text` não aceita e que o `jsonb`
recusa (`unsupported Unicode escape sequence`); e **surrogate solitário**
(`\\ud800` alto, `\\udc00` baixo, sem par), que não é UTF-8 válido e faz o
psycopg estourar ao codificar o parâmetro. Alto e baixo ficam separados como
`1e400` × `Infinity` no irmão `test_corpo_json_nao_finito.py`: faixas
diferentes, e um conserto que só cubra uma deixa a outra em 500.

No webhook da Pluggy isso não é um 500 qualquer — ela REENVIA em erro, então é
500 em laço.

CONTROLE NEGATIVO do grupo, os TRÊS, medidos. Sem contagem de vermelhos aqui
de propósito (§2 — ela envelhece em silêncio, e já envelheceu uma vez quando
este arquivo ganhou testes): quem discrimina está NOMEADO.
  1. `limpa_para_pg` → identidade nos dois chamadores (`lambda v: v`): a matriz
     do webhook, a auditoria e o `note` do saque.
  2. caminhada RECURSIVA em vez de iterativa: `[n1500]`, `[n5000]` e o
     `test_limpa_para_pg_e_iterativo` da unidade.
  3. saneador DESTRUTIVO (`_limpa_str` → `""` quando a string tem veneno): todo
     teste que só mede STATUS continua verde — inclusive os desta família. Quem
     pega é o `test_corpo_venenoso_e_lido_de_volta_saneado`, os do `note` (que
     inspecionam o argumento) e a unidade em `tests/test_pg_text.py`.
Injetar num caso que hoje é verde (`😀`, acento) não discriminaria nada.

CONTROLES POSITIVOS: `test_controle_positivo_corpo_limpo_grava_identico` (o
corpo válido é LIDO DE VOLTA do banco — o 200 sozinho não prova isso), os dois
de profundidade e `test_controle_positivo_note_limpo_inalterado`. Os 3 formatos
de `itemId` e os 6 de `note` continuam cobertos por
`test_corpo_json_campo_filho.py` — não são copiados para cá. A unidade do
`limpa_para_pg` mora em `tests/test_pg_text.py`.
"""
import json
import re

import pytest

import core.admin_dashboard as admin_dashboard
import frontend.routes.open_finance as open_finance_routes
from _corpo_json_helpers import (  # noqa: F401  (fixtures autouse)
    INEXISTENTE,
    _admin_client,
    _admin_tables,
    _post_bruto,
    _pluggy_post,
    _pluggy_post_texto,
    afiliado_stub,
    configured_admin,
)

NUL = "\x00"
SURR_ALTO = "\ud800"
SURR_BAIXO = "\udc00"
VENENOS = [NUL, SURR_ALTO, SURR_BAIXO]
VENENO_IDS = ["nul", "surrogate_alto", "surrogate_baixo"]


# O log_system_event de VERDADE, capturado no import: a fixture
# `configured_admin` troca as duas referências (módulo e rota) por noop, e o
# teste de auditoria precisa do original de volta.
_log_real = admin_dashboard.log_system_event


def _linhas_de_auditoria(marcador: str) -> int:
    from db.connection import get_conn

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "select count(*) as n from system_event_logs "
                "where event_type='pluggy_webhook_received' and details->>'item_id'=%s",
                (marcador,),
            )
            return dict(cur.fetchone())["n"]


def _veneno(v: str) -> str:
    """Cercado de texto legítimo: prova que o resto da string sobrevive e que o
    campo não vira vazio (vazio passaria por vários asserts de graça)."""
    return f"AAA{v}BBB"


# --------------------------------------------------------------------------
# Grupo 1 — a matriz do webhook: 3 formas × 9 posições, todas 200
# --------------------------------------------------------------------------
# Chave, array e array aninhado são obrigatórios aqui: separam "saneei o valor"
# de "saneei a CATEGORIA".
#
# `event: "item/error"` + `itemId` em TODOS os corpos não é decoração: é o que
# leva o corpo inteiro ao `Jsonb(raw)` de
# update_pluggy_open_finance_item_status. A primeira versão deste arquivo usava
# `event: "ping"`, e aí veneno em campo não lido, em chave e em array não
# alcançava sink nenhum (`status_by_event.get("ping")` é None e a fixture stuba
# o log_system_event): MEDIDO, com `ping` a maior parte da matriz seguia VERDE
# no controle negativo — tautológica. O item_id é inexistente de propósito: o UPDATE
# casa 0 linhas, mas o psycopg serializa o parâmetro antes de saber disso, e é
# aí que o 500 nasce.

ITEM_LIVRE = "item-inexistente-317"


def _corpos(v: str) -> dict:
    p = _veneno(v)
    base = {"event": "item/error", "itemId": ITEM_LIVRE}
    return {
        "campo_nao_lido": {**base, "extra": p},
        "chave_do_topo": {**base, p: 1},
        "item_id_camel": {**base, "itemId": p},
        "item_id_snake": {"event": "item/error", "item_id": p},
        "item_ponto_id": {"event": "item/error", "item": {"id": p}},
        "array_simples": {**base, "lista": [p]},
        "array_3_niveis": {**base, "fundo": [[[p]]]},
        "chave_aninhada": {**base, "obj": {"n": {p: 1}}},
        # A única posição com outro event_name: é o `any(%s)` de
        # delete_open_finance_transactions (psycopg adapta a lista como text[]),
        # sink que o Jsonb(raw) não cobre.
        "transaction_ids": {
            "event": "transactions/deleted", "itemId": ITEM_LIVRE,
            "transactionIds": [p],
        },
    }


POSICOES = list(_corpos(NUL))


@pytest.mark.parametrize("veneno", VENENOS, ids=VENENO_IDS)
@pytest.mark.parametrize("posicao", POSICOES)
def test_string_venenosa_no_webhook_nao_da_500(posicao, veneno):
    r = _pluggy_post(_corpos(veneno)[posicao])
    assert r.status_code == 200, r.text
    assert r.json() == {"received": True}


@pytest.mark.parametrize("veneno", VENENOS, ids=VENENO_IDS)
def test_nome_do_evento_venenoso_nao_perde_a_auditoria(monkeypatch, veneno):
    """A 10ª posição — o nome do evento — e o único sink que NÃO dá 500.

    `log_system_event` monta `message` (param text) e `Jsonb(details)` com o
    event_name; com veneno o insert levanta e o `except Exception: print(...)`
    engole: sem 500 e sem linha de auditoria — perda SILENCIOSA, que status
    nenhum mede. Por isso o stub de `configured_admin` é desfeito aqui.
    """
    monkeypatch.setattr(open_finance_routes, "log_system_event", _log_real)
    marcador = f"{ITEM_LIVRE}-audit-{veneno.encode('unicode_escape').decode()}"
    r = _pluggy_post({"event": f"ping{_veneno(veneno)}", "itemId": marcador})
    assert r.status_code == 200, r.text
    assert _linhas_de_auditoria(marcador) == 1, "a linha de auditoria sumiu em silêncio"


# --------------------------------------------------------------------------
# Grupo 2 — controles positivos que LEEM O BANCO DE VOLTA
# --------------------------------------------------------------------------

def _raw_e_status(item_id: str):
    from db.connection import get_conn

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "select raw, status from open_finance_connections "
                "where provider='pluggy' and provider_item_id=%s",
                (item_id,),
            )
            linhas = cur.fetchall() or []
    assert len(linhas) == 1, linhas
    return dict(linhas[0])["raw"], dict(linhas[0])["status"]


def _conexao(user_id: int, item_id: str) -> None:
    import db

    db.save_pluggy_open_finance_item(
        user_id,
        {"id": item_id, "status": "UPDATED", "connector": {"id": 612, "name": "Nubank"}},
    )


def test_controle_positivo_corpo_limpo_grava_identico(user_id):
    """O 200 sozinho não prova nada: um saneador que devolvesse `{}` também
    responderia 200. Aqui o corpo é lido de volta do `raw` jsonb — com acento,
    emoji (que chega como PAR de surrogates escapados e tem de voltar a ser um
    caractere só) e os 4 tipos não-string que a caminhada atravessa sem tocar.
    """
    item_id = f"item-317-limpo-{user_id}"
    _conexao(user_id, item_id)
    corpo = {
        "event": "item/error",
        "itemId": item_id,
        "extra": "pão à vista 😀",
        "n": [1, 2.5, None, True],
    }
    r = _pluggy_post(corpo)
    assert r.status_code == 200, r.text

    raw, status = _raw_e_status(item_id)
    assert raw == corpo, raw
    assert status == "ERROR", status


# Só as 5 posições cujo `itemId` fica LIMPO alcançam a linha do banco para ser
# lida de volta; as 4 com id envenenado não casam com conexão nenhuma de
# propósito, e quem mede isso é o `test_item_id_venenoso_nao_casa_com_id_real`.
POSICOES_RAW = ["campo_nao_lido", "chave_do_topo", "array_simples",
                "array_3_niveis", "chave_aninhada"]


@pytest.mark.parametrize("veneno", VENENOS, ids=VENENO_IDS)
@pytest.mark.parametrize("posicao", POSICOES_RAW)
def test_corpo_venenoso_e_lido_de_volta_saneado(user_id, posicao, veneno):
    """O 200 da matriz é cego a saneador DESTRUTIVO — MEDIDO: com `_limpa_str`
    devolvendo `""` para toda string com veneno, os testes de status desta
    família seguem TODOS verdes. Aqui o corpo envenenado é lido de volta do
    `raw` jsonb, que é para o que o `_veneno()` embrulha em `AAA…BBB`.
    """
    item_id = f"item-317-raw-{posicao}-{veneno.encode('unicode_escape').decode()}-{user_id}"
    _conexao(user_id, item_id)
    r = _pluggy_post({**_corpos(veneno)[posicao], "itemId": item_id})
    assert r.status_code == 200, r.text

    raw, status = _raw_e_status(item_id)
    assert status == "ERROR", status
    # Um saneador que devolvesse `{}` também responderia 200.
    assert raw["event"] == "item/error" and raw["itemId"] == item_id, raw
    # No documento inteiro — valores E chaves, por isso a busca é no dumps:
    # sobrou UMA marca, e entre o AAA e o BBB só há U+FFFD. Vazio reprova
    # (dado destruído) e `AAABBB` também (apagar o veneno inventaria identidade).
    marcas = re.findall("AAA.*?BBB", json.dumps(raw, ensure_ascii=False))
    assert len(marcas) == 1, raw
    assert set(marcas[0][3:-3]) == {"\ufffd"}, repr(marcas[0])


def test_item_id_venenoso_nao_casa_com_id_real(user_id):
    """A razão de SUBSTITUIR por U+FFFD em vez de APAGAR, virada em teste.

    Apagando o NUL, `"<id-real>\\u0000"` viraria `"<id-real>"` e o webhook agiria
    sobre uma conexão de VERDADE que o corpo nunca nomeou — inventar identidade
    é pior que o 500 que se está consertando. `U+FFFD` não aparece em id da
    Pluggy, então "não casa" é garantido.

    MEDIDO: com a variante que apaga, este fica vermelho — junto com os de
    leitura de volta e com `test_payout_note_so_veneno_nao_vira_none`. A matriz,
    que só mede status, não vê; `test_payout_note_venenoso_nao_da_500` também
    não, porque `"AAABBB"` satisfaz os asserts dele.
    """
    item_id = f"item-317-identidade-{user_id}"
    _conexao(user_id, item_id)
    r = _pluggy_post({"event": "item/error", "itemId": item_id + NUL})
    assert r.status_code == 200, r.text

    raw, status = _raw_e_status(item_id)
    assert status == "UPDATED", "o item real foi alterado por um id que não era o dele"
    assert raw is None or raw.get("itemId") != item_id + NUL


# --------------------------------------------------------------------------
# Grupo 3 — profundidade: a caminhada NÃO pode ser recursiva
# --------------------------------------------------------------------------
# MEDIDO: o scanner em C do `json` aceita mais aninhamento que o limite de
# frames do Python. Com walk recursivo, n=1500 e n=5000 (que hoje respondem
# 200) viram 400 dentro do try — ou RecursionError → 500 fora dele. Sem estes
# dois casos a suíte fica VERDE com a versão que regride.

@pytest.mark.parametrize("n", [1500, 5000], ids=["n1500", "n5000"])
def test_profundidade_limpa_continua_200(n):
    texto = '{"event":"ping","d":' + "[" * n + '"ab"' + "]" * n + "}"
    r = _pluggy_post_texto(texto)
    assert r.status_code == 200, r.text


def test_controle_positivo_precedencia_item_id_intacta(monkeypatch):
    """O saneador não pode mexer em `itemId` limpo — é ele que decide de quem é
    a conexão. Os 3 formatos e a precedência entre eles ficam no irmão
    `campo_filho.py::test_controle_positivo_pluggy_item_id_resolve_igual`, que
    continua verde; aqui só o caso com veneno EM OUTRO campo."""
    vistos = []
    monkeypatch.setattr(
        open_finance_routes, "get_connections_by_item_id",
        lambda item_id: vistos.append(item_id) or [],
    )
    r = _pluggy_post({"event": "ping", "itemId": "AAA", "lixo": _veneno(NUL)})
    assert r.status_code == 200, r.text
    assert vistos == ["AAA"]


# --------------------------------------------------------------------------
# Grupo 4 — o irmão fora do webhook: `note` do saque (db/affiliates.py)
# --------------------------------------------------------------------------

ACOES_PAYOUT = [("paid", "mark_payout_paid"), ("reject", "reject_payout")]


def _espiao_payout(monkeypatch, funcao):
    import db.affiliates

    vistos = []
    monkeypatch.setattr(
        db.affiliates, funcao,
        lambda payout_id, note: vistos.append(note) or False,
    )
    return vistos


@pytest.mark.parametrize("acao,funcao", ACOES_PAYOUT, ids=["paid", "reject"])
@pytest.mark.parametrize("veneno", VENENOS, ids=VENENO_IDS)
def test_payout_note_venenoso_nao_da_500(monkeypatch, veneno, acao, funcao):
    """404 (saque inexistente) e não 500 — e o que chega na coluna `text` já
    não tem NUL nem surrogate solitário."""
    vistos = _espiao_payout(monkeypatch, funcao)
    r = _post_bruto(
        _admin_client(),
        f"/admin/api/affiliates/payouts/{INEXISTENTE}/{acao}",
        {"note": _veneno(veneno)},
    )
    assert r.status_code == 404, r.text
    assert len(vistos) == 1, vistos
    assert NUL not in vistos[0]
    vistos[0].encode("utf-8")  # levanta se sobrou surrogate solitário
    assert vistos[0].startswith("AAA") and vistos[0].endswith("BBB"), vistos


@pytest.mark.parametrize("acao,funcao", ACOES_PAYOUT, ids=["paid", "reject"])
def test_payout_note_so_veneno_nao_vira_none(monkeypatch, acao, funcao):
    """`note` que é SÓ veneno: o `.strip()` não tira o U+FFFD, então sobra
    string em vez de None. Registrado de propósito — é o teto do saneamento."""
    vistos = _espiao_payout(monkeypatch, funcao)
    r = _post_bruto(
        _admin_client(),
        f"/admin/api/affiliates/payouts/{INEXISTENTE}/{acao}",
        {"note": NUL},
    )
    assert r.status_code == 404, r.text
    assert vistos == ["�"]


@pytest.mark.parametrize("acao,funcao", ACOES_PAYOUT, ids=["paid", "reject"])
def test_controle_positivo_note_limpo_inalterado(monkeypatch, acao, funcao):
    """Os 6 formatos legítimos moram em `campo_filho.py`; aqui fica o que ele
    não tem — acento e emoji intactos, onde um `encode('ascii','ignore')`
    passaria despercebido."""
    vistos = _espiao_payout(monkeypatch, funcao)
    r = _post_bruto(
        _admin_client(),
        f"/admin/api/affiliates/payouts/{INEXISTENTE}/{acao}",
        {"note": "  pago à mão ✓ 😀  "},
    )
    assert r.status_code == 404, r.text
    assert vistos == ["pago à mão ✓ 😀"]
