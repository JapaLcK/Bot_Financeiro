"""O `_pix_worker` do lifespan CHAMA as três varreduras — o par do
`test_table_cleanup.py::test_processo_do_app_liga_a_poda_no_lifespan`.

**Este arquivo existe por um defeito real, não por simetria.** No PR 1b-B,
`db/webhook_outbox.py::purgar_payloads_antigos` nasceu com 239 linhas de teste
(`tests/test_pix_outbox_purga.py`) e **zero chamadores de produção**:
`RETENCAO_OUTBOX_DIAS = 7` nunca executava, `purged_at` nunca era carimbado, e a
guarda `and purged_at is null` de `registrar_falha` protegia uma coluna que
ficava nula para sempre. É literalmente o mesmo defeito que produziu o
`test_processo_do_app_liga_a_poda_no_lifespan`: três funções de poda existiam e
ninguém as chamava.

O que aqueles 239 testes medem é a QUERY. O que este mede é o CHAMADOR — e as
duas coisas precisam existir juntas, senão a próxima refatoração desliga a
varredura em silêncio e a suíte inteira continua verde.

**Por que não se testa a corrotina direto:** `_pix_worker` é uma closure dentro
do `lifespan` (`frontend/finance_bot_websocket_custom.py`), então não há como
chamá-la de um teste. E ler o arquivo procurando o nome da função não mede nada
(CLAUDE.md §3). Sobra subir o processo — o harness é `tests/_lifespan_probe.py`.

MUTAÇÕES MEDIDAS (uma a uma, rodadas neste arquivo):

  * apague a linha `purgar_retencao` do laço do `_pix_worker` →
    `{"purga": false}` → VERMELHO;
  * apague `asyncio.create_task(_pix_worker(), name="pix_worker")` da lista de
    tarefas do lifespan → os TRÊS ficam `false` → VERMELHO.

CEGUEIRA DECLARADA: as varreduras são substituídas por marcadores, então isto
prova que o laço as CHAMA — não o que elas fazem no banco. Quem mede o efeito é
`tests/test_pix_outbox_purga.py` (a outbox) e `tests/test_pix_privacidade.py`
(a re-zeragem do rastreio órfão).
"""

from _lifespan_probe import sondar

# O laço inteiro: o dreno de 60 s e as duas passadas de 24 h. Os três juntos
# porque a asserção interessante é a do CONJUNTO — cobrir só a purga deixaria
# passar um `_pix_worker` que perdeu o dreno.
_ALVOS = {
    "dreno": "core.services.pix_sweeps:drenar_pendentes",
    "saga": "core.services.pix_sweeps:reconciliar_saga",
    "purga": "core.services.pix_sweeps:purgar_retencao",
}


def test_processo_do_app_liga_as_varreduras_do_pix_no_lifespan():
    """CONTRATO: um processo que serve o `app` com RUN_BACKGROUND_TASKS=1 chama
    o dreno, a reconciliação da saga E a purga de retenção na primeira volta.

    `proxima_saga` nasce em `now()`, então a primeira volta já entra no ramo das
    24 h — é por isso que os três cabem numa sonda só, sem esperar um dia.
    """
    resultado, diagnostico = sondar(_ALVOS)
    assert resultado, f"subprocesso não chegou ao fim:\n{diagnostico}"
    assert resultado == {"dreno": True, "saga": True, "purga": True,
                         "do_disco": []}, (
        "varredura do Pix que existe e ninguém chama (o `_pix_worker` perdeu a "
        "linha, ou o lifespan perdeu o `create_task`), OU o `.env` do disco "
        f"entrou e as tarefas de fundo subiram com credencial viva.\n{diagnostico}"
    )
