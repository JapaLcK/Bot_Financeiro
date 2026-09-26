# core/handlers/account.py
from __future__ import annotations
import db

# #607: o código já foi consumido — não mandar gerar outro.
_JUNCAO_RECUSADA = (
    "⚠️ Não deu pra vincular: sua conta do site e esta conta já têm dados cada uma, "
    "então não dá pra juntar as duas automaticamente.\n"
    "Pra ter tudo num lugar só, use este número numa conta só."
)


def link(platform: str, external_id: str, code: str | None) -> str:
    if not external_id:
        return "⚠️ Não consegui identificar seu ID nesta plataforma."

    if not code:
        # gera código para colar na outra plataforma
        uid = db.get_or_create_canonical_user(platform, external_id)
        link_code = db.create_link_code(uid, minutes_valid=10)
        return (
            f"🔗 Código de link: **{link_code}**\n"
            "Digite *link 123456* na outra plataforma para vincular (expira em 10 min)."
        )

    # consome código e vincula
    target_user_id = db.consume_link_code(code)
    if not target_user_id:
        return "❌ Código inválido ou expirado. Envie *link* para gerar um novo."

    try:
        db.link_platform_identity(platform, external_id, target_user_id)
    except db.MergeRefused:
        return _JUNCAO_RECUSADA
    return "✅ Contas vinculadas! Discord e WhatsApp agora usam os mesmos dados."


def vincular(platform: str, external_id: str, code: str) -> str:
    if not external_id:
        return "⚠️ Não consegui identificar seu ID nesta plataforma."

    target_user_id = db.consume_link_code(code)
    if not target_user_id:
        return "❌ Código inválido ou expirado. Gere um novo no site e tente novamente."

    try:
        db.link_platform_identity(platform, external_id, target_user_id)
    except db.MergeRefused:
        return _JUNCAO_RECUSADA
    platform_label = "WhatsApp" if platform == "whatsapp" else "Discord"
    return f"✅ {platform_label} vinculado à sua conta! Digite *ajuda* para ver os comandos."
