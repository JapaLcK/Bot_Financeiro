"""G5e — as CORRIDAS da adoção de item órfão, e o que sobra depois delas.

Arquivo separado de `test_of_webhook_adopt_dupe.py` porque aquele está a 303
linhas e o teto é 350 (`tests/test_max_lines_python.py`) — não por assunto
diferente. Aqui ficam os entrelaçamentos que acontecem DEPOIS da leitura do
rastro, que é a janela que a leitura movida para junto do `register_item` não
fecha (o "LIMITE CONHECIDO" de `_adota_item_orfao` é a fonte única do texto).

O que cada teste prende:

  • ENTREGA ATRASADA (Codex #313, P1) — a 2ª entrega lê o rastro vazio, a 1ª
    adota e o usuário DESCONECTA antes de ela escrever. Ela chegava ao
    `_salva_item_sob_lock` com `tinha_conexao_propria=False` (honesto: item
    órfão nunca teve conexão), pulava a revalidação de estado e RECRIAVA a
    conexão com sync agendado, na carteira que o usuário acabou de limpar.
  • ABORTO MÚTUO (P0 do Tester, 30/30 rodadas) — duas entregas simultâneas, sem
    gate nenhum. Cada uma grava o rastro antes de qualquer uma pegar o lock,
    cada uma enxerga o rastro da OUTRA e aborta: sobrava rastro COM dono e ZERO
    conexão, estado TERMINAL — a 1ª guarda recusa toda retentativa e o
    `scripts/adotar_items_of_orfaos.py` filtra fora rastro com dono, então o
    usuário ficava com 0 bancos e sem saída pelo produto. O conserto é o aborto
    desfazer a PRÓPRIA reivindicação ainda sob o lock: quem entra depois não vê
    reivindicação nenhuma e adota.
  • PERDER O LOCK (achado do Tester contra a 1ª versão do conserto, e regressão
    contra a `main`) — na intercalação em que quem PEGA o lock é quem aborta, a
    outra entrega leva 503 sem nunca ter tentado escrever. Consertar só o aborto
    sob o lock TROCAVA o dono do estado terminal em vez de fechá-lo: a
    reivindicação da perdedora do lock ficava para trás. O desfazimento vale nos
    DOIS desfechos em que a escrita provadamente não aconteceu.

CONTROLES do grupo (medidos, não deduzidos):
  • negativo (desfazimento): trocar `unregister_item(adocao_registro_id,
    user_id)` por `pass` em `_salva_item_sob_lock` → discrimina
    `test_aborto_mutuo_...`, que falha em "rodada 0: 0 conexões", o P0
    reproduzido; os outros vermelhos caem no rastro que sobra. Sem CONTAR os
    vermelhos: o número envelhece a cada teste novo aqui (CLAUDE.md §2);
  • negativo (desfazimento no 503): `if False and ...` no `if adocao_registro_id
    is not None and not escrita_tentada` do fim de `_grava_reconexao` →
    discrimina `test_quem_perde_o_lock_...`, no assert de rastro
    (`[{'origin': 'webhook_adopt', 'user_id': ...}] == []`). Desligando TAMBÉM
    esse assert, o vermelho seguinte é a retentativa devolvendo `None` — o P0 em
    pessoa, e a razão de os dois asserts ficarem no mesmo teste;
  • negativo (revalidação): `if False and adocao_registro_id is not None:` →
    discrimina `test_entrega_atrasada_...`, que falha em "a entrega atrasada
    adotou", o bug do Codex de volta;
  • o MECANISMO do desfazimento no 503 — o latch do prazo inteiro e a marca que
    separa infra ANTES da escrita de infra DENTRO dela — mudou de arquivo:
    `test_of_webhook_adopt_503.py`, com os controles negativos dele;
  • positivo: o teste do aborto mútuo exige UMA conexão criada. Num código que
    recusasse toda adoção — o risco de uma guarda nova — ele fica vermelho pelo
    mesmo assert. `test_item_created_continua_adotando_o_dono_legitimo`
    (`test_of_webhook_adopt_guards.py`) é o controle positivo do caminho comum.
"""

from __future__ import annotations

import asyncio
import threading

import pytest

import db
import frontend.routes.open_finance as of_routes
from test_of_item_ownership import SEGREDO, _auth, _item_remoto, _webhook, eventos  # noqa: F401
from test_of_webhook_adopt_guards import _limpa_item, _mock_item, _registry, webhook_pluggy  # noqa: F401


