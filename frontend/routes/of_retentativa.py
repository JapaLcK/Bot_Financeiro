"""Retentativa automática do tique de saúde (Onda 5, PR-B2, D3 = A). Sem router.

Depois do job de saúde, relê a Pluggy (só GET) para até K conexões com dado
atrás, uma de cada vez, pelo MESMO caminho do webhook (`_schedule_pluggy_sync`,
com `_INFLIGHT`): um webhook do mesmo item no meio vira `_DIRTY` em vez de um
sync paralelo (a retentativa que encontra o item em voo NÃO marca `_DIRTY`), e a atualização ao vivo sai de graça. Mora aqui, e não em
`core/`, porque `_INFLIGHT` é memória do laço deste pacote.

Quem entra: `classe_de_retentativa` (`core/services/of_retentativa.py`), sobre a
listagem `list_connections_para_retentar`. A elegibilidade é sempre recalculada do
banco: se o processo cair, o tique seguinte recomeça dele. A ÚNICA memória é
`_TENTADOS` (`id → instante`), só para a ORDEM da fila: quem foi tentado ou
coalescido recentemente vai depois de quem não foi, e assim um item que falha sem
gravar nada na linha (`no_accounts` com o `GET /items` falhando) ou um `_INFLIGHT`
preso não fica na frente para sempre. Perdê-la no restart custa, no pior caso, um
tique. Tabelas de elegibilidade e de concorrência: `docs/open_finance_estados.md` §2.2.
"""

from __future__ import annotations

import asyncio
import time
from collections import Counter

from core.reports.reports_daily import filtrar_por_acesso
from core.services.of_retentativa import classe_de_retentativa, elegiveis, tipo_de_desfecho
from core.services.pluggy_sync import _env_int, of_health_check_ligado
from db.open_finance_state import list_connections_para_retentar
from frontend.routes import open_finance as _of

# K padrão: o job de saúde já faz até 200 GETs por tique (`run_of_health_check`),
# e um sync pequeno faz ~10 requisições (estimativa pelo código, não medição).
# 200 / 10 = 20: a retentativa custa no máximo o que a saúde já custa.
_K_PADRAO = 20
# "O mesmo erro 3 vezes não é transitório" (`_SYNC_MAX_ATTEMPTS`), agora entre
# itens diferentes. É escolha, não derivação.
_FALHAS_SEGUIDAS = _of._SYNC_MAX_ATTEMPTS
# Relógio do prazo do tique; variável de módulo para o teste trocar sem mexer no
# `time.monotonic` do laço inteiro.
_relogio = time.monotonic
# Relógio da memória `_TENTADOS`, separado do prazo para os testes trocarem um sem o outro.
_quando = time.monotonic

# `id → instante` (monotônico) da última tentativa ou coalescência DESTE processo; só
# ordena a fila (ver o docstring do módulo). Não é a âncora da "Pluggy à frente", que
# segue sendo `last_attempt_at`, e nada é gravado na linha. Descartada por CANDIDATURA, e
# não por idade: sai o `id` que não está mais entre as candidatas elegíveis do tique (o
# item saiu da fila: leu, foi pausado, apagado...). Por idade (já foi `4 × prazo_sec`)
# perdia a rotação, porque cada tique dura mais que o intervalo e a entrada do tique
# anterior vencia antes do seguinte. Limitada pelo tamanho da fila elegível.
_TENTADOS: dict[int, float] = {}


