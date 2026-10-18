"""
core/services/ai_chat/tools/extrato_ia.py — apagar e recategorizar um lançamento pela IA, pelo
MESMO caminho e pelo MESMO `pode` do Extrato (`db/lancamentos`, `api/v2/lancamentos.py`).

O id da escrita é o da lista do Extrato: `l<id>` (launches) ou `c<id>` (credit_transactions); a
tabela vai no próprio id, então `c42` nunca cai no lançamento 42. O inteiro vale SÓ como o `#N` que
o usuário vê (`user_seq`): nunca como `launches.id` nem como id de compra. `CC<n>` (o código que o
WhatsApp mostra na compra) é `c<n>`; `PC…` (parcelamento) é a 1ª parcela do grupo, `c<n>`.

Fluxo: `validar_*` recusa pelo `pode` ANTES de armar (e diz o que dá para fazer); `ao_confirmar` troca
o id do modelo pelo canônico e grava `_item` (descrição, valor e data LIDOS DO BANCO) na pendência, de
onde sai o resumo; `apagar`/`recategorizar` (o "sim") relêem pelo id e só aceitam a forma canônica: o
que mudou ou sumiu desde a pergunta não vira ação em outro item (o id serial não se reaproveita), e o
inteiro de uma pendência armada antes desta versão é recusado, não executado.
"""
from __future__ import annotations

import logging
import re
from typing import Any

import db
from core.observability import _log_falha
from core.pg_text import violacao_finito
from db import lancamentos, lancamentos_leitura
from db.cards import MSG_COMPRA_DO_BANCO
from db.estabelecimentos import SEM_DESCRICAO
from utils_text import fmt_brl

# A causa vai pro LOG, nunca pro usuário: o texto do psycopg pode trazer o valor e a descrição da
# linha, e a mensagem genérica ainda tem de dizer o que fazer.
ERRO_APAGAR = (
    "🐷 Não consegui apagar agora — deu um erro do meu lado. "
    "Tenta de novo em alguns minutos; se continuar, me chama que eu vejo isso."
)
_FALTOU_ID = "🐷 Faltou o ID do lançamento."
_NAO_ACHEI = ("🐷 Não achei esse lançamento. Manda o `id` da lista ou o #N, ou pede pra eu listar os "
              "últimos e você escolhe.")
_SUMIU = "🐷 Não achei mais esse lançamento (pode já ter sido apagado). Nada mudou."
_ANTIGA = ("🐷 Essa confirmação é de antes de uma atualização minha e não executei nada. "
           "Me pede de novo?")
_CC = re.compile(r"CC([0-9]{1,18})", re.IGNORECASE)
_PC = re.compile(r"PC[0-9A-F]{8}", re.IGNORECASE)
_SEQ = re.compile(r"[0-9]{1,9}")  # [0-9] e nunca \d: "١٢" não é o #N

# Recusas de domínio do `delete_launch_and_rollback` (a frase do WhatsApp mora em
# `core/handlers/pending.py::_RECUSAS_APAGAR`, com `**#N**`; aqui o item é descrito pelo que ele é).
_RECUSAS = (
    (db.LaunchNoEffects, "delete_launch_sem_efeitos",
     "é antigo e não guarda o que precisaria ser revertido, então mantive ele intacto pra não bagunçar seu saldo."),
    (db.InvestmentLotHasWithdrawal, "delete_launch_lote_com_resgate",
     "esse lote já teve resgate. Apaga o resgate primeiro e depois volta aqui pra apagar o aporte."),
    (db.LaunchUnsafeRollback, "delete_launch_inseguro",
     "não consigo reverter com segurança, então mantive ele intacto pra não bagunçar seu saldo."),
)


def _achar(user_id: int, bruto: Any) -> tuple[str, int] | None:
    """A forma que o modelo mandou -> ("l"|"c", id), ou None. Só o que é DO usuário resolve."""
    t = str(bruto if bruto is not None else "").strip().removeprefix("#").strip()
    if ident := lancamentos.ler_id(t.lower()):
        return ident
    if _SEQ.fullmatch(t):  # o `#N` (user_seq), nunca o id interno
        lid = db.resolve_user_seq_to_id(user_id, int(t))
        return ("l", lid) if lid else None
    if m := _CC.fullmatch(t):
        return "c", int(m[1])
    if _PC.fullmatch(t) and (grupo := db.resolve_installment_group_id(user_id, t)):
        with db.get_conn() as conn, conn.cursor() as cur:
            cur.execute("select min(id) as id from credit_transactions "
                        "where user_id = %s and group_id = %s::uuid", (user_id, grupo))
            row = cur.fetchone()
            conn.commit()
        return ("c", row["id"]) if row and row["id"] else None
    return None


def _resumo_item(item: dict) -> dict:
    """O que a pendência guarda do item: lido do banco, com `valor` em float (o `ai_guard` o lê)."""
    p = item["parcela"]
    return {"descricao": (item["descricao"] or item["mensagem"] or "").strip()[:60] or SEM_DESCRICAO,
            "valor": float(item["valor"]), "data": item["data"],
            "parcelas": p["total"] if p and p["total"] > 1 else 1}


def _quem(i: dict) -> str:
    d = i["data"]
    return f'"{i["descricao"]}", {fmt_brl(i["valor"])}, {d[8:10]}/{d[5:7]}'


def _pode(item: dict, campo: str) -> bool:
    # Apagar é só `l`, como a rota da v2: a compra no cartão não apaga (o `pode` dela nem tem `apagar`).
    return campo in item["pode"] and not (campo == "apagar" and item["id"][0] == "c")