def test_entrega_atrasada_nao_ressuscita_o_banco_desconectado(
        user_id, monkeypatch, eventos, webhook_pluggy):
    """A 2ª entrega leu o rastro VAZIO e só chega na escrita depois do disconnect.

    Sem sleep: `user_exists` PRENDE a 1ª a rodar num `threading.Event` (ela está
    num `asyncio.to_thread`, o loop segue livre) já com o rastro lido; a outra
    roda inteira e adota; o usuário desconecta; só então a presa escreve.
    """
    _mock_item(monkeypatch, user_id)
    real, chamadas, chegou, liberar = (of_routes.user_exists, [],
                                       threading.Event(), threading.Event())

    def _preso(uid):
        chamadas.append(uid)
        if len(chamadas) == 1:                      # a 1ª a rodar espera aqui
            chegou.set()
            assert liberar.wait(10), "a outra entrega nunca terminou"
        return real(uid)

    monkeypatch.setattr(of_routes, "user_exists", _preso)

    async def cenario():
        presa = asyncio.create_task(of_routes._adota_item_orfao("z-tarde", "item/created"))
        assert await asyncio.to_thread(chegou.wait, 10), "a 1ª não chegou ao user_exists"
        await of_routes._adota_item_orfao("z-tarde", "item/created")    # adota inteira
        assert len(db.get_connections_by_item_id("z-tarde")) == 1, "pré-condição: adotou"
        db.disconnect_open_finance_connection(user_id)
        liberar.set()
        assert await presa is None, "a entrega atrasada adotou"

    try:
        asyncio.run(cenario())
        assert db.get_connections_by_item_id("z-tarde") == [], \
            "banco REMOVIDO ressuscitou pela entrega atrasada"
        assert webhook_pluggy == ["z-tarde"], f"{webhook_pluggy}: sync re-importa o apagado"
        skips = [e["details"] for e in eventos if e["event"] == "of_webhook_adopt_skipped"]
        assert len(skips) == 1 and "abortada sob o lock" in skips[0]["error"], skips
        # A entrega atrasada apagou a reivindicação DELA e só ela: o rastro da
        # adoção que valeu continua lá, e é ele que recusa a próxima duplicata
        # (senão o conserto do P0 reabriria justamente este bug).
        assert [r["origin"] for r in _registry("z-tarde")] == ["webhook_adopt"], \
            _registry("z-tarde")
    finally:
        db.disconnect_open_finance_connection(user_id)
        _limpa_item("z-tarde")


def test_aborto_mutuo_nao_deixa_o_item_reivindicado_e_sem_conexao(
        user_id, monkeypatch, eventos, webhook_pluggy):
    """Duas entregas do MESMO `item/created` ao mesmo tempo, sem gate nenhum.

    O repro do Tester (`asyncio.gather`), rodado 5 vezes para pegar mais de um
    entrelaçamento: quem chega primeiro à leitura do rastro varia, e o desfecho
    tem de ser o mesmo nos dois casos — UMA conexão, UMA reivindicação viva.

    O que discrimina não é a contagem de rastro, é a de CONEXÃO: com a
    revalidação sozinha (sem o desfazimento) o resultado era `[None, None]` e
    zero conexão em 30/30 rodadas do Tester.
    """
    _mock_item(monkeypatch, user_id)

    async def duas(item_id):
        return await asyncio.gather(
            of_routes._adota_item_orfao(item_id, "item/created"),
            of_routes._adota_item_orfao(item_id, "item/created"))

    for rodada in range(5):
        item = f"z-gather-{rodada}"
        try:
            donos = asyncio.run(duas(item))

            conexoes = db.get_connections_by_item_id(item)
            assert len(conexoes) == 1, (
                f"rodada {rodada}: {len(conexoes)} conexões — 0 é o usuário com "
                "0 bancos e sem saída pelo produto (P0), 2 é o upsert furado")
            assert int(conexoes[0]["user_id"]) == user_id, conexoes
            assert [d for d in donos if d is not None] == [user_id], donos
            # A perdedora apagou a própria reivindicação: rastro com dono só o
            # da que ganhou. Reivindicação abandonada é o que tornava o estado
            # TERMINAL (a 1ª guarda recusa a retentativa e o script one-shot
            # filtra fora rastro com dono).
            assert [r["origin"] for r in _registry(item)] == ["webhook_adopt"], _registry(item)

            # E o item continua respondendo como banco adotado, não como órfão:
            # a duplicata seguinte é recusada por DONO, sem tocar na conexão.
            assert asyncio.run(of_routes._adota_item_orfao(item, "item/created")) is None
            assert len(db.get_connections_by_item_id(item)) == 1
        finally:
            db.disconnect_open_finance_connection(user_id)
            _limpa_item(item)

    # Todo skip tem de ser um dos DOIS desfechos previstos, e nenhum outro
    # (`usuario_inexistente`, erro de escrita): a perdedora ou nem chega a
    # reivindicar (1ª guarda, quando a outra já gravou) ou aborta sob o lock. A
    # proporção entre os dois varia com o escalonamento — por isso não se conta.
    motivos = [(e["details"].get("motivo"), e["details"].get("error", ""))
               for e in eventos if e["event"] == "of_webhook_adopt_skipped"]
    assert motivos and all(m == "rastro_com_dono" or "abortada sob o lock" in err
                           for m, err in motivos), motivos