async def retentar_leituras(*, prazo_sec: float) -> dict:
    """Uma passada. `prazo_sec`: depois dele não começa item novo e não se espera
    mais um sync em voo (o laço só dorme de novo quando termina; o tique passa
    metade do intervalo).

    Desliga com `OF_HEALTH_CHECK_ENABLED=0` (a mesma leitura do job de saúde) ou
    `OF_RETRY_MAX_PER_TICK<=0` (só esta etapa). Valor que não é inteiro
    (`""`, `abc`, `off`, `1.5`) cai no padrão, como toda `OF_*` inteira
    (`_env_int`): só `0` ou negativo desliga.

    O `of_retry_tick` sai sempre, também quando a passada levanta (então com
    `interrompido="erro"`); a exceção sobe para o laço, que a registra e segue.
    """
    k = _env_int("OF_RETRY_MAX_PER_TICK", _K_PADRAO)
    if not of_health_check_ligado() or k <= 0:
        return {"skipped": "disabled"}
    inicio = _relogio()
    candidatas: list = []
    conta: Counter = Counter()
    tentados: list[str] = []
    interrompido = None
    seguidas = 0
    try:
        candidatas = elegiveis(await asyncio.to_thread(list_connections_para_retentar))
        # Quem este processo ainda não tentou vem primeiro; entre os tentados, o mais
        # antigo primeiro (rodízio). `sorted` é estável: os empates seguem a ordem da
        # listagem (`last_attempt_at nulls first, id`).
        ids = {r["id"] for r, _ in candidatas}
        for i in [i for i in _TENTADOS if i not in ids]:
            del _TENTADOS[i]
        fila = sorted(candidatas, key=lambda rc: _TENTADOS.get(rc[0]["id"], float("-inf")))
        # Dono já visto SEM acesso nesta passada: o resto das conexões dele é pulado sem
        # consulta (o acesso é por dono). Só guarda o NEGATIVO: o positivo é refeito antes
        # de cada item agendado, porque o plano pode expirar durante a passada.
        cortados: set[int] = set()

        # A passada percorre a fila JÁ ORDENADA e para ao agendar K itens, ou no prazo.
        # Nada é consultado por candidato antes do laço: a listagem e a classificação
        # (pura) são as únicas O(candidatos); a rechecagem e o acesso são por item
        # percorrido, e o acesso no máximo uma vez por dono cortado + uma por item agendado.
        for row, _classe in fila:
            if len(tentados) >= k:
                break
            # Tudo o que foi decidido antes da passada (a linha, o prazo) vale no máximo até
            # aqui: uma passada dura até metade do intervalo, e a rechecagem pode esperar o
            # pool por até 30 s. Cada um é REAVALIADO por item, imediatamente antes de agendar.
            if prazo_sec - (_relogio() - inicio) <= 0:
                interrompido = "prazo"
                break
            if row["user_id"] in cortados:
                conta["sem_acesso"] += 1
                continue
            # Rechecagem: o tique pode levar dezenas de minutos, e um Atualizar ou uma
            # reconexão no meio tiram a linha sem custo de Pluggy.
            atual = await asyncio.to_thread(list_connections_para_retentar, id=row["id"])
            if not atual or classe_de_retentativa(atual[0]) is None:
                conta["rechecados"] += 1
                continue
            linha = atual[0]
            # Acesso do dono AGORA: um plano ou trial que expirou durante a passada não
            # sincroniza o item seguinte. É a mesma `filtrar_por_acesso` dos laços proativos
            # (§0.7), com o dono deste item. Item de dono cortado é pulado e NÃO toma vaga do
            # K: fica fora de `_TENTADOS` e de `tentados`.
            if not await asyncio.to_thread(filtrar_por_acesso, [linha["user_id"]]):
                cortados.add(linha["user_id"])
                conta["sem_acesso"] += 1
                continue
            # O prazo restante DEPOIS das duas consultas acima: o valor do começo da
            # iteração podia estar velho e agendar um sync novo com o prazo estourado.
            restante = prazo_sec - (_relogio() - inicio)
            if restante <= 0:
                interrompido = "prazo"
                break
            # Sem carimbo de tentativa NA LINHA: quem grava `last_attempt_at` é o próprio
            # sync (e a marca de falha). Carimbar antes empurrava a âncora da "Pluggy
            # à frente" para depois do dado dela, e um 5xx passageiro num `no_accounts`
            # o tirava da lista até a Pluggy coletar de novo; também carimbava o
            # coalescido, que ninguém tentou. A ordem da fila vem de `_TENTADOS`
            # (memória), não da coluna (`docs/open_finance_estados.md` §2.2).
            # `marcar_sujo=False`: o item em voo é só contado como coalescido. O `_DIRTY` não
            # tem dono (a rodada suja roda sem `expected_user_id` e sem `filtrar_por_acesso`).
            tarefa = _of._schedule_pluggy_sync(linha["provider_item_id"],
                                               expected_user_id=linha["user_id"],
                                               marcar_sujo=False)
            tentados.append(linha["provider_item_id"])
            _TENTADOS[linha["id"]] = _quando()
            if tarefa is None:
                conta["coalescidos"] += 1
                continue
            try:
                # `shield`: estourar o prazo solta o tique, não cancela o sync, que
                # segue vivo no `_INFLIGHT` (webhook do mesmo item vira `_DIRTY`).
                desfecho = await asyncio.wait_for(asyncio.shield(tarefa), restante)
            except asyncio.TimeoutError:
                conta["pendurados"] += 1
                interrompido = "prazo"
                break
            tipo = tipo_de_desfecho(desfecho)
            conta[tipo] += 1
            if tipo == "429":
                interrompido = "429"
                break
            seguidas = seguidas + 1 if tipo == "falha" else (0 if tipo == "ok" else seguidas)
            if seguidas >= _FALHAS_SEGUIDAS:
                interrompido = "falhas_seguidas"
                break
    except Exception:
        interrompido = "erro"
        raise
    finally:
        resumo = {"elegiveis": len(candidatas),
                  "por_classe": dict(Counter(c for _, c in candidatas)),
                  "tentados": len(tentados), "ok": conta["ok"],
                  "falhas": conta["falha"] + conta["429"], "neutros": conta["neutro"],
                  "coalescidos": conta["coalescidos"], "pendurados": conta["pendurados"],
                  "rechecados": conta["rechecados"], "sem_acesso": conta["sem_acesso"],
                  "interrompido": interrompido,
                  "items": tentados}
        # Sem `user_id` (a coluna fica NULL: o tique tem vários donos) e sem uid em
        # `details`, que sobreviveria à exclusão da conta (issue #541).
        await _of.log_system_event(
            "warning" if interrompido in ("429", "falhas_seguidas", "erro") else "info",
            "of_retry_tick",
            f"Retentativa do tique: {len(tentados)} de {len(candidatas)} elegível(is)",
            source="open_finance", details=resumo)
    return resumo
