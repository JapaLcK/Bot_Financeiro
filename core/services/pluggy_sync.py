"""Sync real Open Finance/Pluggy — puxa contas + transações e grava nas tabelas OF.

Fase 0 do plano de Open Finance: substitui o gerador mock por dados reais.
NÃO toca no saldo manual nem em `launches` — isso é a Fase 1 (import + conciliação).

O trabalho aqui é bloqueante (httpx + DB); chame via asyncio.to_thread a partir das rotas.
"""

from __future__ import annotations

import os
import time
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from dateutil import parser as _dateutil_parser

from utils_date import _tz

from core.services.pluggy import (
    PluggyApiError,
    create_pluggy_api_key,
    get_pluggy_item,
    list_pluggy_accounts,
    list_pluggy_investments,
    list_pluggy_transactions,
    update_pluggy_item,
)
from core.services.pluggy_health import (
    READ_FAILED,
    # "ainda buscando no banco": esperamos sair disto antes de sincronizar, senão
    # lemos o snapshot velho. Vem do `pluggy_health` porque lá é a fonte do
    # significado dos status de item — aqui era o MESMO conjunto declarado de novo.
    _UPDATING as ITEM_UPDATING,
    connection_ui_state,
    derive_item_health,
    resolve_connection_state,
)
from db import (
    claim_items_for_refresh,
    claim_manual_refresh,
    get_connections_by_item_id,
    get_open_finance_connection_by_item_id,
    get_open_finance_snapshot,
    import_open_finance_credit,
    import_open_finance_launches,
    list_connections_for_health_check,
    mark_sync_attempt,
    mark_sync_result,
    pluggy_item_lock,
    save_open_finance_investments,
    sync_open_finance_caixinhas,
    save_open_finance_sync,
    sync_imported_open_finance_updates,
)


def _HEALTH_MISSING() -> dict:
    """Health de item que sumiu — os dois caminhos que gravam 404 usam este."""
    return {"observed_at": datetime.now(_tz()).isoformat(), "item_status": "MISSING",
            "execution_status": None, "products": {}, "stale_products": []}


def item_missing(exc: Exception) -> bool:
    """404 no `GET /items/{id}` = o item não existe mais na Pluggy.

    É a ÚNICA resposta que prova isso: o `GET /accounts?itemId=<deletado>` volta
    200 com `results: []` (medido em produção), indistinguível de um item vivo
    sem contas se ninguém perguntar pelo item.
    """
    return isinstance(exc, PluggyApiError) and exc.status_code == 404


def _to_decimal(value: Any, default: str = "0") -> Decimal:
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return Decimal(default)


def _parse_date(value: Any) -> date:
    """Aceita 'YYYY-MM-DD', ISO com hora, ou datetime. Cai pra hoje se não der."""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value or "").strip()
    if text:
        try:
            return datetime.strptime(text[:10], "%Y-%m-%d").date()
        except ValueError:
            pass
    return datetime.now(_tz()).date()


def _parse_datetime(value: Any) -> datetime | None:
    """Extrai o instante COMPLETO (com hora) de uma transação do Pluggy.

    Retorna um datetime timezone-aware quando o banco envia hora real; retorna
    None quando só há data (hora == 00:00) — nesse caso o import cai no fallback
    de data pura, sem inventar horário. O `date` do Pluggy é ISO 8601, ex.:
    "2026-08-14T15:30:00.000-03:00" (hora real) ou "2026-08-14T00:00:00.000Z"
    (placeholder de só-data). A meia-noite é tratada como "sem hora".
    """
    dt: datetime | None = None
    if isinstance(value, datetime):
        dt = value
    else:
        text = str(value or "").strip()
        if not text:
            return None
        # "YYYY-MM-DD" puro não carrega hora → sem horário real
        if len(text) == 10 and text.count("-") == 2:
            return None
        try:
            dt = _dateutil_parser.isoparse(text)
        except (ValueError, TypeError, OverflowError):
            return None

    if dt is None:
        return None

    # meia-noite exata = placeholder de "só data" do provedor → sem hora real
    if dt.hour == 0 and dt.minute == 0 and dt.second == 0 and dt.microsecond == 0:
        return None

    # garante timezone-aware; se vier naive, assume o fuso do bot
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=_tz())
    return dt


def normalize_pluggy_account(raw: dict) -> dict:
    """Converte a conta crua da Pluggy no formato que `save_open_finance_sync` espera.

    Confia no `balance` como o Pluggy manda (cartão já vem negativo = valor devido).
    Não mexe no sinal — a semântica de consolidação fica pra Fase 1.
    """
    return {
        "provider_account_id": str(raw.get("id") or ""),
        "name": str(raw.get("marketingName") or raw.get("name") or raw.get("type") or "Conta"),
        "type": str(raw.get("type") or "BANK"),
        "subtype": (str(raw["subtype"]) if raw.get("subtype") else None),
        "currency": str(raw.get("currencyCode") or "BRL"),
        "balance": _to_decimal(raw.get("balance")),
        "raw": raw,
    }


