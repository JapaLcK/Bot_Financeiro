"""PL-04: a regra pura do Xerife e o contrato da explicação (sem banco).

Controles: o grupo de limiar/amostra/arredondamento falha se a regra for desligada
(trocar `>` por `>=`, ignorar `AMOSTRA_MINIMA`, arredondar duas vezes); os casos
"dispara" provam o caminho legítimo, para o grupo não passar num código que recusa tudo.
"""
import json
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal as D

import pytest

from core.services import anomalia as an
from core.services.anomalia import AmostraMinima, Candidato, Referencia, avaliar_lancamento

MULT, MIN = D("2.5"), D("50")
AGORA = datetime(2026, 10, 9, 14, 3, 11, tzinfo=timezone.utc)


def _ref(media="100", n=5, esperados=0):
    return Referencia(D(media), n, date(2026, 7, 14), date(2026, 10, 7), esperados)


def _av(valor, ref=None, dias=90, **kw):
    return avaliar_lancamento(D(valor), ref or _ref(), dias, multiplicador=MULT, minimo=MIN, **kw)


def test_limiar_estrito():
    assert _av("250").status == "abaixo_do_limiar"      # == 2,5x não dispara
    assert _av("250.01").status == "anomalia"           # +1 centavo dispara


def test_minimo_de_valor():
    ref = _ref("10")
    assert _av("49.99", ref).status == "valor_pequeno"
    assert _av("50.00", ref).status == "anomalia"


def test_amostra_minima_por_ocorrencias():
    assert _av("400", _ref(n=4)).status == "amostra_insuficiente"
    assert _av("400", _ref(n=5)).status == "anomalia"


def test_amostra_insuficiente_so_quando_teria_alertado():
    # gasto pequeno ou normal de quem tem pouco histórico não conta como "suprimido"
    assert _av("100", _ref(n=2)).status == "abaixo_do_limiar"
    assert _av("40", _ref("10", n=2)).status == "valor_pequeno"


def test_constante_da_amostra_inverte_o_veredito(monkeypatch):
    assert _av("400", _ref(n=4)).status == "amostra_insuficiente"
    monkeypatch.setattr(an, "AMOSTRA_MINIMA", AmostraMinima(lancamentos=4, dias=0))
    assert _av("400", _ref(n=4)).status == "anomalia"


def test_minimo_de_dias_so_vale_quando_exigido():
    c28 = AmostraMinima(lancamentos=5, dias=28)
    assert _av("400", dias=27, amostra=c28).status == "amostra_insuficiente"
    assert _av("400", dias=28, amostra=c28).status == "anomalia"
    assert _av("400", dias=0).status == "anomalia"       # dias=0 (hoje) não exige histórico


def test_historico_incompleto_na_fronteira_dos_90_dias():
    assert _av("400", dias=89).historico_incompleto is True
    assert _av("400", dias=90).historico_incompleto is False
    assert _av("400", dias=89).status == "anomalia"      # flag, não bloqueio


def test_media_zero_ou_sem_referencia_nao_alerta_nem_divide():
    assert _av("400", _ref("0")).status == "sem_referencia"
    assert avaliar_lancamento(D("400"), None, 90, multiplicador=MULT, minimo=MIN).status == "sem_referencia"


def _cand(**kw):
    base = dict(launch_id=123, valor=D("480"), categoria="Alimentação", descricao="Padaria X",
                data=date(2026, 10, 8))
    return Candidato(**{**base, **kw})


def _payload(c=None, ref=None, dias=90):
    c, ref = c or _cand(), ref or _ref("150", n=12)
    av = avaliar_lancamento(c.valor, ref, dias, multiplicador=MULT, minimo=MIN)
    return an.montar_payload(c, ref, av, dias, multiplicador=MULT, agora=AGORA)


def test_contrato_do_payload_e_json_puro():
    p = _payload()
    json.loads(json.dumps(p))                            # sem Decimal cru
    assert p["tipo"] == "anomalia" and p["launch_id"] == 123 and p["valor"] == 480.0 and p["media"] == 150.0
    e = p["explicacao"]
    assert e["versao"] == 1 and e["fonte"] == {"base": "lancamentos", "cartao_incluido": False}
    assert e["referencia"]["valor"] == 150.0 and e["referencia"]["janela"] == {
        "dias": 90, "primeira": "2026-07-14", "ultima": "2026-10-07"}
    assert e["diferenca"] == {"absoluta": 330.0, "percentual": 220.0, "razao": 3.2}
    assert e["amostra"]["lancamentos"] == 12 and e["amostra"]["suficiente"] is True
    assert e["amostra"]["historico_incompleto"] is False
    assert e["calculado_em"] == "2026-10-09T14:03:11+00:00"
    assert "3,2x" in p["mensagem"] and "R$ 150,00" in p["mensagem"] and "R$ 330,00" in p["mensagem"]
    assert "+220%" in p["mensagem"] and "12 lançamentos de 14/07 a 07/10" in p["mensagem"]
    assert "Era esperado?" in p["mensagem"] and "Histórico ainda curto" not in p["mensagem"]


def test_historico_curto_aparece_na_mensagem_e_na_explicacao():
    p = _payload(dias=34)
    assert "Histórico ainda curto (34 de 90 dias)" in p["mensagem"]
    assert p["explicacao"]["amostra"]["historico_incompleto"] is True
    assert p["explicacao"]["amostra"]["dias_historico"] == 34


