"""
scripts/generate_pii_keys.py — Gera as 2 chaves necessárias pra cifragem
column-level de PII (core/crypto.py).

Uso:
    python -m scripts.generate_pii_keys

Saída: 2 linhas prontas pra colar no .env (ou no Railway → Variables).

ATENÇÃO:
  - PII_ENCRYPTION_KEY é a chave Fernet (cifra/decifra). Pode ser rotacionada
    no futuro (mantendo a antiga como PII_ENCRYPTION_KEY_V1).
  - PII_HASH_PEPPER é o segredo HMAC do hash determinístico. NUNCA ROTACIONE
    — se mudar, todos os email_hash/phone_hash/external_id_hash existentes
    deixam de bater e os usuários não conseguem mais logar.

Guarde backup das 2 chaves num cofre fora do Railway (1Password, Bitwarden,
GCP Secret Manager). Sem elas, os PII cifrados ficam ilegíveis.
"""
from __future__ import annotations

import secrets

from cryptography.fernet import Fernet


def main() -> None:
    enc_key = Fernet.generate_key().decode("ascii")
    # 48 bytes = 64 chars base64; HMAC-SHA256 não exige tamanho específico
    # mas algo bem maior que 32 dá margem.
    pepper = secrets.token_urlsafe(48)

    print()
    print("# ── PII Encryption (core/crypto.py) ──────────────────────────────")
    print("# Cole no .env (local) ou em Railway → Variables (produção)")
    print()
    print(f"PII_ENCRYPTION_KEY={enc_key}")
    print(f"PII_HASH_PEPPER={pepper}")
    print()
    print("# ⚠️  GUARDE BACKUP DAS 2 CHAVES num cofre fora do Railway.")
    print("# ⚠️  PII_HASH_PEPPER NUNCA pode ser rotacionado em produção.")
    print()


if __name__ == "__main__":
    main()
