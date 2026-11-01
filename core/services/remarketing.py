"""core/services/remarketing.py — o lote de envio da régua de remarketing (PR 6 de
docs/plano-remarketing.md). Inerte sem `REMARKETING_DESDE`: desligado não toca o banco.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone

_log = logging.getLogger(__name__)


def ligado() -> datetime | None:
    """O instante de `REMARKETING_DESDE` (ISO 8601 COM fuso) ou None: vazia, inválida ou sem fuso = desligado."""
    bruto = (os.getenv("REMARKETING_DESDE") or "").strip()
    if not bruto:
        return None
    try:
        desde = datetime.fromisoformat(bruto)
    except ValueError:
        return None
    return desde if desde.tzinfo is not None else None


def send_remarketing_email(user_id: int, to: str, etapa: int, token: str) -> bool:
    from core.services.email_service import (  # noqa: PLC0415
        EMAIL_FROM_FOUNDER, _public_base_url, make_unsub_url, send_email, unsub_headers)
    from core.services.remarketing_emails import montar  # noqa: PLC0415

    base = _public_base_url()
    link = f"{base}/retomar?t={token}&utm_source=email&utm_medium=remarketing&utm_campaign=rm{etapa}"
    unsub = make_unsub_url(user_id, to)
    assunto, html, texto = montar(etapa, link, unsub, base)
    return send_email(to, assunto, html, text_body=texto, from_addr=EMAIL_FROM_FOUNDER,
                      headers=unsub_headers(unsub), log_recipient=False)


def _send_whatsapp(telefone: str, etapa: int, token: str, template: str) -> bool:
    from adapters.whatsapp.wa_client import send_template  # noqa: PLC0415
    from utils_phone import normalize_phone_e164  # noqa: PLC0415

    lingua = (os.getenv("WA_TEMPLATE_REMARKETING_LANGUAGE") or "").strip() or "pt_BR"
    # `send_template` devolve None no 401 da Meta (não levanta): não é envio.
    return send_template(normalize_phone_e164(telefone), template, language_code=lingua,
                         url_button_param=token) is not None


def _checar(uid: int) -> tuple[dict | None, str | None]:
    """Releitura direta do banco antes de cada canal: (estado, None) ou (None, motivo) se barrado."""
    from db.remarketing import estado_fresco, ja_comprou  # noqa: PLC0415

    if ja_comprou(uid):
        return None, "comprou"
    est = estado_fresco(uid)
    return (None, "excluido") if est is None or est["saindo"] else (est, None)


def _pode(uid: int) -> dict | None:
    return _checar(uid)[0]


def _enviar(uid: int, etapa: int, token: str, motivos: list[str], res: dict) -> None:
    from db.remarketing import numero_de_outra_conta  # noqa: PLC0415

    est = _pode(uid)
    if est is None:
        motivos.append("email:barrado")
    elif est["opt_out_email"] or not est["email"]:
        motivos.append("email:sem_canal")
    else:
        try:
            res["email"] = send_remarketing_email(uid, est["email"], etapa, token)
        except Exception as exc:     # falha de e-mail não corta o WhatsApp
            res["email"] = False
            motivos.append(f"email:{type(exc).__name__}")

    est = _pode(uid)
    template = (os.getenv(f"WA_TEMPLATE_REMARKETING_{etapa}") or "").strip()
    if est is None:
        motivos.append("wa:barrado")
    elif est["origem"] != "assinar":
        motivos.append("wa:origem")
    elif est["opt_out_whatsapp"]:
        motivos.append("wa:opt_out")
    elif not est["telefone"]:
        motivos.append("wa:sem_telefone")
    elif not template:
        motivos.append("wa:sem_template")
    elif numero_de_outra_conta(uid, est["telefone"]):
        motivos.append("wa:outra_conta")
    else:
        try:
            res["wa"] = _send_whatsapp(est["telefone"], etapa, token, template)
        except Exception as exc:     # só o TIPO: str(exc) pode ecoar o número
            res["wa"] = False
            motivos.append(f"wa:{type(exc).__name__}")


def _um(uid: int, etapa: int, agora: datetime) -> None:
    from core.services.retomada import emitir_token  # noqa: PLC0415
    from db.remarketing import em_andamento, fechar, reivindicar  # noqa: PLC0415

    _, barrado = _checar(uid)
    if barrado is None and em_andamento(uid, agora):
        return                       # checkout aberto: adia sem reivindicar (curto, < 60 min)
    if not reivindicar(uid, etapa):
        return                       # outra réplica ganhou
    motivos: list[str] = [] if barrado is None else [barrado]
    res: dict = {}                   # o que já saiu, para o fechamento mesmo se algo levantar
    if barrado is None:              # comprou/excluído: fecha para sair da fila (`limit` do candidatos)
        try:
            _enviar(uid, etapa, emitir_token(uid, etapa), motivos, res)
        except Exception as exc:     # nunca deixa a linha presa em `enviando`; não reenvia
            motivos.append(f"erro:{type(exc).__name__}")
    for tentativa in (1, 2):         # fechar é o que tira a linha de `enviando`: 1 retentativa; na 2ª sobe ao log
        try:
            return fechar(uid, etapa, res.get("email"), res.get("wa"), ",".join(motivos) or None)
        except Exception:
            if tentativa == 2:
                raise


def rodar_uma_vez(agora: datetime | None = None) -> int:
    """Um tick do lote. Devolve quantos candidatos processou sem erro (0 desligado)."""
    desde = ligado()
    if desde is None:
        return 0
    from db.remarketing import candidatos  # noqa: PLC0415

    agora = agora or datetime.now(timezone.utc)
    feitos = 0
    for uid, etapa in candidatos(desde, agora):
        try:
            _um(uid, etapa, agora)
            feitos += 1
        except Exception as exc:     # uma falha não derruba o lote
            _log.warning("remarketing user_id=%s etapa=%s falhou: %s", uid, etapa, type(exc).__name__)
    return feitos
