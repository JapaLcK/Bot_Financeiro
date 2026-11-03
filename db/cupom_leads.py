"""db/cupom_leads.py — leads do pop-up do cupom (POST /cupom). Uma linha por e-mail."""

from __future__ import annotations

from .connection import get_conn


def registrar(email: str, consent_text: str, origem: str, ip_hash: str) -> bool:
    """True só se o e-mail é novo; repetido não toca a linha (a prova do 1º consentimento fica)."""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "insert into cupom_leads (email, consent_text, origem, ip_hash) values (%s, %s, %s, %s)"
            " on conflict (email) do nothing returning email",
            (email, consent_text, origem, ip_hash),
        )
        return cur.fetchone() is not None


def marcar_enviado(email: str) -> None:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("update cupom_leads set sent_at = now() where email = %s", (email,))


def remover(email: str) -> None:
    """Envio falhou: apaga para a próxima tentativa do visitante contar como nova."""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("delete from cupom_leads where email = %s and sent_at is null", (email,))