def test_quem_aborta_sob_o_lock_some_do_rastro(user_id, monkeypatch, eventos, webhook_pluggy):
    """O MECANISMO que tira o estado de terminal, medido sozinho.

    A rival é plantada DEPOIS da 1ª guarda (dentro do `user_exists`, que roda
    entre a leitura do rastro e o `register_item`): é o único jeito de forçar o
    aborto sob o lock sem concorrência de verdade. O que se mede é o que sobra —
    o rastro fica com UMA linha, a da rival, e nenhuma da entrega que abortou.
    Com a reivindicação abandonada de volta ao registry, este mesmo item é
    inalcançável: a 1ª guarda recusa e `ITEMS_SEM_CONEXAO` + o filtro de rastro
    com dono do `scripts/adotar_items_of_orfaos.py` não o listam.
    """
    _mock_item(monkeypatch, user_id)
    real, rival = of_routes.user_exists, []

    def _planta(uid):
        if not rival:
            rival.append(db.register_item(uid, provider_item_id="z-abandono",
                                          origin="pluggy_item"))
        return real(uid)

    monkeypatch.setattr(of_routes, "user_exists", _planta)
    try:
        assert asyncio.run(of_routes._adota_item_orfao("z-abandono", "item/created")) is None
        assert db.get_connections_by_item_id("z-abandono") == []
        assert [r["origin"] for r in _registry("z-abandono")] == ["pluggy_item"], \
            _registry("z-abandono")

        # Sem a rival não sobra reivindicação nenhuma, e o item volta a ser
        # ADOTÁVEL — é a saída que o aborto mútuo não tinha.
        assert db.unregister_item(rival[0], user_id) == 1
        assert db.item_registry_origins("z-abandono") == set(), "o item voltou a ser órfão"
        assert asyncio.run(of_routes._adota_item_orfao("z-abandono", "item/created")) == user_id
        assert len(db.get_connections_by_item_id("z-abandono")) == 1
    finally:
        db.disconnect_open_finance_connection(user_id)
        _limpa_item("z-abandono")


def test_unregister_item_nao_apaga_linha_de_outro_usuario(user_id):
    """Isolamento por usuário (CLAUDE.md §0) num DELETE que recebe `id` cru."""
    linha = db.register_item(user_id, provider_item_id="z-iso", origin="webhook_adopt")
    try:
        assert db.unregister_item(linha, user_id + 10_000_000) == 0
        assert db.item_registry_origins("z-iso") == {"webhook_adopt"}
        # e o par (`exceto_registro_id`, `exceto_user_id`) só ignora a linha do
        # PRÓPRIO dono: com outro user_id ela continua contando.
        assert db.item_registry_origins(
            "z-iso", exceto_registro_id=linha, exceto_user_id=user_id + 10_000_000) == \
            {"webhook_adopt"}
        assert db.item_registry_origins(
            "z-iso", exceto_registro_id=linha, exceto_user_id=user_id) == set()
        # MEIO par era pior que nenhum: so o `id` fazia o `user_id = null` levar
        # o `not (...)` a NULL e descartar TODA linha - "nunca teve dono" para um
        # item que TEM. Hoje e `ValueError` na fronteira (CLAUDE.md 0.2).
        for kw in ({"exceto_registro_id": linha}, {"exceto_user_id": user_id}):
            with pytest.raises(ValueError):
                db.item_registry_origins("z-iso", **kw)
    finally:
        _limpa_item("z-iso")


