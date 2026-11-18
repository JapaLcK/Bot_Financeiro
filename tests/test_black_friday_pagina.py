"""GET /black-friday: o estado (antes, oferta, depois) sai do relógio do SERVIDOR (`campanha_estado`), nunca do
aparelho; os preços do HTML são os da conta do servidor (uma fonte de verdade); o Essencial não aparece.

Controles: trocar o relógio de `serve_black_friday` por um fixo deixa o grupo vermelho (cada estado tem asserção
própria); tirar o corte dos blocos `v:`/`e:` mostra os dois estados juntos (`test_so_o_estado_atual_no_html`);
mudar um preço no HTML sem mudar a conta deixa `test_precos_do_html_sao_os_do_servidor` vermelho.
Positivos: o estado "oferta" tem os CTAs e o "depois" leva à /precos.
"""
import re
from datetime import datetime
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient

import frontend.finance_bot_websocket_custom as dashboard
from core.services import oferta_campanha as camp
from frontend.routes.retomada import _brl, campanha_config

BRT = ZoneInfo("America/Sao_Paulo")
ANTES = datetime(2026, 11, 22, 23, 59, 59, tzinfo=BRT)
DENTRO = datetime(2026, 11, 25, 12, tzinfo=BRT)
ULTIMO = datetime(2026, 11, 30, 23, 59, 59, tzinfo=BRT)
DEPOIS = datetime(2026, 12, 1, tzinfo=BRT)
client = TestClient(dashboard.app)


def _pagina(monkeypatch, quando):
    monkeypatch.setattr(camp, "_agora", lambda: quando)
    r = client.get("/black-friday")
    assert r.status_code == 200 and r.headers["cache-control"] == "no-store"
    return r.text


def test_estado_oferta_tem_os_ctas_para_o_assinar_anual(monkeypatch):
    for quando in (DENTRO, ULTIMO):                 # 30/11 23:59:59 ainda é oferta
        h = _pagina(monkeypatch, quando)
        assert set(re.findall(r'href="(/assinar\?[^"]+)"', h)) == {"/assinar?plano=plus&amp;ciclo=annual", "/assinar?plano=pro&amp;ciclo=annual"}
        assert "Garantir meu Plus anual" in h and "Quero o Pro com 40%" in h
        assert "Termina segunda, 30 de novembro, às 23h59." in h and "<button" not in h
        assert "acabou" not in h


def test_estado_antes_tem_botoes_desabilitados_sem_href(monkeypatch):
    h = _pagina(monkeypatch, ANTES)
    assert "/assinar" not in h                       # nenhum link de compra antes do dia 23
    assert h.count("<button") == 4 and h.count("Começa segunda, dia 23, à 0h") == 4
    assert "De segunda, dia 23, à 0h, até segunda, dia 30" in h and "Vale de 23 a 30 de novembro" in h
    assert "Até segunda, 30 de novembro" not in h and "Termina segunda" not in h


def test_estado_depois_so_a_despedida_e_ver_os_planos(monkeypatch):
    h = _pagina(monkeypatch, DEPOIS)
    assert "A Black Friday do PigBank acabou." in h and 'href="/precos"' in h and "Ver os planos" in h
    assert "/assinar" not in h and "R$ 119,40" not in h and "Plus anual" not in h


def test_so_o_estado_atual_no_html(monkeypatch):
    for quando in (ANTES, DENTRO, DEPOIS):
        assert not re.search(r"<!--/?[ev]:?[a-z,]*-->", _pagina(monkeypatch, quando))   # nenhum marcador vaza


def test_essencial_fora_em_todos_os_estados(monkeypatch):
    for quando in (ANTES, DENTRO, DEPOIS):
        assert "Essencial" not in _pagina(monkeypatch, quando)
    assert "qualquer plano" not in _pagina(monkeypatch, DENTRO)


def test_precos_do_html_sao_os_do_servidor(monkeypatch):
    monkeypatch.setattr(camp, "_agora", lambda: DENTRO)
    c = campanha_config()
    h = _pagina(monkeypatch, DENTRO)
    for tier, cheio in (("plus", "R$ 199"), ("pro", "R$ 499")):
        p = c["precos"][tier]
        assert _brl(p["annual"]) in h and cheio in h and f"Dá {_brl(p['annual'] // 12).replace('.', ',')} por mês." in h
    assert "R$ 119,40" in h and "R$ 299,40" in h and "R$ 9,95" in h and "R$ 24,95" in h
