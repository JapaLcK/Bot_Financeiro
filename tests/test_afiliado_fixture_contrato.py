"""As fixtures do Playwright (`tests/frontend/_afiliado.mjs`) contra o `GET /api/affiliate/me` real.

A rota fica fora do OpenAPI, então os tipos de `webapp/src/dashboard/afiliado/api.ts` são à mão, e
resposta fora do contrato vira erro na tela de dinheiro. Com a fixture parada no formato antigo, o
Playwright seguiria verde. Aqui a cadeia fecha: resposta real = fixture = o que o leitor exige. Que
o leitor aceita a fixture quem prova é o navegador (`dashboard_v2_afiliado*.test.mjs`).

As fixtures saem do próprio `.mjs` pelo `node`, sem `node_modules` (o `_afiliado.mjs` só importa
`node:*`). Sem `node` o teste fica vermelho, de propósito: pular esconderia a divergência.
"""
import copy
import functools
import json
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from db.affiliate_payments import marcar_reembolso, registrar_pagamento
from db.affiliates import mark_payout_paid, reject_payout, request_payout, reverse_commission, set_affiliate_status
from db.affiliate_reviews import registrar_reembolso, resolver_parcial
from db.connection import get_conn
from test_afiliado_area import _afiliado, _comissao, _me

RAIZ = Path(__file__).resolve().parent.parent
LEITOR = RAIZ / "webapp" / "src" / "dashboard" / "afiliado" / "api.ts"
# Cada função do leitor, o nome do parâmetro dela e onde ela lê na resposta.
FUNCOES = {"afiliadoDe": ("v", ""), "statsDe": ("s", "stats."), "comissaoDe": ("c", "commissions[]."), "pedidoDe": ("p", "payouts[].")}
FORA_DO_LEITOR = {"payouts[].paid_at"}  # a rota devolve e a tela não usa
DATA = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}")


@functools.cache
def _fixtures():
    """Toda exportação do `.mjs` com o formato da resposta: fixture nova entra sozinha."""
    script = ("const f = await import(process.argv[1]);"
              "console.log(JSON.stringify(Object.fromEntries(Object.entries(f).filter(([, v]) => v && typeof v === 'object' && 'stats' in v))));")
    alvo = (RAIZ / "tests" / "frontend" / "_afiliado.mjs").as_uri()
    saida = subprocess.run(["node", "--input-type=module", "-e", script, alvo], cwd=RAIZ, capture_output=True, text=True, check=True, timeout=60)
    fixtures = json.loads(saida.stdout)
    assert "ATIVA" in fixtures, sorted(fixtures)
    return fixtures


def _tipo(v):
    if v is None:
        return "nulo"
    if isinstance(v, bool):
        return "booleano"
    if isinstance(v, (int, float)):
        return "numero"  # o JS não separa 10 de 10.0
    if isinstance(v, str):
        return "data" if DATA.match(v) else "texto"
    return "lista" if isinstance(v, list) else "objeto"


def formas(v, caminho="", saida=None):
    """`{caminho: tipos}`. `[]` é item de lista; item sem uma chave que outro tem marca "ausente"."""
    saida = {} if saida is None else saida
    saida.setdefault(caminho, set()).add(_tipo(v))
    if isinstance(v, dict):
        for k, filho in v.items():
            formas(filho, f"{caminho}.{k}" if caminho else k, saida)
    elif isinstance(v, list):
        chaves = {k for item in v if isinstance(item, dict) for k in item}
        for item in v:
            formas(item, f"{caminho}[]", saida)
            for k in chaves - set(item):
                saida.setdefault(f"{caminho}[].{k}", set()).add("ausente")
    return saida


