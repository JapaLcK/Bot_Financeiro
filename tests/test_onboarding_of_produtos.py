"""O wizard (/onboarding, passo 2) promete o que o Open Finance lê; a promessa
tem de bater com os produtos que o connect token pede à Pluggy (§0.7).

A fonte é o default de PLUGGY_PRODUCTS em `create_pluggy_connect_token`
(core/services/pluggy.py), medido pela própria função com o HTTP simulado —
não pelo texto do arquivo. O HTML estático não importa Python, então a
duplicação é inevitável e este teste é o que a amarra.
"""
import re
from pathlib import Path

import core.services.pluggy as pl

HTML = Path(__file__).resolve().parent.parent / "frontend" / "comecar.html"

# Produto da Pluggy → como o wizard o nomeia. Produto novo no default sem frase
# aqui (e no HTML) reprova: o usuário autorizaria algo que a tela não contou.
FRASE = {
    "ACCOUNTS": "Contas e saldos",
    "TRANSACTIONS": "Transações",
    "CREDIT_CARDS": "Cartões de crédito",
    "INVESTMENTS": "Investimentos",
}


def _produtos_default(monkeypatch):
    captured = {}

    def fake_post(self, url, headers=None, json=None):  # noqa: A002
        captured["products"] = json["options"]["products"]

        class _R:
            status_code = 200

            def json(self_inner):
                return {"accessToken": "tok"}

        return _R()

    monkeypatch.delenv("PLUGGY_PRODUCTS", raising=False)
    monkeypatch.setattr(pl, "create_pluggy_api_key", lambda: "k")
    monkeypatch.setattr(pl, "_raise_for_pluggy_response", lambda resp, msg: None)
    monkeypatch.setattr(pl.httpx.Client, "post", fake_post)
    pl.create_pluggy_connect_token(1, None)
    return captured["products"]


def _itens_do_wizard():
    html = HTML.read_text(encoding="utf-8")
    bloco = re.search(r'<ul[^>]*data-role="of-products"[^>]*>(.*?)</ul>', html, re.S)
    assert bloco, "lista data-role=of-products sumiu do comecar.html"
    return [re.sub(r"\s+", " ", li).strip() for li in re.findall(r"<li>(.*?)</li>", bloco.group(1), re.S)]


def test_lista_do_wizard_bate_com_os_produtos_pedidos_a_pluggy(monkeypatch):
    produtos = _produtos_default(monkeypatch)
    assert set(produtos) == set(FRASE), f"produto sem frase no wizard: {set(produtos) ^ set(FRASE)}"
    assert _itens_do_wizard() == [FRASE[p] for p in produtos]
