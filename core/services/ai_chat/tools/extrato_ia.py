"""
core/services/ai_chat/tools/extrato_ia.py — apagar e recategorizar um lançamento pela IA, pelo
MESMO caminho e pelo MESMO `pode` do Extrato (`db/lancamentos`, `api/v2/lancamentos.py`).

O id da escrita é o da lista do Extrato: `l<id>` (launches) ou `c<id>` (credit_transactions); a
tabela vai no próprio id, então `c42` nunca cai no lançamento 42. O inteiro vale SÓ como o `#N` que
o usuário vê (`user_seq`): nunca como `launches.id` nem como id de compra. `CC<n>` (o código que o
WhatsApp mostra na compra) é `c<n>`; `PC…` (parcelamento: o código, o uuid do grupo ou os 8 hex com letra) é a 1ª parcela do grupo, `c<n>`.
Toda tool que entrega id ao modelo o entrega nesta forma (`l`/`c`/`PC`): `list_recent_launches`,
`get_uncategorized_launches`, `get_open_bill` (`c<id>`) e `list_installments` (`codigo`).

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
from db.categories import resolve_category_input
from db.estabelecimentos import SEM_DESCRICAO
from utils_text import fmt_brl

# A causa vai pro LOG, nunca pro usuário: o texto do psycopg pode trazer o valor e a descrição da
# linha, e a mensagem genérica ainda tem de dizer o que fazer.
ERRO_APAGAR = (
    "🐷 Não consegui apagar agora — deu um erro do meu lado. "
    "Tenta de novo em alguns minutos; se continuar, me chama que eu vejo isso."
)
_FALTOU_ID = "🐷 Faltou o ID do lançamento."
_FALTOU_CATEGORIA = "🐷 Faltou a categoria nova. Qual categoria você quer?"
_NAO_ACHEI = ("🐷 Não achei esse lançamento. Manda o `id` da lista ou o #N, ou pede pra eu listar os "
              "últimos e você escolhe.")
_SUMIU = "🐷 Não achei mais esse lançamento (pode já ter sido apagado). Nada mudou."
_ANTIGA = ("🐷 Essa confirmação é de antes de uma atualização minha e não executei nada. "
           "Me pede de novo?")
_CC = re.compile(r"CC([0-9]{1,18})", re.IGNORECASE)
# Parcelamento: `PC`+8 hex (o código que a lista entrega) ou o uuid inteiro. Os 8 hex SEM `PC` não valem:
# `c1234567` colidiria com o id `c<id>` e `cc123456` com o `CC<id>` (a compra de outro grupo do usuário).
_PC = re.compile(r"PC[0-9A-F]{8}|[0-9A-F]{8}(?:-[0-9A-F]{4}){3}-[0-9A-F]{12}", re.IGNORECASE)
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


def _compra(user_id: int, n: int) -> dict | None:
    """`source` e `is_refund` da compra `n` DO usuário (o item do Extrato não traz a fonte), ou None."""
    with db.get_conn() as conn, conn.cursor() as cur:
        cur.execute("select source, is_refund from credit_transactions where user_id = %s and id = %s",
                    (user_id, n))
        row = cur.fetchone()
        conn.commit()
    return row


def _pago_pelo_banco(user_id: int, n: int) -> bool:
    """O pagamento de fatura que o banco fez (Open Finance ativo: `funding_source` bank e `delta_conta` 0), pelo
    EFEITO gravado do usuário. O pagamento antigo que saiu da Carteira (`delta_conta` != 0) não é este."""
    with db.get_conn() as conn, conn.cursor() as cur:
        cur.execute("select efeitos from launches where user_id = %s and id = %s", (user_id, n))
        row = cur.fetchone()
        conn.commit()
    e = (row or {}).get("efeitos") or {}
    return (e.get("funding_source") or {}).get("kind") == "bank" and not e.get("delta_conta")


def _motivo(user_id: int, i: dict) -> str:
    """Por que o item está fora do `pode`, em linguagem de produto (um texto por estado do Extrato)."""
    if i["guardado"]:
        return "Esse é um movimento de caixinha ou de investimento (guardar ou resgatar), e eu não apago nem mudo isso."
    o = i["origem"]
    if i["id"][0] == "c":
        if o == "cartao":
            return MSG_COMPRA_DO_BANCO
        veio = "veio de um extrato importado" if (_compra(user_id, int(i["id"][1:])) or {}).get("source") == "ofx" \
            else "foi lançada por você"
        return f"Essa compra no cartão {veio}, e mexer nela mexe nas parcelas e na fatura."
    if o == "registro_antigo":
        if i["categoria"] == "pagamento_fatura" and _pago_pelo_banco(user_id, int(i["id"][1:])):
            return "Esse é o pagamento de uma fatura que saiu direto do seu banco, e não mexeu no saldo da sua Carteira, então não tenho o que reverter."
        return "Esse registro não guarda o efeito dele no seu saldo (é antigo, veio de um extrato importado ou não mexeu na sua conta), e eu não tenho o que reverter."
    if o == "banco":
        return "Esse lançamento veio do seu banco (Open Finance): o banco é dono do valor e da data."
    return ("Esse é o pagamento de uma conta ou fatura: apagar devolveria o dinheiro e a conta seguiria paga."
            if "categoria" in i["pode"] else
            "Esse é um saque ou depósito em dinheiro ligado ao seu banco, e a categoria dele é fixa.")


def _recusa(user_id: int, item: dict, campo: str) -> str | None:
    """None = pode. Senão o motivo e o que a IA ainda faz com esse item (nada de apontar outro canal)."""
    if _pode(item, campo):
        return None
    outro, oferta = ("categoria", "mudo a categoria") if campo == "apagar" else ("apagar", "apago ele")
    resto = f" Se quiser, {oferta}." if _pode(item, outro) else " Ele é só de leitura pra mim."
    return f"🐷 {_motivo(user_id, item)}{resto}"


# Registro de `launches` que o Extrato não lista (criar/apagar caixinha e investimento): a IA o recusa
# como o Extrato (P-1), mas com o motivo, não com "não achei".
_CAIXINHA_INVEST = ("criar_caixinha", "create_investment", "delete_investment")


def _fora_do_extrato(user_id: int, alvo: tuple[str, int]) -> str | None:
    """`um_item` devolveu None: se o registro é DO usuário e o Extrato não o lista, diz por quê."""
    if alvo[0] == "c":  # o estorno: o Extrato filtra (`MES_CARTAO_SQL`)
        row = _compra(user_id, alvo[1])
        quem = "Esse é o estorno de uma compra no cartão" if row and row["is_refund"] else None
    else:
        with db.get_conn() as conn, conn.cursor() as cur:
            cur.execute("select tipo from launches where user_id = %s and id = %s", (user_id, alvo[1]))
            row = cur.fetchone()
            conn.commit()
        quem = row and ("Esse é o registro de criar ou apagar uma caixinha ou um investimento"
                        if row["tipo"] in _CAIXINHA_INVEST else "Esse tipo de registro")
    if not quem:
        return None
    return f"🐷 {quem} e não aparece no Extrato, então não apago nem mudo por aqui. Eu mexo em gastos e receitas."


def _validar(user_id: int, args: dict[str, Any], campo: str) -> str | None:
    if not str(args.get("launch_id") or "").strip():
        return _FALTOU_ID
    alvo = _achar(user_id, args.get("launch_id"))
    item = lancamentos_leitura.um_item(user_id, *alvo) if alvo else None
    if item is None:
        return (alvo and _fora_do_extrato(user_id, alvo)) or _NAO_ACHEI
    return _recusa(user_id, item, campo)


def validar_apagar(user_id: int, args: dict[str, Any]) -> str | None:
    return _validar(user_id, args, "apagar")


def validar_categoria(user_id: int, args: dict[str, Any]) -> str | None:
    nova = args.get("new_category")
    # NUL: o jsonb da pendência o recusa (DataError em `ai_set_pending_action`, que `chat()` repropaga).
    if not isinstance(nova, str) or "\x00" in nova or not resolve_category_input(user_id, nova, create=True):
        return _FALTOU_CATEGORIA  # antes de armar "mudar para None"
    return _validar(user_id, args, "categoria")


def ao_confirmar(user_id: int, args: dict[str, Any]) -> dict[str, Any]:
    """Depois do `validate`: id canônico + o item como o banco o tem agora. Se sumiu neste meio
    tempo, segue só com o id canônico, sem `_item` (o `execute` relê e responde "não achei mais")."""
    alvo = _achar(user_id, args.get("launch_id"))
    if alvo is None:
        return args
    item = lancamentos_leitura.um_item(user_id, *alvo)
    if item is None:  # sumiu agora: o id canônico faz o "sim" responder "não achei mais"
        return {**args, "launch_id": f"{alvo[0]}{alvo[1]}"}
    return {**args, "launch_id": item["id"], "_item": _resumo_item(item)}


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
    nada = "Não apaguei nada." if campo == "apagar" else "Não mudei nada."
    if not _pode(item, campo):
        acao = "apagar" if campo == "apagar" else "mudar a categoria"
        return f"🐷 Esse lançamento mudou desde a pergunta e não dá mais pra {acao} por aqui. {nada}"
    # Sem `_item` não há o que comparar (o `ao_confirmar` não achou o item, que agora reapareceu): recusa.
    # ponytail: a comparação roda fora do lock; edição do próprio usuário no mesmo ms do "sim" passa.
    if args.get("_item") != _resumo_item(item):  # editado depois da pergunta
        return f"🐷 Esse lançamento mudou desde a pergunta. Me pede de novo. {nada}"
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
    from db.categories import ensure_user_category

    r = _reler(user_id, args, "categoria")
    if isinstance(r, str):
        return r
    tabela, n, item = r
    nova = resolve_category_input(user_id, args.get("new_category") or "", create=True)
    if not nova:
        return _FALTOU_CATEGORIA
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