def divergencias(real, fixture):
    """O que separa a fixture da resposta real. `real` traz a união dos estados: a fixture pode ter só parte dos tipos."""
    r, f = formas(real), formas(fixture)
    erros = [f"{c}: a resposta real não tem" for c in sorted(f.keys() - r.keys())]
    for c in sorted(r.keys() - f.keys()):
        lista = c.split("[]")[0]
        if not ("[]" in c and lista in f and f"{lista}[]" not in f):  # lista vazia na fixture não diz nada dos itens
            erros.append(f"{c}: a fixture não tem")
    erros += [f"{c}: fixture {sorted(f[c])}, real {sorted(r[c])}" for c in sorted(f.keys() & r.keys()) if f[c] - r[c]]
    return erros


def _leitor():
    """Os caminhos que o leitor lê, as listas de status dele e os status da conta. Forma nova do arquivo fica vermelha aqui, não passa vazia."""
    fonte = LEITOR.read_text(encoding="utf-8")
    caminhos = set()
    for nome, (var, prefixo) in FUNCOES.items():
        corpo = re.search(rf"^function {nome}\(\w+: unknown\): \w+ \{{\n(.*?)^\}}", fonte, re.M | re.S)
        assert corpo, f"{nome} sumiu ou mudou de forma em {LEITOR.name}: reescreva esta leitura"
        achados = re.findall(rf"\b{var}\.([a-z_]+(?:\.[a-z_]+)*)", corpo.group(1))
        assert achados, f"{nome} não lê nada de `{var}`: reescreva esta leitura"
        caminhos |= {prefixo + a.removesuffix(".map") for a in achados}
    listas = {nome: set(re.findall(r'"(\w+)"', corpo)) for nome, corpo in re.findall(r"^const (\w+) = \[(.*?)\] as const;", fonte, re.M)}
    assert listas.keys() >= {"STATUS_COMISSAO", "REEMBOLSOS", "STATUS_SAQUE"}, sorted(listas)
    conta = set(re.findall(r'v\.status !== "(\w+)"', fonte))
    assert conta, "a conferência de `v.status` mudou de forma: reescreva esta leitura"
    return caminhos, listas, conta


def faltam_no_real(real, caminhos):
    r = formas(real)
    sobram = {c for c in r.keys() - caminhos - FORA_DO_LEITOR if c and not c.endswith("[]")}  # raiz e item de lista não são chave
    return sorted(caminhos - r.keys()) + sorted(sobram)


def _status(resposta):
    return {"conta": {resposta["status"]}, "STATUS_COMISSAO": {c["status"] for c in resposta["commissions"]},
            "REEMBOLSOS": {c["payment_refund"] for c in resposta["commissions"]}, "STATUS_SAQUE": {p["status"] for p in resposta["payouts"]}}


def _resposta_real():
    """Todo estado que a rota devolve: comissão em carência, em saque, em revisão, ajustada, paga e estornada; saque aberto, pago com nota e rejeitado; reembolso total e parcial; conta ativa e desativada.

    Devolve as duas leituras, ativa e desativada; em cada uma, `paid_at`, `note` e `payout_id` vêm nulos e preenchidos."""
    agora = datetime.now(timezone.utc)
    c = _afiliado()
    _comissao(c, 100)
    assert mark_payout_paid(request_payout(c.aff["id"])["id"], "pago por Pix")
    _comissao(c, 100)
    assert reject_payout(request_payout(c.aff["id"])["id"], "chave não encontrada")
    total, parcial = _comissao(c, 10), _comissao(c, 10)
    for comissao, tipo in ((total, "total"), (parcial, "partial")):
        assert registrar_pagamento(comissao.indicado.uid, "stripe", comissao.fatura, 10000, agora)
        assert marcar_reembolso("stripe", comissao.fatura, tipo, agora)
    assert reverse_commission(total.id)
    ajustada, em_revisao = _comissao(c, 10), _comissao(c, 10)
    for comissao in (ajustada, em_revisao):
        registrar_reembolso(comissao.fatura, "partial", 3000, agora)
    with get_conn() as conn:
        pendencia = conn.execute("select id from affiliate_refund_reviews where stripe_invoice_id = %s",
                                 (ajustada.fatura,)).fetchone()["id"]
    assert resolver_parcial(pendencia, "admin", 600, "Ajuste do valor aprovado", 3000)
    _comissao(c, 10, disponivel=False)
    request_payout(c.aff["id"])
    ativa = _me(c)
    assert set_affiliate_status(c.aff["id"], "disabled")
    return ativa, _me(c)


