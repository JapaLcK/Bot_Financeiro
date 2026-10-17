"""Apoio do PATCH /settings/{user_id}/security/contact (`settings.py`): leituras do
contato gravado, a senha atual exigida para trocar o e-mail e o aviso ao e-mail antigo."""

import asyncio
import logging

from fastapi import HTTPException

from db.connection import get_conn

logger = logging.getLogger(__name__)


def telefone_livre(telefone: str, user_id: int) -> bool:
    """O `telefone_livre` dos cadastros (§0.7): a variante com/sem o nono dígito do
    número de outra conta também está em uso. O índice único só vê o número exato,
    e continua de rede para a corrida entre esta leitura e o UPDATE (#630)."""
    from db_support import telefone_livre as _livre
    with get_conn() as conn, conn.cursor() as cur:
        return _livre(cur, telefone, exceto_user_id=user_id) is not None


def contato_atual(user_id: int) -> dict | None:
    """Hashes do contato gravado, direto do banco: o `get_auth_user` tem cache de
    10 s que pode estar velho (até depois de invalidar, `db_support.py:587-594`),
    e o "mudou?" decide o teto e o `telefone_livre` (#911). Hash é a forma da escrita."""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("select email_hash, phone_hash from auth_accounts where user_id = %s", (user_id,))
        return cur.fetchone()


async def exigir_senha_atual(user_id: int, current_password: str | None) -> bool:
    """Trocar o e-mail pede a senha atual (B0a). True = conta com credencial: o e-mail
    antigo recebe aviso. False = conta paga SEM credencial (quiz/funil): troca livre e
    sem aviso, é a saída "E-mail errado? Corrigir" de `criar-senha.js`. Conta só-Google
    leva o 409 de `_reauth_password_ok`, ANTES do 400 do campo vazio (o campo nem
    aparece para ela). Chamar DEPOIS do teto `settings-contact`: senha errada conta nele."""
    from db import conta_sem_credencial
    if await asyncio.to_thread(conta_sem_credencial, user_id):
        return False
    from frontend.finance_bot_websocket_custom import _reauth_password_ok
    if not await _reauth_password_ok(user_id, current_password or ""):
        if not current_password:
            raise HTTPException(status_code=400, detail="Confirme sua senha atual para trocar o e-mail.")
        # Sem WWW-Authenticate: o auth-refresh.js só renova 401 que o declara.
        raise HTTPException(status_code=401, detail="Senha incorreta.")
    return True


async def travar_e_ler_email(cur, user_id: int) -> dict | None:
    """Dentro da transação do PATCH: trava a linha de `auth_accounts` e lê o e-mail
    gravado. Dois PATCH concorrentes do mesmo usuário se serializam aqui: o segundo
    enxerga o e-mail já trocado pelo primeiro ("mudou?" e "antigo" decididos sob o lock)."""
    await cur.execute("select email_hash, email, email_enc from auth_accounts where user_id = %s for update",
                      (user_id,))
    return await cur.fetchone()


async def avisar_email_antigo(linha_antiga: dict | None, user_id: int, novo: str) -> None:
    """Best-effort, DEPOIS do commit: a decifragem do antigo e o envio só logam (sem
    endereço) e nunca desfazem a troca."""
    if not linha_antiga:
        return
    try:
        from core.services.email_service import send_email_changed_notice
        from core.services.retomada import mascarar_email
        from db.privacy import email_da_linha
        antigo = await asyncio.to_thread(email_da_linha, linha_antiga, user_id)
        if antigo and not await asyncio.to_thread(send_email_changed_notice, antigo, mascarar_email(novo)):
            logger.warning("aviso de troca de e-mail não enviado")
    except Exception as exc:  # noqa: BLE001
        logger.warning("aviso de troca de e-mail falhou: %s", type(exc).__name__)