def normalize_pluggy_transaction(raw: dict) -> dict:
    """Confia no `amount` do Pluggy, que já vem assinado (negativo = saída, positivo = entrada).

    NÃO deriva o sinal do campo `type`: no cartão, uma compra vem como type=CREDIT com
    amount negativo — inverter pelo type transformaria compra em receita (bug pego no E2E
    sandbox). O `type` fica só no `raw`.
    """
    return {
        "provider_transaction_id": str(raw.get("id") or ""),
        "description": str(raw.get("description") or raw.get("descriptionRaw") or "Transação"),
        "amount": _to_decimal(raw.get("amount")),
        "transaction_date": _parse_date(raw.get("date")),
        "transacted_at": _parse_datetime(raw.get("date")),
        "category": (str(raw["category"]) if raw.get("category") else None),
        "raw": raw,
    }


def normalize_pluggy_investment(raw: dict) -> dict:
    """Converte o investimento cru da Pluggy pro formato de `save_open_finance_investments`."""
    return {
        "provider_investment_id": str(raw.get("id") or ""),
        "name": str(raw.get("name") or raw.get("type") or "Investimento"),
        "type": str(raw.get("type") or ""),
        "subtype": (str(raw["subtype"]) if raw.get("subtype") else None),
        "currency": str(raw.get("currencyCode") or "BRL"),
        "balance": _to_decimal(raw.get("balance")),
        "raw": raw,
    }


def is_caixinha(investment: dict) -> bool:
    """Caixinha do Nubank / Cofrinho do PicPay = CDB de renda fixa (doc Pluggy)."""
    return (str(investment.get("type") or "").upper() == "FIXED_INCOME"
            and str(investment.get("subtype") or "").upper() == "CDB")


def sync_pluggy_item(provider_item_id: str) -> dict:
    """Sincroniza um item Pluggy: contas + transações → tabelas OF. Idempotente.

    Máquina de estados (explícita, porque remendá-la um caso por vez foi como o
    bug original nasceu): aborta APENAS em PAUSED e DELETED. ERROR **não** é
    terminal — é recuperável, e é por aqui que ERROR volta a ACTIVE: item
    consultado → saudável → sync executado → concluído.

    Este é o ÚNICO lugar do sistema que carimba ACTIVE/last_sync_at.
    """
    connection = get_open_finance_connection_by_item_id(provider_item_id)
    if not connection:
        return {"ok": False, "reason": "connection_not_found", "item_id": provider_item_id}

    status_local = str(connection.get("status") or "").upper()
    # Trial venceu sem virar assinatura: dados importados ficam, mas o sync PARA
    # (o item nem existe mais na Pluggy). Barra webhook atrasado/replay.
    if status_local == "PAUSED":
        return {"ok": False, "reason": "connection_paused", "item_id": provider_item_id}
    # Desconectado: terminal do mesmo jeito. Sem isto, um webhook atrasado
    # (item/updated chega depois do item/deleted) ressuscitava a conexão.
    if status_local == "DELETED":
        return {"ok": False, "reason": "connection_deleted", "item_id": provider_item_id}

    # PERGUNTA PELO ITEM ANTES DE DAR POR BOM. O `/accounts?itemId=<deletado>`
    # devolve 200 com results:[] — só o `GET /items/{id}` devolve 404. Sem esta
    # consulta, item apagado no banco virava "sincronizado agora, 0 contas".
    api_key = create_pluggy_api_key()
    try:
        item = get_pluggy_item(provider_item_id, api_key)
    except Exception as exc:
        if not item_missing(exc):
            raise
        # NÃO apaga espelho, NÃO apaga item local nem remoto, NÃO reconecta:
        # a decisão de refazer a conexão é do usuário. Aqui só se conta a verdade.
        status, reason = resolve_connection_state(missing=True)
        mark_sync_result(connection["id"], ok=False, status=status, status_reason=reason,
                         health=_HEALTH_MISSING())
        return {"ok": False, "reason": "item_missing", "item_id": provider_item_id,
                "connection_id": connection["id"], "user_id": connection["user_id"]}

    health = derive_item_health(item)
    return _sync_pluggy_item_confirmado(provider_item_id, connection, api_key, health)


