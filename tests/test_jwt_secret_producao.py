"""N7: em produção o monólito recusa subir com JWT_SECRET de dev/CI/doc ou curto.

O boot é de verdade (subprocesso importando o monólito): a guarda roda no import,
então chamar só `motivo_jwt_secret_fraco` não provaria que alguém a chama.
"""
import os
import subprocess
import sys
from pathlib import Path

import pytest

from token_utils import motivo_jwt_secret_fraco

RAIZ = Path(__file__).resolve().parent.parent

# Os valores de JWT_SECRET escritos no repo (CI, conftest, hook, .env.example,
# docs, scripts) — `git grep -n JWT_SECRET`.
LITERAIS_DO_REPO = [
    "dev-only-jwt-secret-32-bytes-minimum-len",
    "test-jwt-secret-for-ci-only-32-bytes",
    "test-jwt-secret-for-pytest-only-32-bytes",
    "troque-por-uma-string-longa-e-aleatoria",
    "qualquer-coisa-com-32-bytes-para-teste",
    "sonda-321-jwt-secret-com-32-bytes-ok",
    "synthetic-agent-chat-replay-only-32-bytes",
]
# Gerados UMA vez (token_urlsafe(48) e token_hex(32)) e conferidos sem marca.
FORTE_URLSAFE = "OP3LXV1Wq9f82BTsRs1zm4xlgqEXZ8D8NpeG9xKs2V0ms74JBI_6y1FuR4vneS_D"
FORTE_HEX = "3ac565fb43bba668677df7d29d8c7fb725b34a23388efea6ae31c8466fec0c74"


@pytest.mark.parametrize("valor", LITERAIS_DO_REPO + ["a" * 31])
def test_valor_de_dev_ou_curto_e_fraco(valor):
    assert motivo_jwt_secret_fraco(valor) is not None


# Uma marca por caso: nos literais do repo as marcas se sobrepõem (todo `test`
# vem junto de `secret` ou `bytes`), então tirar uma delas não falhava nada.
@pytest.mark.parametrize("marca", ["secret", "test", "troque", "bytes", "changeme"])
def test_cada_marca_sozinha_e_fraca(marca):
    assert motivo_jwt_secret_fraco(marca + "x" * 30) is not None


# "a"*32 é a fronteira exata (o "a"*31 acima é fraco) e não contém marca.
@pytest.mark.parametrize("valor", [FORTE_URLSAFE, FORTE_HEX, "a" * 32])
def test_valor_aleatorio_passa(valor):
    assert motivo_jwt_secret_fraco(valor) is None


# Mesmo preâmbulo de `tests/_lifespan_probe.py`: `ROOT_DIR` vazio impede o
# `.env` do disco de entrar (senão ele reporia JWT_SECRET/APP_ENV por baixo).
_BOOT = ("import pathlib,tempfile,config.env; "
         "config.env.ROOT_DIR=pathlib.Path(tempfile.mkdtemp()); "
         "import frontend.finance_bot_websocket_custom as m; "
         "print('BOOT_OK', hasattr(m, 'app'))")


def _subir(app_env: str, segredo: str) -> subprocess.CompletedProcess:
    env = {
        "PATH": os.environ.get("PATH", ""),
        "HOME": os.environ.get("HOME", ""),
        "PYTHONPATH": str(RAIZ),
        "DATABASE_URL": "postgresql://nada.invalid:1/x",
        "JWT_SECRET": segredo,
        "APP_ENV": app_env,
    }
    return subprocess.run([sys.executable, "-c", _BOOT], cwd=RAIZ, env=env,
                          capture_output=True, text=True, timeout=120)


def test_boot_prod_recusa_segredo_do_ci_sem_vazar_o_valor():
    segredo = "test-jwt-secret-for-ci-only-32-bytes"
    proc = _subir("prod", segredo)
    assert proc.returncode != 0
    assert "JWT_SECRET" in proc.stderr
    assert segredo not in proc.stderr and segredo not in proc.stdout
    assert "BOOT_OK" not in proc.stdout


def test_boot_production_recusa_segredo_curto():
    proc = _subir("production", "a" * 31)
    assert proc.returncode != 0
    assert "BOOT_OK" not in proc.stdout
    assert "JWT_SECRET" in proc.stderr


def test_boot_prod_aceita_segredo_forte():
    proc = _subir("prod", FORTE_URLSAFE)
    assert proc.returncode == 0, proc.stderr[-2000:]
    assert "BOOT_OK True" in proc.stdout


def test_boot_dev_aceita_segredo_de_dev():
    proc = _subir("dev", "dev-only-jwt-secret-32-bytes-minimum-len")
    assert proc.returncode == 0, proc.stderr[-2000:]
    assert "BOOT_OK True" in proc.stdout
