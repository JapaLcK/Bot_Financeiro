"""Auto-indicação (#1506): o indicado compartilha telefone ou chave Pix com o afiliado?

Sem HTTP, dentro da transação de `_comissao_da_primeira`. Só compara; quem bloqueia é o chamador.
Telefone: `auth_accounts.phone_hash` e `user_identities.external_id_hash` são o mesmo HMAC dos
dígitos (kind phone/external_id só dão strip), então cruzam entre si. Pix: a chave cifrada do
afiliado é decifrada (grava `pii_access_log`) e comparada por hash com e-mail/telefone do indicado
e, se ele também é afiliado, por valor canônico com a chave dele.
`candidatos_simetricos` (não `phone_lookup_candidates`): bloquear por número de outro dono (fixo, #909) é falso positivo em dinheiro.
ponytail: telefone × telefone é por hash exato (sem a variante do 9º dígito, que exigiria o claro);
telefone do afiliado = chave Pix de indicado que não é afiliado não é detectável (a chave dele não existe).
"""
import re

from core.crypto import PiiAccessContext, decrypt_pii_optional, hash_pii
from utils_phone import candidatos_simetricos, normalize_phone_e164


def _telefones(cur, uid: int) -> set[str]:
    cur.execute("select phone_hash as h from auth_accounts where user_id = %s and phone_hash is not null"
                " union select external_id_hash from user_identities"
                " where user_id = %s and provider = 'whatsapp' and external_id_hash is not null",
                (int(uid), int(uid)))
    return {r["h"] for r in cur.fetchall()}


def _email(cur, uid: int) -> set[str]:
    cur.execute("select email_hash as h from auth_accounts where user_id = %s and email_hash is not null",
                (int(uid),))
    return {r["h"] for r in cur.fetchall()}


def _chave(cur, uid: int) -> str | None:
    """Chave Pix do afiliado `uid`, decifrada (auditada) e canônica; None sem chave."""
    cur.execute("select pix_key_enc from affiliates where user_id = %s and pix_key_enc is not null", (int(uid),))
    row = cur.fetchone()
    if not row:
        return None
    k = (decrypt_pii_optional(row["pix_key_enc"], ctx=PiiAccessContext(
        purpose="afiliado_vinculo", actor="system:afiliado", subject_user_id=int(uid), field="pix_key")) or "").strip()
    if "@" in k:
        return k.lower()
    if re.fullmatch(r"[\d\s().+/-]+", k):
        d = re.sub(r"\D", "", k)
        try:
            return normalize_phone_e164(d)
        except ValueError:
            return d  # CPF/CNPJ fora do formato de telefone
    return k.lower() or None  # aleatória


def _hashes_da_chave(chave: str) -> set[str]:
    if "@" in chave:
        return {hash_pii(chave, kind="email")}
    if chave.isdigit():
        try:
            return {hash_pii(c, kind="phone") for c in candidatos_simetricos(chave)}
        except ValueError:
            pass
    return set()


def compartilha_identificador(cur, affiliate_user_id: int, referred_user_id: int) -> str | None:
    """'telefone', 'pix' ou None. Toda leitura filtra por `user_id` (um lado, depois o outro)."""
    if _telefones(cur, affiliate_user_id) & _telefones(cur, referred_user_id):
        return "telefone"
    chave = _chave(cur, affiliate_user_id)
    if not chave:
        return None
    if _hashes_da_chave(chave) & (_telefones(cur, referred_user_id) | _email(cur, referred_user_id)):
        return "pix"
    return "pix" if _chave(cur, referred_user_id) == chave else None