def _sync_pluggy_item_confirmado(provider_item_id: str, connection: dict, api_key: str,
                                 health: dict) -> dict:
    """O sync em si, com o item já confirmado vivo.

    Duas fases, e a fronteira entre elas é o lock:
      1. LEITURA remota (contas, transações, investimentos) — sem lock nenhum.
         É read-only contra a Pluggy, e a paginação leva até 60 requisições por
         conta; segurar um lock de sessão aqui retinha uma conexão de banco por
         minutos (ver `pluggy_item_lock`).
      2. ESCRITA — dentro do lock por item. É ela que dava os 10
         `deadlock detected` medidos em produção, quando `item/updated` e
         `transactions/created` chegavam com 0–17s de intervalo.
    """
    # Ponto comum de TODOS os caminhos que mexem na carteira — inclusive o webhook
    # de produção, que chama esta função direto (frontend/routes/open_finance.py),
    # sem passar por sync_pluggy_user. Segurar aqui cobre as leituras remotas
    # abaixo, que rodam antes de qualquer commit. Os wrappers seguram mais cedo
    # ainda (antes de listar itens / antes do PATCH e da espera); este é a rede
    # que pega qualquer caminho novo que apareça.
    #
    # `heartbeat` RENOVA o hold ao longo do sync: a busca de transações pode levar
    # até 60 requisições paginadas por conta (minutos), passando do _SYNC_QUIET_MIN
    # do hold inicial. Sem renovar, o hold expiraria no meio de um item longo — e
    # como last_sync_at só é carimbado no fim, nada seguraria o e-mail nessa janela.
    # Chamado a cada conta e a cada página. Expira sozinho se o processo morrer.
    heartbeat = lambda: _hold_aggregate_emails(connection["user_id"], "sync_item")
    heartbeat()

    accounts: list[dict] = []
    try:
        for raw_account in list_pluggy_accounts(provider_item_id, api_key):
            heartbeat()
            account = normalize_pluggy_account(raw_account)
            if not account["provider_account_id"]:
                continue
            raw_txs = list_pluggy_transactions(account["provider_account_id"], api_key,
                                               on_page=heartbeat)
            account["transactions"] = [
                tx for tx in (normalize_pluggy_transaction(t) for t in raw_txs) if tx["provider_transaction_id"]
            ]
            accounts.append(account)
    except Exception:
        # Falhou lendo conta/transação: foi TENTATIVA, e o carimbo de tentativa
        # mora depois do lock, lá embaixo — sem isto a falha não deixava rastro
        # nenhum na linha. Não vira resultado: o motivo continua desconhecido e
        # quem decide retentar é o chamador (`_retryable`).
        mark_sync_attempt(connection["id"], origin="sync")
        raise

    # #10: investimentos (inclui Caixinha/CDB). A leitura fica AQUI, junto do
    # resto do remoto: corretora (XP/Rico/BTG/Warren) devolve `/accounts` vazio
    # porque a carteira vive em `/investments`, e o early-return de `no_accounts`
    # ficava ANTES desta chamada — a conexão nunca espelhava nada e last_sync_at
    # congelava para sempre. `PLUGGY_PRODUCTS` inclui INVESTMENTS de propósito.
    #
    # FAIL-SOFT, e isto não é zelo: subir a leitura para antes de qualquer
    # escrita fez um 429 em `/investments` DESCARTAR as contas e transações já
    # lidas — medido, 1 conta + 1 transação viraram 0 e 0, e `/investments` com
    # rate limit é o caso de produção que originou esta onda. O que não pode
    # é confundir "li e veio vazio" com "não consegui ler": só o primeiro
    # autoriza `no_accounts`.
    investments: list[dict] = []
    investments_ok = True
    try:
        investments = [normalize_pluggy_investment(i)
                       for i in list_pluggy_investments(provider_item_id, api_key)]
    except Exception as exc:
        investments_ok = False
        print(f"[pluggy_sync] investimentos indisponíveis item={provider_item_id} "
              f"erro={type(exc).__name__}", flush=True)
    heartbeat()

    # ── FASE 2: escrita, serializada por item ────────────────────────────────
    with pluggy_item_lock(provider_item_id) as locked:
        if not locked:
            return {"ok": False, "reason": "sync_in_progress", "item_id": provider_item_id,
                    "connection_id": connection["id"], "user_id": connection["user_id"]}
        # Tentativa carimbada DEPOIS do lock: quem não conseguiu escrever não
        # tentou sincronizar, e antes o perdedor da corrida já mexia na linha.
        mark_sync_attempt(connection["id"], origin="sync")

        # Item vivo que não espelhou NADA — nem conta nem investimento — não é
        # sucesso. Só que a decisão vem depois da leitura de investimentos: item
        # de corretora é exatamente isto, zero contas e a carteira toda em
        # `/investments`. `health` é carimbado (é a saúde medida agora, e ela
        # vale); o que não pode é ACTIVE/last_sync_at.
        if not accounts and not investments:
            status, reason = resolve_connection_state(
                health=health, has_data=False, leitura_completa=investments_ok)
            mark_sync_result(connection["id"], ok=False, status=status,
                             status_reason=reason, health=health)
            return {"ok": False, "reason": reason, "item_id": provider_item_id,
                    "connection_id": connection["id"], "user_id": connection["user_id"],
                    "accounts_synced": 0, "transactions_synced": 0}

        result = save_open_finance_sync(connection["id"], accounts)
        inv_result = save_open_finance_investments(connection["id"], investments)

        # Caixinhas do OF viram caixinhas do Pig automaticamente (auto-create + dedup) e o
        # saldo do banco é espelhado nas vinculadas — mas SÓ pra planos pagos (Essencial+).
        # No Grátis (pós-trial) o OF nem sincroniza (conexão PAUSED barra acima); este gate é
        # a segunda trava: se o usuário caiu de plano, as caixinhas congelam (não atualizam).
        # Renda variável (ações/FIIs) é lida à parte no snapshot, também gated. Fail-soft.
        caixinha_result = {"caixinhas_created": 0, "caixinhas_linked": 0, "caixinhas_mirrored": 0}
        try:
            from core.services.plan_service import require_min_tier
            if require_min_tier(connection["user_id"], "essencial"):
                caixinha_result = sync_open_finance_caixinhas(connection["id"], connection["user_id"])
        except Exception as exc:
            print(f"[pluggy_sync] caixinha auto-import: {exc}")

        # Fase 1: conta BANK → launches (analytics, sem mover saldo); cartão → faturas (opção a).
        imported = import_open_finance_launches(connection["user_id"], connection["id"])
        imported_credit = import_open_finance_credit(connection["user_id"], connection["id"])
        # Propaga correções da Pluggy (transactions/updated) pros já importados (não deixa stale).
        updated = sync_imported_open_finance_updates(connection["user_id"], connection["id"])

        # Concluído: ESTE é o único carimbo de sucesso do sistema. Dentro do
        # lock de propósito — ele afirma que as escritas acima valeram.
        status, reason = resolve_connection_state(health=health, has_data=True,
                                                  leitura_completa=investments_ok)
        mark_sync_result(connection["id"], ok=True, status=status, status_reason=reason,
                         health=health, at=datetime.now(_tz()))

    # Agentes do Piggy — gatilho pós-sync: Xerife roda só sobre o delta deste
    # usuário. IMPORTANTE: depois da reconciliação/import acima (merge-silencioso
    # primeiro, senão duplicata manual+OF viraria falso positivo). Fail-soft.
    # FORA do lock: não mexe no espelho do item e pode demorar.
    if imported.get("inserted") or (imported_credit or {}).get("inserted"):
        try:
            from core.services.piggy_agents import run_agents_for_user
            run_agents_for_user(connection["user_id"], trigger="of_sync")
        except Exception as exc:  # nunca derruba o sync por causa de agente
            print(f"[pluggy_sync] agents hook: {exc}")

    return {
        "ok": True,
        "item_id": provider_item_id,
        "connection_id": connection["id"],
        "user_id": connection["user_id"],
        # Sucesso PARCIAL continua sendo sucesso de sync (o que veio, veio), mas
        # quem lê o resultado precisa saber o que ficou pra trás.
        "stale_products": list(health.get("stale_products") or []),
        "investments_ok": investments_ok,
        **result,
        **inv_result,
        **caixinha_result,
        "imported": imported,
        "imported_credit": imported_credit,
        "updated": updated,
    }


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