def test_arredondamento_unico_do_percentual():
    # +11,46%: 11,5 em uma casa; em inteiro é 11, e NÃO 12 (11,46 -> 11,5 -> 12 é o erro)
    p = _payload(_cand(valor=D("111.46")), _ref("100"))
    assert p["explicacao"]["diferenca"]["percentual"] == 11.5
    assert "+11%" in p["mensagem"]


def test_valor_e_media_nao_redondos_sem_float_cru():
    p = _payload(_cand(valor=D("100.005")), _ref("33.3333333333"))
    json.dumps(p)
    assert p["explicacao"]["referencia"]["valor"] == 33.33
    assert p["explicacao"]["diferenca"]["absoluta"] == pytest.approx(66.68, abs=0.011)


def test_texto_do_usuario_vira_uma_linha_so():
    sujo = "Mer\ncado *x*​" + "z" * 500
    p = _payload(_cand(categoria=sujo, descricao="a\r\nb _c_ `d`"))
    for campo in (p["mensagem"], p["titulo"]):
        assert "\n" not in campo and "\r" not in campo and "*" not in campo
        assert "​" not in campo and "`" not in campo
    assert len(p["titulo"]) < 90                          # a categoria de 500 caracteres é cortada


def test_descricao_vazia_nao_deixa_parenteses():
    p = _payload(_cand(descricao="   "))
    assert "()" not in p["mensagem"] and "( )" not in p["mensagem"]


def test_falha_na_explicacao_nao_derruba_o_alerta_essencial(monkeypatch, caplog):
    def explode(*a, **k):
        raise RuntimeError("texto-do-usuario-secreto")
    monkeypatch.setattr(an, "montar_explicacao", explode)
    p = _payload()
    assert p["tipo"] == "anomalia" and p["launch_id"] == 123 and "explicacao" not in p
    assert p["titulo"].startswith("Gasto fora do padrão em ") and "R$ 480,00" in p["mensagem"]
    assert p["mensagem"].endswith(an.CONVITE_ESPERADO)
    assert "RuntimeError" in caplog.text and "texto-do-usuario-secreto" not in caplog.text


def test_avaliar_candidatos_conta_suprimidos_e_so_gera_anomalia():
    primeira = AGORA - timedelta(days=40)
    base = dict(id=1, valor=D("400"), categoria="Alimentação", descricao="", criado_em=AGORA,
                media=D("100"), n=5, primeira=AGORA - timedelta(days=30), ultima=AGORA - timedelta(days=3),
                esperados_fora=0, primeira_usuario=primeira)
    rows = [base, {**base, "id": 2, "n": 3}, {**base, "id": 3, "n": None, "media": None}]
    payloads, suprimidos = an.avaliar_candidatos(rows, AGORA, multiplicador=MULT, minimo=MIN)
    assert [p["launch_id"] for p in payloads] == [1]
    assert suprimidos == 1                                 # o de n=3; o sem referência não conta
    assert payloads[0]["explicacao"]["amostra"]["dias_historico"] == 40
    assert payloads[0]["explicacao"]["amostra"]["historico_incompleto"] is True


def test_dias_de_historico_sao_limitados_a_90():
    base = dict(id=1, valor=D("400"), categoria="Alimentação", descricao="", criado_em=AGORA,
                media=D("100"), n=5, primeira=AGORA - timedelta(days=30), ultima=AGORA - timedelta(days=3),
                esperados_fora=0, primeira_usuario=AGORA - timedelta(days=120))
    (p,), _ = an.avaliar_candidatos([base], AGORA, multiplicador=MULT, minimo=MIN)
    assert p["explicacao"]["amostra"]["dias_historico"] == 90
    assert p["explicacao"]["amostra"]["historico_incompleto"] is False


def test_valor_absurdo_nao_derruba_o_alerta_essencial():
    # 1e26 estoura o contexto do Decimal.quantize: tem de cair no degradê, não levantar
    p = _payload(_cand(valor=D("1e26")), _ref("100"))
    json.dumps(p)
    assert p["tipo"] == "anomalia" and p["launch_id"] == 123 and p["valor"] == 1e26
    assert "explicacao" not in p and p["titulo"].startswith("Gasto fora do padrão em ")
    assert p["mensagem"].endswith(an.CONVITE_ESPERADO)          # D3 vale também no caminho degradado
    assert p["mensagem"].endswith("Era esperado? Marque no painel que eu deixo de contar esse gasto.")


@pytest.mark.parametrize("bruto,esperado", [("nan", "2.5"), ("inf", "2.5"), (-1, "2.5"), (0, "2.5"),
                                            (None, "2.5"), ("3", "3.0"), (2, "2.0"), (2.3, "2.3")])   # 2.3 NÃO vira o binário 2.2999…
def test_limiar_da_config_crua(bruto, esperado):
    assert an.limiar(bruto, 2.5) == D(esperado)
    with pytest.raises(ValueError):
        an.limiar("abc", 2.5)


def test_campos_legados_do_payload_iguais_aos_da_main():
    """A main gravava `valor = float(valor)` (qualquer nº de casas) e `media = round(float(media), 2)`."""
    for v in ("100.005", "0.125", "480", "1e26"):
        assert _payload(_cand(valor=D(v)), _ref("150"))["valor"] == float(D(v))
    assert _payload(_cand(), _ref("33.335"))["media"] == round(float(D("33.335")), 2)
    assert _payload(_cand(), _ref("33.3333333333"))["media"] == 33.33
