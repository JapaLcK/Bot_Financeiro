"""Controles do `_host_fora` (tests/test_of_log_dono_coluna.py): pega o HOST cru
de verdade e não colide com dígitos aleatórios de id (flake do CI, run 38672567198)."""
import pytest

from tests.test_of_log_dono_coluna import HOST, _host_fora


def test_host_fora_pega_o_host_cru_inteiro_ou_em_pedaco():
    for vazado in (HOST, "falhou em db.interno", "ip 10.9.8.7", "port 5432 failed"):
        with pytest.raises(AssertionError):
            _host_fora({"error": vazado})


def test_host_fora_nao_confunde_id_aleatorio_com_a_porta():
    _host_fora("2º passe falhou", {"items": ["i541-disc-3543273973"], "motivo": "RuntimeError"})