def request_pluggy_refresh(*, origin: str, user_id: int | None = None, limit: int = 200) -> dict:
    """Dispara o PATCH /items dos itens ELEGÍVEIS (a Pluggy re-busca do banco e
    manda webhook → sync). Usado pelo tick periódico e pelo refresh manual.

    Três coisas mudaram em relação ao `refresh_all_pluggy_items` que existia aqui:

    1. `origin="startup"` não dispara NADA. O tick antigo rodava um refresh no boot
       e o Railway sobe container novo a cada deploy — medido: 15 rodadas em 48h,
       10 delas nos 5 minutos seguintes a um deploy. Cota da Pluggy queimada por
       deploy, não por necessidade.
    2. Quem escolhe os itens é `claim_items_for_refresh`, um `UPDATE ... RETURNING`
       atômico: duas instâncias no mesmo tick não pegam o mesmo item, e o cooldown
       fica PERSISTIDO em `next_refresh_at` (sobrevive a restart, que era como o
       intervalo de 6h virava "a cada deploy").
    3. Falha de PATCH deixou de ser engolida (`except Exception: pass`) — vira
       evento com item e motivo.

    LIMITAÇÃO CONHECIDA (decisão de 2026-08-15): este caminho é fire-and-forget —
    dispara o PATCH e retorna; o webhook chega depois e aciona sync_pluggy_item (que
    aí sim renova o hold e faz heartbeat). O hold aplicado aqui dura _SYNC_QUIET_MIN
    (10min). Se a Pluggy demorar MAIS que isso pra entregar o webhook, o hold expira
    na janela PATCH→webhook e um retrato agregado maduro pode ser emailado com o
    estado ANTERIOR ao refresh. Não é corrigido de propósito:
      - probabilidade ínfima: o webhook normalmente chega em ~segundos (o refresh
        manual assume ~18s, OF_REFRESH_WAIT_SEC); >10min é anomalia severa da Pluggy;
      - dano marginal: o e-mail sai com o estado COMPLETO anterior (correto no
        momento do envio), não com um snapshot parcial errado como no bug original;
        e o feed se autocorrige quando o webhook processa.
    Fechar 100% exigiria um worker renovando o lease enquanto o item está UPDATING
    (processo/estado novo), custo desproporcional pro cenário."""
    if origin == "startup":
        print("[pluggy_sync] of_refresh_skipped_startup", flush=True)
        return {"triggered": 0, "total": 0, "origin": origin, "skipped": "startup",
                "failures": []}

    claimed = claim_items_for_refresh(
        cooldown_sec=_env_int("OF_REFRESH_MIN_INTERVAL_SEC", 6 * 60 * 60),
        jitter_pct=float(_env_int("OF_REFRESH_JITTER_PCT", 10)),
        origin=origin,
        limit=limit,
        user_id=user_id,
    )
    triggered = 0
    failures: list[dict] = []
    segurados: set[int] = set()
    for row in claimed:
        item_id = row.get("provider_item_id")
        if not item_id:
            continue
        try:
            # Segura o e-mail dos agregados ANTES do PATCH: daqui até o webhook
            # trazer os dados novos, o evento maduro seguiria reivindicável com o
            # valor velho — e o emailed_at recusaria a correção depois.
            dono = row.get("user_id")
            if dono is not None and dono not in segurados:
                _hold_aggregate_emails(dono, "refresh_all")
                segurados.add(dono)
            update_pluggy_item(item_id)
            triggered += 1
        except Exception as exc:
            motivo = "item_missing" if item_missing(exc) else type(exc).__name__
            failures.append({"item_id": item_id, "reason": motivo, "error": str(exc)[:200]})
            print(f"[pluggy_sync] pluggy_refresh_patch_failed item={item_id} motivo={motivo}",
                  flush=True)
    return {"triggered": triggered, "total": len(claimed), "origin": origin,
            "failures": failures,
            "claimed": [{"item_id": r.get("provider_item_id"), "user_id": r.get("user_id")}
                        for r in claimed]}