# ── o contrato ──────────────────────────────────────────────────────────────

def test_fixtures_tem_o_formato_da_resposta_real():
    real = _resposta_real()[0]
    erros = {nome: e for nome, fixture in _fixtures().items() if (e := divergencias(real, fixture))}
    assert not erros, erros


def test_leitor_e_resposta_real_tem_as_mesmas_chaves():
    caminhos, _, _ = _leitor()
    ativa, desativada = _resposta_real()
    assert faltam_no_real(ativa, caminhos) == []
    assert faltam_no_real(desativada, caminhos) == []


def test_status_da_resposta_real_e_das_fixtures_sao_os_do_leitor():
    _, listas, conta = _leitor()
    aceitos = {**listas, "conta": conta}
    ativa, desativada = _resposta_real()
    real, outra = _status(ativa), _status(desativada)
    for nome in real:
        assert real[nome] | outra[nome] == aceitos[nome], nome
    for fixture in _fixtures().values():
        for nome, valores in _status(fixture).items():
            assert valores <= aceitos[nome], (nome, valores)


def test_nulos_aparecem_nos_dois_estados():
    """Sem isto, um cenário que parasse de produzir `paid_at` preenchido passaria a aceitar só o nulo."""
    r = formas(_resposta_real()[0])
    assert r["payouts[].paid_at"] == {"nulo", "data"}
    assert r["payouts[].note"] == {"nulo", "texto"}
    assert r["commissions[].payout_id"] == {"nulo", "numero"}
    assert r["commissions[].original_amount"] == {"nulo", "numero"}


# ── controles: o comparador acusa, numa cópia ───────────────────────────────

def test_controle_campo_a_menos_na_resposta_real_fica_vermelho():
    real = _resposta_real()[0]
    caminhos, _, _ = _leitor()
    copia = copy.deepcopy(real)
    del copia["stats"]["generated"]
    assert "stats.generated: a resposta real não tem" in divergencias(copia, _fixtures()["ATIVA"])
    assert "stats.generated" in faltam_no_real(copia, caminhos)
    copia = copy.deepcopy(real)
    for comissao in copia["commissions"]:
        del comissao["payment_refund"]
    assert "commissions[].payment_refund: a resposta real não tem" in divergencias(copia, _fixtures()["ATIVA"])


def test_controle_campo_renomeado_na_fixture_fica_vermelho():
    real = _resposta_real()[0]
    fixture = copy.deepcopy(_fixtures()["ATIVA"])
    fixture["stats"]["gerado"] = fixture["stats"].pop("generated")
    erros = divergencias(real, fixture)
    assert "stats.gerado: a resposta real não tem" in erros
    assert "stats.generated: a fixture não tem" in erros


def test_controle_um_item_da_lista_sem_campo_fica_vermelho():
    """A união dos itens esconderia isto: os outros itens da lista ainda têm a chave."""
    real = _resposta_real()[0]
    fixture = copy.deepcopy(_fixtures()["ATIVA"])
    del fixture["commissions"][1]["payout_id"]
    assert any(e.startswith("commissions[].payout_id: fixture") for e in divergencias(real, fixture))


def test_controle_tipo_trocado_na_fixture_fica_vermelho():
    real = _resposta_real()[0]
    fixture = copy.deepcopy(_fixtures()["ATIVA"])
    fixture["stats"]["available"] = "120.00"
    assert any(e.startswith("stats.available: fixture") for e in divergencias(real, fixture))


def test_controle_chave_que_o_leitor_exige_e_a_rota_nao_manda_fica_vermelho():
    caminhos, _, _ = _leitor()
    assert "stats.novo_campo" in faltam_no_real(_resposta_real()[0], caminhos | {"stats.novo_campo"})