def test_quem_perde_o_lock_tambem_some_do_rastro(user_id, monkeypatch, eventos, webhook_pluggy):
    """A 3ª intercalação: quem PEGA o lock é justamente quem aborta.

    Achado do Tester contra a 1ª versão do conserto, e era REGRESSÃO contra a
    `main`: as duas entregas reivindicam, a que pega o lock vê o rastro da outra,
    aborta e apaga o dela — e a outra PERDE o lock (503). A reivindicação da
    perdedora ficava para trás, e o desfecho medido em duas colunas era
    `0 conexões + rastro com dono + retentativa recusada` (o estado terminal do
    P0) onde a `main` dava 1 conexão saudável e 2 rastros.

    Determinismo sem sleep, duas barreiras: a de `user_exists` prende as duas
    ENTRE a leitura do rastro e o `register_item` (então as duas passam a 1ª
    guarda com o registry vazio e as duas reivindicam); a do
    `_salva_item_sob_lock` prende as duas no lock. A 1ª a chegar delega para o
    real (vai abortar), a 2ª devolve `(None, False)` em TODAS as tentativas —
    lock ocupado, escrita nunca tentada. A 3ª entrega (a retentativa da Pluggy)
    não espera barreira nenhuma e roda o caminho real inteiro.

    CONTROLES: negativo — `if False and ...` na condição do desfazimento no 503
    deixa este teste vermelho no assert de RASTRO, o 1º a bater (o vermelho
    seguinte e por que os dois asserts moram juntos: docstring do módulo);
    positivo — a retentativa exige UMA conexão, e derruba quem recusar tudo.
    """
    _mock_item(monkeypatch, user_id)
    monkeypatch.setattr(of_routes, "_RECONNECT_DEADLINE_MS", 2000)
    real_lock, real_user = of_routes._salva_item_sob_lock, of_routes.user_exists
    b_leitura, b_lock = threading.Barrier(2, timeout=10), threading.Barrier(2, timeout=10)
    vistos, chegadas, trava = {}, [], threading.Lock()

    def _pareia(uid):
        with trava:
            n = len(chegadas)
            chegadas.append(uid)
        if n < 2:
            b_leitura.wait()            # as duas passaram a 1ª guarda; agora reivindicam
        return real_user(uid)

    def _intercala(*a, **kw):           # repassa TUDO (args E kwargs): nada some no meio
        registro = a[6]                 # `adocao_registro_id`: identifica a ENTREGA
        with trava:
            nova = registro not in vistos
            if nova:
                vistos[registro] = len(vistos)
            ordem = vistos[registro]
        if nova and ordem < 2:
            b_lock.wait()
        return (None, False) if ordem == 1 else real_lock(*a, **kw)

    monkeypatch.setattr(of_routes, "user_exists", _pareia)
    monkeypatch.setattr(of_routes, "_salva_item_sob_lock", _intercala)

    async def duas():
        return await asyncio.gather(
            of_routes._adota_item_orfao("z-perde-lock", "item/created"),
            of_routes._adota_item_orfao("z-perde-lock", "item/created"))

    try:
        assert asyncio.run(duas()) == [None, None], "ninguém devia ter gravado"
        assert db.get_connections_by_item_id("z-perde-lock") == []
        # O ponto do conserto: NENHUMA reivindicação sobrou. Sem ele fica a da
        # perdedora do lock (a que abortou já apagava a dela).
        assert _registry("z-perde-lock") == [], _registry("z-perde-lock")
        erros = [e["details"].get("error", "") for e in eventos
                 if e["event"] == "of_webhook_adopt_skipped"]
        assert any("503" in e for e in erros) and any("409" in e for e in erros), erros

        # RETENTATIVA: é ela que o estado terminal recusava. Aqui adota.
        assert asyncio.run(
            of_routes._adota_item_orfao("z-perde-lock", "item/created")) == user_id
        assert len(db.get_connections_by_item_id("z-perde-lock")) == 1, \
            "a retentativa foi recusada: 0 conexões, o usuário sem banco e sem saída"
    finally:
        db.disconnect_open_finance_connection(user_id)
        _limpa_item("z-perde-lock")