def run_of_health_check(*, limit: int = 200) -> dict:
    """Mede a saúde das conexões ativas com um `GET /items/{id}` por item.

    Só GET: não consome cota de COLETA da Pluggy (o PATCH é que consome), então
    pode rodar mesmo com o refresh desligado. É o que faz uma conexão cujo item
    sumiu sair de ACTIVE — sem webhook e sem refresh, nada mais executaria o
    caminho novo e ela ficaria "Atualizado" para sempre.

    DISJUNTOR: este job escreve `status='ERROR'` em conexão de usuário e roda
    ligado por padrão. Se a credencial apontar para o client errado, o
    `GET /items/{id}` devolve 404 para TODOS e uma passada marcaria a base
    inteira como `item_missing` — mandando todo mundo refazer a conexão.

    A CONDIÇÃO DE SAÍDA é `checked > 0`, e é ela que faz o disjuntor convergir.
    UM item que respondeu prova que a credencial está boa; se os outros deram
    404 com a mesma chave, eles sumiram de verdade. Sem essa condição o
    disjuntor travava PARA SEMPRE — medido: 10 conexões, 6 mortas, tick 1 marca
    0 de 6 e as 4 sadias saem do lote (health fresco); do tick 2 em diante a
    amostra É só a dos mortos, 100% > 50%, e as 6 nunca saíam de "Atualizado" —
    exatamente o que este job existe para evitar quando `OF_REFRESH_ENABLED`
    está off (o default).

    Só quando NINGUÉM respondeu (`checked == 0`) o percentual manda: acima de
    `OF_HEALTH_MISSING_ABORT_PCT` numa amostra de pelo menos
    `OF_HEALTH_MIN_SAMPLE` items, a passada aborta sem gravar os ausentes e
    loga. 100% desliga o disjuntor.
    """
    if (os.getenv("OF_HEALTH_CHECK_ENABLED") or "1").strip().lower() in ("0", "false", "no", "off"):
        return {"checked": 0, "missing": 0, "skipped": "disabled"}

    rows = list_connections_for_health_check(
        older_than_sec=_env_int("OF_HEALTH_MAX_AGE_SEC", 12 * 60 * 60),
        limit=limit,
    )
    if not rows:
        return {"checked": 0, "missing": 0}

    api_key = create_pluggy_api_key()
    checked = 0
    ausentes: list[dict] = []
    for row in rows:
        try:
            item = get_pluggy_item(row["provider_item_id"], api_key)
        except Exception as exc:
            if not item_missing(exc):
                print(f"[pluggy_sync] health check falhou item={row['provider_item_id']}: {exc}")
                continue
            ausentes.append(row)   # só grava depois, se o disjuntor não abrir
            continue
        # ok=None: saúde medida NÃO é sucesso de sync — não pode carimbar last_sync_at.
        # `status` E `status_reason` saem JUNTOS do resolvedor: limpar só o motivo
        # deixava o `status='ERROR'` que este mesmo job escreveu, e a tela trocava
        # "Refaça a conexão" por "Erro temporário" — para sempre (medido). Igual
        # para `no_accounts`: quem responde se ele ainda vale é o espelho
        # (`has_data`, lido na mesma query), não a memória da última passada.
        health = derive_item_health(item)
        status, reason = resolve_connection_state(
            health=health, has_data=bool(row.get("has_data")),
            reason_atual=str(row.get("status_reason") or ""))
        mark_sync_result(row["id"], ok=None, status=status, status_reason=reason,
                         health=health)
        checked += 1

    limite = _env_int("OF_HEALTH_MISSING_ABORT_PCT", 50) / 100.0
    minimo = _env_int("OF_HEALTH_MIN_SAMPLE", 5)
    if (ausentes and checked == 0 and len(rows) >= minimo
            and len(ausentes) / len(rows) > limite):
        print(f"[pluggy_sync] of_health_circuit_open ausentes={len(ausentes)}/{len(rows)} "
              f"limite={limite:.0%} — nada gravado", flush=True)
        return {"checked": checked, "missing": 0, "aborted": "too_many_missing",
                "missing_seen": len(ausentes), "sample": len(rows)}

    status, reason = resolve_connection_state(missing=True)
    for row in ausentes:
        mark_sync_result(row["id"], ok=False, status=status, status_reason=reason,
                         health=_HEALTH_MISSING())
    return {"checked": checked, "missing": len(ausentes)}


