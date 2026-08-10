"""Testes do detector de spike de falha de login (A09 detector 1).

Não tocam no banco: usam um cursor falso, então rodam em segundos e cobrem a
lógica de threshold + cooldown sem precisar de Postgres.
"""
import asyncio

from core.services import security_alerts


class FakeCursor:
    """Cursor async mínimo. Responde a 3 queries: contagem, checagem de
    cooldown e insert do marcador."""

    def __init__(self, *, count: int, cooldown_hit: bool = False):
        self._count = count
        self._cooldown_hit = cooldown_hit
        self._last = None
        self.marker_inserts = []

    async def execute(self, sql, params=None):
        s = " ".join(sql.split()).lower()
        if "count(*)" in s:
            self._last = {"n": self._count}
        elif "select 1" in s and "system_event_logs" in s:
            self._last = (1,) if self._cooldown_hit else None
        elif s.startswith("insert into system_event_logs"):
            self.marker_inserts.append(params)
            self._last = None
        else:
            self._last = None

    async def fetchone(self):
        return self._last


def _run(count, *, cooldown_hit=False, ip="203.0.113.7"):
    cur = FakeCursor(count=count, cooldown_hit=cooldown_hit)
    result = asyncio.run(
        security_alerts.detect_auth_failure_spike(cur, ip_address=ip)
    )
    return result, cur


def test_abaixo_do_threshold_nao_alerta(monkeypatch):
    monkeypatch.setenv("AUTH_ALERT_THRESHOLD", "5")
    result, cur = _run(count=4)
    assert result is None
    assert cur.marker_inserts == []


def test_no_threshold_alerta_e_grava_marcador(monkeypatch):
    monkeypatch.setenv("AUTH_ALERT_THRESHOLD", "5")
    result, cur = _run(count=5)
    assert result is not None
    assert result["count"] == 5
    assert result["ip"] == "203.0.113.7"
    assert len(cur.marker_inserts) == 1  # gravou o cooldown


def test_em_cooldown_nao_realerta(monkeypatch):
    monkeypatch.setenv("AUTH_ALERT_THRESHOLD", "5")
    result, cur = _run(count=20, cooldown_hit=True)
    assert result is None
    assert cur.marker_inserts == []


def test_desligado_por_env_nao_alerta(monkeypatch):
    monkeypatch.setenv("AUTH_ALERT_THRESHOLD", "5")
    monkeypatch.setenv("SECURITY_ALERTS_ENABLED", "0")
    result, cur = _run(count=99)
    assert result is None
    assert cur.marker_inserts == []


def test_sem_ip_nao_alerta(monkeypatch):
    monkeypatch.setenv("AUTH_ALERT_THRESHOLD", "5")
    result, cur = _run(count=99, ip=None)
    assert result is None
    assert cur.marker_inserts == []
