"""O VALOR do teto: `_statement_timeout_options()` de `core/system_event_log.py`.

A outra metade do assunto — o teto EXERCITADO com a tabela travada (cronômetro,
lock, reentrância) — mora em `tests/test_system_event_log_teto.py`; as fixtures
e os helpers de banco dos dois moram em `tests/_system_event_log_helpers.py`.

Aqui se mede o que a env `SYSTEM_EVENT_LOG_TIMEOUT_MS` faz com o `options`, e o
critério é sempre o mesmo: valor que o Postgres RECUSA derruba o connect inteiro
e faz o módulo perder 100% dos registros em silêncio; valor que ele ACEITA mas
não protege (teto de dias) faz o mesmo estrago sem o erro. Por isso o intervalo
fecha dos dois lados, e por isso todo caso tem uma metade que GRAVA de verdade —
asserção só sobre a string do `options` nunca veria o connect ser recusado.

CONTROLE NEGATIVO DO GRUPO: tire o `options=_statement_timeout_options()` dos
DOIS `psycopg.connect` de `core/system_event_log.py`. VERMELHO AQUI, um só:
`test_options_chega_no_connect`. A MESMA injeção derruba outros três no arquivo
irmão (`test_insert_com_tabela_travada_desiste_dentro_do_teto`,
`test_leitura_com_tabela_travada_desiste_dentro_do_teto` e
`test_warning_do_psycopg_nao_reentra_no_handler`) — 4 somando os dois arquivos.

CONTROLE NEGATIVO DO TETO SUPERIOR (injeção SEPARADA, e é a que discrimina o
conserto desta rodada): em `_statement_timeout_options`, troque
`if not _PISO_MS <= ms <= _TETO_MAX_MS` por `if ms < _PISO_MS`. VERMELHOS:
`test_valor_sem_sentido_volta_ao_default[2147483648]` e
`[99999999999999999999]`, e só eles — os outros cinco valores do parametrize
continuam verdes, que é o que separa "fechou o lado de cima" de "passou a
recusar tudo".

CONTROLE POSITIVO: `test_valor_no_limite_superior_e_obedecido`. Sem ele, o grupo
passaria num helper que joga fora todo valor grande e devolve sempre o default —
uma env que não configura mais nada é pior que a env fora de faixa.
"""
from __future__ import annotations

import psycopg
import pytest

from core.system_event_log import (
    _TETO_MAX_MS,
    _statement_timeout_options,
    log_system_event_sync,
    recent_event_exists,
)

from _system_event_log_helpers import (  # noqa: F401  (fixtures autouse)
    EVENTO_DEFAULT,
    EVENTO_OPTIONS,
    TETO_PADRAO_OPTIONS,
    _limpa,
    _linhas,
    sem_env_de_teto,
    tabelas_admin,
)


@pytest.mark.parametrize("valor", ["0", "-1", "abacaxi", "", "50",
                                   "2147483648", "99999999999999999999"])
def test_valor_sem_sentido_volta_ao_default(valor, monkeypatch, user_id):
    """`"0"` é o que mais importa: no Postgres `statement_timeout=0` significa
    SEM LIMITE, então obedecer a env INVERTERIA o sentido do parâmetro —
    "desligado" na cabeça de quem configura viraria "sem teto nenhum". `-1` o
    servidor recusa (o connect inteiro falharia) e `"50"` está abaixo do piso de
    100ms, onde nem o INSERT em tabela livre cabe.

    O intervalo tem DOIS lados: `"2147483648"` é o primeiro valor que o Postgres
    recusa no connect ("value exceeds integer range"), e com ele obedecido o
    módulo perdia 100% dos registros com a suíte inteira verde — é a metade (b)
    abaixo que pega isso. `"99999999999999999999"` é o mesmo defeito com um
    inteiro que nem cabe em 64 bits.

    Duas metades: (a) o helper devolve o default; (b) com a tabela livre a linha
    AINDA é gravada — um valor que o servidor recusasse derrubaria o connect."""
    monkeypatch.setenv("SYSTEM_EVENT_LOG_TIMEOUT_MS", valor)
    assert _statement_timeout_options() == TETO_PADRAO_OPTIONS

    try:
        log_system_event_sync("warning", EVENTO_DEFAULT, "mensagem",
                              source="teste", user_id=user_id)
        assert _linhas(EVENTO_DEFAULT) == 1
    finally:
        _limpa(EVENTO_DEFAULT)


def test_valor_no_limite_superior_e_obedecido(monkeypatch):
    """Controle POSITIVO do lado de cima: NO limite a env ainda manda. Sem ele,
    o caso acima passaria num helper que joga fora tudo que é grande — e aí a
    env deixaria de configurar o que ela existe para configurar."""
    monkeypatch.setenv("SYSTEM_EVENT_LOG_TIMEOUT_MS", str(_TETO_MAX_MS))
    assert _statement_timeout_options() == f"-c statement_timeout={_TETO_MAX_MS}ms"


def test_options_chega_no_connect(monkeypatch, user_id):
    """O `options` não basta existir no helper: tem de chegar no `connect`.
    Espiã no molde de `git show b113e9c:tests/test_log_system_event_timeout_ms.py`."""
    vistos: list[dict] = []
    real = psycopg.connect

    def espia(url, **kw):
        vistos.append(kw)
        return real(url, **kw)

    monkeypatch.setattr(psycopg, "connect", espia)
    try:
        log_system_event_sync("warning", EVENTO_OPTIONS, "mensagem",
                              source="teste", user_id=user_id)
        assert vistos and vistos[0].get("options") == TETO_PADRAO_OPTIONS, vistos
        assert vistos[0].get("connect_timeout") == 2, vistos

        vistos.clear()
        assert recent_event_exists(EVENTO_OPTIONS, user_id) is True, \
            "o caminho de leitura parou de encontrar a linha"
        assert vistos and vistos[0].get("options") == TETO_PADRAO_OPTIONS, vistos
    finally:
        _limpa(EVENTO_OPTIONS)
