# core/handlers/account.py
from __future__ import annotations
import logging

import db
from core.crypto import hash_pii
from core.observability import log_system_event_sync
from core.services.email_service import SUPPORT_EMAIL
from db.rate_limits import rate_limit_estourado
from utils_phone import mask_phone

logger = logging.getLogger(__name__)

# Uma só para `link` e `vincular`, por plataforma. O código pode vir do site OU do `link` sem código
# na outra plataforma (que não exige conta no site), então o texto cobre os dois caminhos.
_CODIGO_INVALIDO_WHATSAPP = (
    "❌ Código inválido ou expirado. Gere um novo e me mande: *vincular <código>*. "
    "Se a conta é do site, toque em *Conectar WhatsApp* lá; se veio de outra plataforma, envie *link* nela."
)
_CODIGO_INVALIDO = (
    "❌ Código inválido ou expirado. Gere um novo e me mande: *link <código>*. "
    "Se a conta é do site, gere o código lá; se veio de outra plataforma, envie *link* nela."
)


def _codigo_invalido(platform: str) -> str:
    return _CODIGO_INVALIDO_WHATSAPP if platform == "whatsapp" else _CODIGO_INVALIDO


# #607: o código já foi consumido — não mandar gerar outro.
_JUNCAO_RECUSADA = (
    "⚠️ Não deu pra vincular: sua conta do site e esta conta já têm dados cada uma, "
    "então não dá pra juntar as duas automaticamente.\n"
    "Pra ter tudo num lugar só, use este número numa conta só."
)
# D6: cada motivo do `MergeRefused` com o seu texto. Motivo desconhecido cai na
# colisão, que é o texto genérico e verdadeiro.
_RECUSA_COLISAO = (
    "⚠️ Não deu pra vincular: as duas contas têm informações que não dá pra juntar "
    f"automaticamente. Fale com a gente em {SUPPORT_EMAIL}."
)
_RECUSA_POR_MOTIVO = {
    "dados_dos_dois_lados": _JUNCAO_RECUSADA,
    "open_finance": (
        "⚠️ A conta que você usa aqui tem banco conectado pelo Open Finance, e ela não pode "
        f"ser juntada a outra automaticamente. Fale com a gente em {SUPPORT_EMAIL}."
    ),
    "origem_presa": (
        "⚠️ A conta que você usa aqui tem ou teve assinatura no site, e ela não pode ser "
        f"juntada a outra automaticamente. Fale com a gente em {SUPPORT_EMAIL}."
    ),
    "apagaria_login": (
        "⚠️ Não vinculei: juntar as duas contas apagaria o login do site desta conta, e você "
        "perderia esse acesso. Por enquanto esse caso precisa do suporte: fale com a gente em "
        f"{SUPPORT_EMAIL}."
    ),
    "autoindicacao": (
        "⚠️ Não deu pra vincular: uma destas contas foi indicada pela outra no programa de "
        f"afiliados, e as duas não podem virar uma só. Fale com a gente em {SUPPORT_EMAIL}."
    ),
}

# #722 (D3): o código vale 15 min (token de 128 bits); o teto por número é defesa extra
# contra varredura. Conta TODA tentativa com código, inclusive a certa: um UPSERT antes do
# consumo, sem segundo passo. Quem tem o código certo acerta na 1ª.
# O teto global só alerta (log `link_code_global_ceiling` em db.users.consume_link_code).
_TETO_CODIGO = (5, 15 * 60)
_MUITAS_TENTATIVAS = "Muitas tentativas de código. Espere 15 minutos e gere um código novo (no site ou com *link* na outra plataforma)."


def _tentativas_estouradas(platform: str, external_id: str) -> bool:
    identificador = f"{platform}:{hash_pii(external_id, kind='external_id')}"  # sem telefone em claro
    return rate_limit_estourado("wa-link-code", identificador, *_TETO_CODIGO)


def _registrar_vinculo(platform: str, external_id: str, user_id: int) -> None:
    """Único contador do funil de onboarding desde que o telefone não liga mais (era
    `whatsapp_auto_link_success`). Só o número mascarado, nunca em claro."""
    if platform == "whatsapp":
        log_system_event_sync(
            "info",
            "whatsapp_code_link_success",
            "WhatsApp vinculado por código.",
            source="account",
            user_id=user_id,
            details={"wa_id": mask_phone(external_id)},
        )
        _avisar_dono_da_conta(external_id, user_id)


def _avisar_dono_da_conta(external_id: str, user_id: int) -> None:
    """Aviso de segurança (e-mail + push do app) de que um WhatsApp foi ligado à conta. Só o
    número mascarado. Falha aqui nunca desfaz o vínculo."""
    mascarado = mask_phone(external_id)
    try:
        email = (db.get_auth_user(user_id) or {}).get("email")
        if email:
            from core.services.email_service import send_whatsapp_linked_notice

            send_whatsapp_linked_notice(email, mascarado)
    except Exception as exc:
        logger.warning("aviso de vínculo por e-mail falhou: %s", type(exc).__name__, extra={"user_id": user_id})
    try:
        from core.services.push_service import send_push_to_user

        send_push_to_user(
            user_id, "WhatsApp vinculado",
            f"O número {mascarado} foi ligado à sua conta. Não foi você? Fale com o suporte.",
            collapse_id="wa-vinculado",
        )
    except Exception as exc:
        logger.warning("aviso de vínculo por push falhou: %s", type(exc).__name__, extra={"user_id": user_id})


def link(platform: str, external_id: str, code: str | None) -> str:
    if not external_id:
        return "⚠️ Não consegui identificar seu ID nesta plataforma."

    if not code:
        # gera código para colar na outra plataforma
        uid = db.get_or_create_canonical_user(platform, external_id)
        link_code = db.create_link_code(uid, minutes_valid=10)
        return (
            f"🔗 Código de link: **{link_code}**\n"
            f"Digite *link {link_code}* na outra plataforma para vincular (expira em 10 min)."
        )

    # consome código e vincula
    if _tentativas_estouradas(platform, external_id):
        return _MUITAS_TENTATIVAS
    target_user_id = db.consume_link_code(code)
    if not target_user_id:
        return _codigo_invalido(platform)

    try:
        db.link_platform_identity(platform, external_id, target_user_id)
    except db.MergeRefused as exc:
        return _RECUSA_POR_MOTIVO.get(exc.motivo, _RECUSA_COLISAO)
    _registrar_vinculo(platform, external_id, target_user_id)
    return "✅ Contas vinculadas! Este canal agora usa os dados da sua conta."


def vincular(platform: str, external_id: str, code: str) -> str:
    if not external_id:
        return "⚠️ Não consegui identificar seu ID nesta plataforma."

    if _tentativas_estouradas(platform, external_id):
        return _MUITAS_TENTATIVAS
    target_user_id = db.consume_link_code(code)
    if not target_user_id:
        return _codigo_invalido(platform)

    try:
        db.link_platform_identity(platform, external_id, target_user_id)
    except db.MergeRefused as exc:
        return _RECUSA_POR_MOTIVO.get(exc.motivo, _RECUSA_COLISAO)
    _registrar_vinculo(platform, external_id, target_user_id)
    if platform == "whatsapp":
        return (
            "✅ WhatsApp vinculado à sua conta! Já pode usar, por exemplo: "
            "*gastei 50 mercado*, *recebi 1000 salario*, *saldo* ou *ajuda*."
        )
    return "✅ Discord vinculado à sua conta! Digite *ajuda* para ver os comandos."