def _hold_aggregate_emails(user_id: int, origem: str) -> None:
    """Segura o e-mail dos agentes whole-portfolio enquanto a carteira se mexe.

    Carimba email_hold_until nos eventos pendentes deles, o que os torna não
    reivindicáveis pelo runner de e-mail até o hold expirar. Coluna própria — não
    mexe em fired_at, que é dado de negócio (data exibida, ordenação do feed,
    disparos_mes/saved_365d). Tem que
    ser chamado no PRIMEIRO ponto de cada caminho que mexe na carteira, antes de
    qualquer espera ou I/O remoto:

      • sync_pluggy_user        — antes de buscar os itens;
      • refresh_and_sync_...    — antes do PATCH na Pluggy, porque esse fluxo
        ainda espera OF_REFRESH_WAIT_SEC (18s por padrão) até os dados novos
        aparecerem, e essa espera inteira ficaria descoberta se só o sync
        segurasse.

    Nenhum carimbo de conclusão (last_sync_at) enxerga essas janelas — ele só
    existe depois que um item termina. Sem isso, um evento agregado já maduro
    seria emailado no meio da atualização e o emailed_at recusaria a correção
    pelo resto do mês.

    Fail-soft por contrato: nunca levanta. E o hold EXPIRA sozinho — o oposto de
    uma flag de "sync em progresso", que se vazasse (processo morto no meio)
    bloquearia o envio pra sempre. No pior caso o e-mail sai um tick depois."""
    try:
        from db import hold_agent_emails
        from core.services.piggy_agents import _AGENT_EMAIL_MIN_AGE_MIN, _SYNC_QUIET_MIN
        hold_agent_emails(user_id, list(_AGENT_EMAIL_MIN_AGE_MIN), _SYNC_QUIET_MIN)
    except Exception as exc:
        print(f"[pluggy_sync] hold agregados ({origem}): {exc}")


def _sync_item_contido(connection: dict) -> dict:
    """`sync_pluggy_item` de UM item que NUNCA derruba o lote.

    `sync_pluggy_item` re-levanta o que não é 404 de propósito (é o que faz o
    webhook retentar um 429 — `_retryable`, em `frontend/routes/open_finance.py`).
    No LOTE isso é o oposto do que se quer: um único banco com a cota estourada
    subia a exceção até `refresh_and_sync_pluggy_user` e virava 502 na rota —
    sem relatório por item, sem `of_manual_refresh` no log, e os bancos
    saudáveis do mesmo usuário nunca sincronizavam. Falha de um item é
    resultado DAQUELE item.

    Mesma escolha já feita para a leitura de `/investments` (READ_FAILED, ver
    `_sync_pluggy_item_confirmado`): leitura que falhou não é dado ausente.
    """
    item_id = connection["provider_item_id"]
    try:
        return sync_pluggy_item(item_id)
    except Exception as exc:
        print(f"[pluggy_sync] item {item_id} falhou no lote: {type(exc).__name__}: {exc}")
        try:
            # Tira o item do verde na tela: `status=None` é "não mexe" (a chamada
            # falhou, não observamos a saúde do item — só que a tentativa não deu
            # certo), e o motivo pendente faz `connection_ui_state` recusar
            # "Atualizado" pelo default seguro dele.
            #
            # LIMITE MEDIDO: quem limpa `read_failed` é só um sync que consiga
            # ler as contas. O job de saúde NÃO lê contas (só `GET /items`), e o
            # early return `if has_data:` de `resolve_connection_state` devolve
            # ("ACTIVE","") antes do ramo que preservaria o motivo — então um
            # banco que 429 de forma persistente volta ao verde em até
            # OF_HEALTH_MAX_AGE_SEC (12h) com o espelho velho. A pílula não olha
            # a idade de `last_sync_at`; "Atualizado" quer dizer "o item está
            # saudável na Pluggy", não "nosso espelho está fresco". Onda 2.
            mark_sync_result(connection["id"], ok=False, status=None,
                             status_reason=READ_FAILED)
        except Exception as exc2:  # banco fora do ar não pode derrubar o lote também
            print(f"[pluggy_sync] mark_sync_result falhou ({item_id}): {exc2}")
        return {"ok": False, "reason": READ_FAILED, "item_id": item_id,
                "connection_id": connection.get("id"), "error": type(exc).__name__}


def sync_pluggy_user(user_id: int) -> dict:
    """Sincroniza todos os itens Pluggy de um usuário (útil pra sync manual/testes)."""
    snapshot = get_open_finance_snapshot(user_id)
    conns = [
        c
        for c in snapshot.get("connections", [])
        if (c.get("provider") == "pluggy" and c.get("provider_item_id"))
    ]
    items = [c["provider_item_id"] for c in conns]
    if items:
        _hold_aggregate_emails(user_id, "sync_user")

    results = [_sync_item_contido(c) for c in conns]

    # Agentes whole-portfolio: rodam UMA vez, depois de TODOS os itens sincronizarem,
    # pra não gravar um retrato parcial que o dedupe por período congelaria. Ficam
    # FORA do hook por-item de sync_pluggy_item de propósito.
    #   Faria Limer — agrega ações/FIIs de todas as conexões (dedupe rv_*:YYYY-MM);
    #   Barão       — agrega o saldo parado de todas as contas (dedupe parado:YYYY-MM).
    # Fail-soft por agente (nunca derruba o sync, e um não impede o outro).
    # O import fica DENTRO do try: piggy_agents faz conversões no nível do módulo
    # (ex.: int(AGENTS_INTERVAL_SEC)), então um env malformado explodiria aqui —
    # depois dos dados financeiros já persistidos — e derrubaria o sync inteiro.
    if results:
        for nome in ("faria_limer", "barao"):
            try:
                import core.services.piggy_agents as _agents
                getattr(_agents, f"run_{nome}_once")(user_id=user_id)
            except Exception as exc:
                print(f"[pluggy_sync] {nome} hook (user): {exc}")

    return {
        "ok": True,
        "items_synced": len(results),
        "accounts_synced": sum(r.get("accounts_synced", 0) for r in results),
        "transactions_synced": sum(r.get("transactions_synced", 0) for r in results),
        "launches_imported": sum((r.get("imported") or {}).get("inserted", 0) for r in results),
        "results": results,
    }