def _recusa(item: dict, campo: str) -> str | None:
    """None = pode. Senão o motivo e o que a IA ainda faz com esse item (nada de apontar outro canal)."""
    if _pode(item, campo):
        return None
    if campo == "apagar":
        motivo = MSG_COMPRA_DO_BANCO if item["origem"] == "cartao" else "Esse não dá pra apagar por aqui."
        resto = " Se quiser, mudo a categoria." if _pode(item, "categoria") else ""
    else:
        motivo = "Esse não dá pra mudar a categoria por aqui."
        resto = " Se quiser, apago ele." if _pode(item, "apagar") else ""
    return f"🐷 {motivo}{resto or ' Ele é só de leitura pra mim.'}"


def _validar(user_id: int, args: dict[str, Any], campo: str) -> str | None:
    if not str(args.get("launch_id") or "").strip():
        return _FALTOU_ID
    alvo = _achar(user_id, args.get("launch_id"))
    item = lancamentos_leitura.um_item(user_id, *alvo) if alvo else None
    return _NAO_ACHEI if item is None else _recusa(item, campo)


def validar_apagar(user_id: int, args: dict[str, Any]) -> str | None:
    return _validar(user_id, args, "apagar")


def validar_categoria(user_id: int, args: dict[str, Any]) -> str | None:
    return _validar(user_id, args, "categoria")


def ao_confirmar(user_id: int, args: dict[str, Any]) -> dict[str, Any]:
    """Depois do `validate`: id canônico + o item como o banco o tem agora. Se sumiu neste meio
    tempo, segue sem `_item` (o `execute` relê e responde "não achei mais")."""
    alvo = _achar(user_id, args.get("launch_id"))
    item = lancamentos_leitura.um_item(user_id, *alvo) if alvo else None
    return args if item is None else {**args, "launch_id": item["id"], "_item": _resumo_item(item)}


def resumo_apagar(args: dict[str, Any]) -> str:
    return f"apagar {_quem(args['_item'])}" if args.get("_item") else "apagar o lançamento"


def resumo_categoria(args: dict[str, Any]) -> str:
    i, nova = args.get("_item"), args.get("new_category")
    if not i:
        return f'mudar a categoria do lançamento para "{nova}"'
    parcelas = f" (vale para as {i['parcelas']} parcelas)" if i["parcelas"] > 1 else ""
    return f'mudar a categoria de {_quem(i)} para "{nova}"{parcelas}'


def _reler(user_id: int, args: dict[str, Any], campo: str):
    """O "sim": só a forma canônica, relida agora. (tabela, id, item) ou a frase de recusa."""
    ident = lancamentos.ler_id(str(args.get("launch_id") or ""))
    if ident is None:
        return _ANTIGA
    item = lancamentos_leitura.um_item(user_id, *ident)
    if item is None:
        return _SUMIU
    if not _pode(item, campo):
        acao = "apagar" if campo == "apagar" else "mudar a categoria"
        return (f"🐷 Esse lançamento mudou desde a pergunta e não dá mais pra {acao} por aqui. "
                f"Não {'apaguei' if campo == 'apagar' else 'mudei'} nada.")
    return (*ident, item)


def apagar(user_id: int, args: dict[str, Any]) -> str:
    r = _reler(user_id, args, "apagar")
    if isinstance(r, str):
        return r
    _, n, item = r  # `l`: `_pode` já recusou o `c`
    quem = _quem(_resumo_item(item))
    try:
        aviso = db.delete_launch_and_rollback(user_id, n, exigir_pode=True)
    except LookupError:
        return _SUMIU
    except lancamentos.NaoEditavel:  # mudou entre a releitura e o lock
        return "🐷 Esse lançamento mudou desde a pergunta e não dá mais pra apagar por aqui. Não apaguei nada."
    except Exception as e:
        if violacao_finito(e):
            raise  # runner responde MSG_VALOR_WHATSAPP
        for classe, evento, frase in _RECUSAS:
            if isinstance(e, classe):
                _log_falha(evento, user_id, e, nivel=logging.WARNING, launch_id=n)
                return f"🐷 Sobre {quem}: {frase}"
        _log_falha("delete_launch", user_id, e, launch_id=n)
        return ERRO_APAGAR
    # aviso: apagou a fundida, o banco volta para a lista (P3)
    return f"🗑️ Apagado: {quem}. {aviso or 'Saldo revertido.'}"


def recategorizar(user_id: int, args: dict[str, Any]) -> str:
    from db.accounts import LaunchDateLockedError, update_launch_fields
    from db.cards import update_credit_transaction_fields
    from db.categories import ensure_user_category, resolve_category_input

    r = _reler(user_id, args, "categoria")
    if isinstance(r, str):
        return r
    tabela, n, item = r
    nova = resolve_category_input(user_id, args.get("new_category") or "", create=True)
    if not nova:
        return "🐷 Faltou a categoria nova. Manda de novo."
    try:
        if tabela == "l":
            ok = update_launch_fields(user_id, n, categoria=nova, exigir_pode=True)
        else:  # propaga ao parcelamento inteiro
            ok = update_credit_transaction_fields(user_id, n, categoria=nova, exigir_pode=True)
    except LaunchDateLockedError as exc:  # trava de categoria da declarada pelo banco
        return f"🐷 {exc}"
    except lancamentos.NaoEditavel:
        return "🐷 Esse lançamento mudou desde a pergunta e não dá mais pra mudar a categoria por aqui. Não mudei nada."
    if not ok:
        return _SUMIU
    # criar só depois do UPDATE: id inexistente não pode deixar categoria órfã no catálogo.
    ensure_user_category(user_id, nova)
    i = _resumo_item(item)
    parcelas = f" (as {i['parcelas']} parcelas)" if i["parcelas"] > 1 else ""
    return f"✅ {_quem(i)}{parcelas} agora é {nova}."
