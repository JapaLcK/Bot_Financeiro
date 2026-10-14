"""PKCE (RFC 7636), só o método S256, para o login Google do app nativo.

O app gera um `code_verifier`, manda `S256(verifier)` no start e o verifier na
troca: um app que interceptou o `pigbank://auth?code=` (esquema customizado não
tem dono único) não tem o verifier e não consegue trocar o código. Sem `plain`:
ele devolveria o próprio segredo ao navegador.
"""
import base64
import hashlib
import re

# SHA-256 em base64url sem padding: sempre 43 caracteres.
_CHALLENGE = re.compile(r"[A-Za-z0-9_-]{43}")
# RFC 7636 §4.1, usado como `Field(pattern=...)` nos corpos das rotas.
VERIFIER_PATTERN = r"^[A-Za-z0-9\-._~]{43,128}$"


def challenge_valido(challenge: str) -> bool:
    return _CHALLENGE.fullmatch(challenge) is not None


def s256(verifier: str | None) -> str | None:
    """`challenge` do verifier; None se não veio verifier."""
    if verifier is None:
        return None
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
