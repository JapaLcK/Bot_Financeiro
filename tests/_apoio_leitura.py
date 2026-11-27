"""Apoio dos testes da sessão de leitor (credencial de visualização)."""
import re
import uuid

import db
import frontend.finance_bot_websocket_custom as dashboard
from _apoio_auth_app import req


def usuario_novo() -> str:
    return f"leitor.{uuid.uuid4().hex[:10]}"


SENHA_LEITOR = "senha-de-leitura-1"


def leitor_de(uid: int, usuario: str | None = None, senha: str = SENHA_LEITOR) -> dict:
    """Define a credencial de leitura (versão +1) e emite uma sessão de leitor REAL pelo emissor único."""
    usuario = usuario or usuario_novo()
    versao = db.definir_credencial_leitura(uid, usuario, senha)
    access, jti, refresh = dashboard._issue_session_token(
        uid, "", req("/auth/leitura/login"), leitura_versao=versao)
    dash = dashboard.make_dashboard_token(uid, hours=dashboard.DASHBOARD_SESSION_HOURS, jti=jti, ro=True)
    return {"usuario": usuario, "senha": senha, "versao": versao, "access": access, "jti": jti,
            "refresh": refresh, "dashboard": dash, "bearer": {"Authorization": f"Bearer {access}"}}


def url_de(path: str) -> str:
    """Rota com parâmetros -> URL concreta (`{user_id}`, `{x:path}`...)."""
    return re.sub(r"\{[^}]+\}", "1", path)