def refresh_and_sync_pluggy_user(
    user_id: int,
    *,
    wait_seconds: int | None = None,
    poll_interval: float | None = None,
) -> dict:
    """Refresh sob demanda: pede pra Pluggy re-buscar do banco (PATCH /items),
    espera a atualização concluir e então sincroniza. É o que o botão "Atualizar"
    do Open Finance chama.

    Por que não é só sincronizar: sem webhook (ambiente de dev), o PATCH é
    ASSÍNCRONO na Pluggy — ela re-busca do banco e só depois os dados novos ficam
    disponíveis. Se sincronizássemos na hora, leríamos o snapshot antigo (a
    transação que acabou de acontecer não estaria lá). Por isso esperamos o item
    sair de UPDATING (com teto de `wait_seconds`) antes de sincronizar.

    A RESPOSTA É POR ITEM E POR PRODUTO. Antes, o 404 do PATCH e o do
    `get_pluggy_item` eram engolidos (`except Exception: pass`), a função devolvia
    `ok: True, still_updating: 0` e a tela dizia "Tudo em dia!" para uma conexão
    morta. Agora cada item volta com estado, motivo e produtos, e o `ok` do topo é
    a conjunção: só é verdadeiro se TODOS os itens estiverem em `_ESTADOS_OK`.

    QUEM LEVA PATCH é decidido por `claim_manual_refresh`, não por "todo item do
    snapshot": conexão PAUSED/DELETED fica de fora (o item nem existe mais na
    Pluggy) e item refrescado há menos de `OF_MANUAL_REFRESH_COOLDOWN_SEC` volta
    como `rate_limited`. Isto passou a ser obrigatório quando o pull-to-refresh
    do app foi ligado neste caminho: antes o gesto só relia o snapshot, agora
    cada puxão pediria coleta nova em todos os bancos do usuário.
    """
    if wait_seconds is None:
        wait_seconds = _env_int("OF_REFRESH_WAIT_SEC", 18)
    if poll_interval is None:
        try:
            poll_interval = float(os.getenv("OF_REFRESH_POLL_SEC", "2.5"))
        except (TypeError, ValueError):
            poll_interval = 2.5

    t0 = time.monotonic()
    snapshot = get_open_finance_snapshot(user_id)
    conns = [
        c for c in snapshot.get("connections", [])
        if (c.get("provider") == "pluggy" and c.get("provider_item_id"))
    ]
    items = [c["provider_item_id"] for c in conns]
    institutions = {c["provider_item_id"]: c.get("institution_name") for c in conns}

    if not items:
        # Sem banco Pluggy (ex.: só conexão mock): nada a refrescar, só sincroniza.
        result = sync_pluggy_user(user_id)
        return {**result, "ok": True, "refreshed": 0, "waited": False, "still_updating": 0,
                "items": [], "duration_ms": int((time.monotonic() - t0) * 1000)}

    # Reivindica ANTES de falar com a Pluggy: o UPDATE condicional é o que
    # serializa a rajada (5 puxões seguidos = 1 PATCH) e o que exclui os status
    # terminais. Quem não foi reivindicado não leva PATCH nem espera.
    liberados = claim_manual_refresh(
        user_id, items,
        cooldown_sec=_env_int("OF_MANUAL_REFRESH_COOLDOWN_SEC", 120),
    )
    rate_limited = {i for i in items if i not in set(liberados)}

    if not liberados:
        # Dentro do cooldown: zero PATCH e zero espera — mas SINCRONIZA. O que o
        # cooldown protege é a cota de COLETA da Pluggy (só o PATCH a consome);
        # reler o que ela já tem é GET. Antes isto era early-return puro, e
        # bastava o tick periódico passar para o botão do usuário virar um
        # "já está tudo em dia" sem ter olhado nada.
        result = sync_pluggy_user(user_id)
        parado = _refresh_items_report(items, institutions, {},
                                       _reasons_do_sync(result), set(),
                                       rate_limited=rate_limited)
        return {**result, "ok": _todos_ok(parado), "refreshed": 0, "waited": False,
                "still_updating": 0, "items": parado,
                "duration_ms": int((time.monotonic() - t0) * 1000)}

    # Segura o e-mail dos agregados ANTES do PATCH: a espera do passo 2 leva até
    # wait_seconds (18s por padrão) e o touch de sync_pluggy_user só acontece
    # depois dela — essa janela inteira ficaria descoberta.
    _hold_aggregate_emails(user_id, "refresh_user")

    api_key = create_pluggy_api_key()

    # 1. Dispara o refresh na Pluggy pra cada item. Falha por item não trava o
    #    resto, mas PARA DE SER INVISÍVEL: vira motivo na resposta.
    triggered = 0
    patch_ok: dict[str, bool] = {}
    reasons: dict[str, str] = {}
    for item_id in liberados:
        try:
            update_pluggy_item(item_id, api_key)
            patch_ok[item_id] = True
            triggered += 1
        except Exception as exc:
            patch_ok[item_id] = False
            reasons[item_id] = "item_missing" if item_missing(exc) else "refresh_failed"

    # 2. Espera os itens saírem de UPDATING (Pluggy terminou de re-buscar do banco).
    #    time.sleep aqui é ok: a rota chama isto via asyncio.to_thread (fora do loop).
    #    Renova o hold a cada volta: wait_seconds é configurável (OF_REFRESH_WAIT_SEC)
    #    e pode passar do _SYNC_QUIET_MIN — sem renovar, o hold expiraria durante a
    #    própria espera e o e-mail poderia sair antes do sync final.
    deadline = time.monotonic() + max(0, wait_seconds)
    pending = set(liberados)
    while pending and time.monotonic() < deadline:
        _hold_aggregate_emails(user_id, "refresh_user")
        time.sleep(poll_interval)
        for item_id in list(pending):
            try:
                item = get_pluggy_item(item_id, api_key)
            except Exception as exc:
                pending.discard(item_id)  # erro de leitura não trava o fluxo
                reasons.setdefault(item_id, "item_missing" if item_missing(exc) else "read_failed")
                continue
            status = str(item.get("status") or "").upper()
            if status not in ITEM_UPDATING:
                pending.discard(item_id)

    # 3. Sincroniza (idempotente): puxa contas/transações novas e carimba o resultado.
    result = sync_pluggy_user(user_id)
    reasons.update(_reasons_do_sync(result))

    items_out = _refresh_items_report(items, institutions, patch_ok, reasons, pending,
                                      rate_limited=rate_limited)
    # `**result` PRIMEIRO: ele traz um `ok` próprio (o de sync_pluggy_user, que é
    # sempre True) e sobrescreveria o veredito por item se viesse depois.
    return {
        **result,
        "ok": _todos_ok(items_out),
        "refreshed": triggered,
        "waited": True,
        "still_updating": len(pending),
        "items": items_out,
        "duration_ms": int((time.monotonic() - t0) * 1000),
    }


