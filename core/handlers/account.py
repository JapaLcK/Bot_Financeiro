# core/handlers/account.py
from __future__ import annotations
import db
from core.crypto import hash_pii
from core.services.email_service import SUPPORT_EMAIL
from db.rate_limits import rate_limit_estourado

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
    "autoindicacao": (
        "⚠️ Não deu pra vincular: uma destas contas foi indicada pela outra no programa de "
        f"afiliados, e as duas não podem virar uma só. Fale com a gente em {SUPPORT_EMAIL}."
    ),
}

# #722 (D3): o código tem 6 dígitos e vale 15 min; sem teto, um número varre os
# 10^6. Conta TODA tentativa com código, inclusive a certa: um UPSERT antes do
# consumo, sem segundo passo. Quem tem o código certo acerta na 1ª.
# ponytail: teto por número; quem troca de número a cada 5 escapa. Teto global depois.
_TETO_CODIGO = (5, 15 * 60)
_MUITAS_TENTATIVAS = "Muitas tentativas de código. Espere 15 minutos e gere um código novo no site."


def _tentativas_estouradas(platform: str, external_id: str) -> bool:
    identificador = f"{platform}:{hash_pii(external_id, kind='external_id')}"  # sem telefone em claro
    return rate_limit_estourado("wa-link-code", identificador, *_TETO_CODIGO)


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
    if _tentativas_estouradas(platform, external_id):
        return _MUITAS_TENTATIVAS
    target_user_id = db.consume_link_code(code)
    if not target_user_id:
        return "❌ Código inválido ou expirado. Envie *link* para gerar um novo."

    try:
        db.link_platform_identity(platform, external_id, target_user_id)
    except db.MergeRefused as exc:
        return _RECUSA_POR_MOTIVO.get(exc.motivo, _RECUSA_COLISAO)
    return "✅ Contas vinculadas! Discord e WhatsApp agora usam os mesmos dados."


def vincular(platform: str, external_id: str, code: str) -> str:
    if not external_id:
        return "⚠️ Não consegui identificar seu ID nesta plataforma."

    if _tentativas_estouradas(platform, external_id):
        return _MUITAS_TENTATIVAS
    target_user_id = db.consume_link_code(code)
    if not target_user_id:
        return "❌ Código inválido ou expirado. Gere um novo no site e tente novamente."

    try:
        db.link_platform_identity(platform, external_id, target_user_id)
    except db.MergeRefused as exc:
        return _RECUSA_POR_MOTIVO.get(exc.motivo, _RECUSA_COLISAO)
    platform_label = "WhatsApp" if platform == "whatsapp" else "Discord"
    return f"✅ {platform_label} vinculado à sua conta! Digite *ajuda* para ver os comandos."