# Estados que autorizam dizer "tudo em dia". LISTA DE PERMISSÃO, nunca de
# exclusão: um estado novo que este arquivo não conhece precisa nascer FALSO.
# `rate_limited` entra porque significa "acabou de ser atualizado", não "falhou".
_ESTADOS_OK = ("updated", "rate_limited")


def _todos_ok(items_out: list[dict]) -> bool:
    return all(i["state"] in _ESTADOS_OK for i in items_out)


def _reasons_do_sync(result: dict) -> dict[str, str]:
    """Motivo por item dos syncs que não deram certo (os dois caminhos usam)."""
    return {str(r.get("item_id")): str(r.get("reason"))
            for r in (result.get("results") or [])
            if not r.get("ok") and r.get("reason")}


# Estado SOBREPOSTO aqui → (rótulo, detalhe). O rótulo vem do banco via
# `connection_ui_state`; quando este relatório troca o `state`, o rótulo tem que
# ir junto — senão sai `state="partial"` com `label="Atualizado"`, que é
# armadilha para quem consumir isto depois. Só vale para o estado que ESTE
# arquivo sobrepõe: um "partial" vindo do `connection_ui_state` mantém o detalhe
# dele ("Cartão desatualizado desde 12/08"), que diz muito mais.
_OVERRIDE_LABEL = {
    "partial": ("Parcial", "Parte dos dados ainda não veio"),
    "rate_limited": ("Atualizado", "Atualizado há pouco"),
}


# Produto → como ele aparece na resposta quando está atrasado.
def _product_state(info: dict) -> str:
    if info.get("updated"):
        return "updated"
    last = str(info.get("last_updated_at") or "")[:10]
    return f"stale_since_{last}" if last else "stale"


def _refresh_items_report(items, institutions, patch_ok, reasons, pending,
                          *, rate_limited=()) -> list[dict]:
    """Estado por item DEPOIS do sync, lido do banco — que é onde o `mark_sync_result`
    acabou de gravar status, motivo e saúde. `connection_ui_state` é quem decide o
    estado; aqui só se junta com o resultado do PATCH e da espera."""
    out = []
    for item_id in items:
        rows = get_connections_by_item_id(item_id)
        row = rows[0] if len(rows) == 1 else {}
        ui = connection_ui_state(row)
        health = row.get("health") if isinstance(row.get("health"), dict) else {}
        state, label, detail = ui["state"], ui["label"], ui["detail"]
        if not patch_ok.get(item_id, True) and state == "updated":
            # PATCH falhou mas o espelho estava íntegro: não é "tudo em dia".
            state, (label, detail) = "partial", _OVERRIDE_LABEL["partial"]
        elif item_id in rate_limited and state == "updated":
            # Dentro do cooldown do manual: não pedimos coleta nova, e o que está
            # no banco é recente. Só sobrepõe o estado quando ele era verde — um
            # item perdido/pausado continua reportando o que ele é.
            state, (label, detail) = "rate_limited", _OVERRIDE_LABEL["rate_limited"]
        out.append({
            "item_id": item_id,
            "institution": institutions.get(item_id),
            "state": state,
            "label": label,
            "detail": detail,
            "products": {k: _product_state(v) for k, v in (health.get("products") or {}).items()},
            "reason": ("rate_limited" if state == "rate_limited"
                       else (reasons.get(item_id) or row.get("status_reason"))),
            "patch_ok": bool(patch_ok.get(item_id, False)),
            "still_updating": item_id in pending,
        })
    return out
