"""Cache-primeiro nas taxas do BCB (O1-1 da auditoria de performance).

Com cache fresco em market_rates, o caminho do dashboard NÃO pode ir à rede
— e o valor servido tem de ser o MESMO que o caminho de rede gravaria.
Dinheiro exibido: dias cacheados são finais.
"""
from datetime import date, timedelta

import db
import db.investments as investments_db


def _stub_latest(calls, payload):
    def stub(series_code, limit=15):
        calls.append(series_code)
        return payload
    return stub


def test_get_latest_cache_fresco_zero_http_e_mesmo_valor():
    hoje = date.today()
    payload = [{"data": hoje.strftime("%d/%m/%Y"), "valor": "14,90"}]
    calls: list[int] = []
    original = investments_db._fetch_sgs_latest_json
    investments_db._fetch_sgs_latest_json = _stub_latest(calls, payload)
    try:
        with db.get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "delete from market_rates where code in ('CDI_AA','SELIC_AA','IPCA_12M')"
                )
                # Frio: vai à rede (stub) e grava no cache.
                cold = {
                    "cdi_aa": investments_db.get_latest_cdi_aa(cur),
                    "selic_aa": investments_db.get_latest_selic_aa(cur),
                    "ipca_12m": investments_db.get_latest_ipca_12m(cur),
                }
                assert calls == [4389, 432, 13522]
                assert cold["cdi_aa"] == (hoje, 14.9)

                # Quente (positivo): 0 HTTP e os MESMOS valores do caminho de rede.
                calls.clear()
                warm = {
                    "cdi_aa": investments_db.get_latest_cdi_aa(cur),
                    "selic_aa": investments_db.get_latest_selic_aa(cur),
                    "ipca_12m": investments_db.get_latest_ipca_12m(cur),
                }
                assert calls == [], f"cache fresco foi à rede: {calls}"
                assert warm == cold

                # Negativo (fix desligado via fresh_days=-1, no MESMO estado verde):
                # a contagem de HTTP volta a 3. O memo de confirmação (setado
                # pelos fetches frios acima) é limpo — senão ele seguraria a rede.
                calls.clear()
                investments_db._sgs_confirmed_until.clear()
                investments_db.get_latest_market_rate(cur, "CDI_AA", 4389, fresh_days=-1)
                investments_db.get_latest_market_rate(cur, "SELIC_AA", 432, fresh_days=-1)
                investments_db.get_latest_market_rate(cur, "IPCA_12M", 13522, fresh_days=-1)
                assert len(calls) == 3, f"sem cache-primeiro esperava 3 HTTP, veio {calls}"
            conn.rollback()
    finally:
        investments_db._fetch_sgs_latest_json = original


def test_get_latest_cache_stale_cai_na_rede():
    """ref_date além do frescor ⇒ rede; e rede falhando ⇒ fallback stale (inalterado)."""
    velho = date.today() - timedelta(days=30)
    calls: list[int] = []
    original = investments_db._fetch_sgs_latest_json
    investments_db._fetch_sgs_latest_json = _stub_latest(calls, [])  # rede "falha"
    try:
        with db.get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("delete from market_rates where code='SELIC_AA'")
                cur.execute(
                    "insert into market_rates(code, ref_date, value) values ('SELIC_AA', %s, %s)",
                    (velho, 10.5),
                )
                out = investments_db.get_latest_selic_aa(cur)
                assert calls == [432]  # stale ⇒ tentou a rede
                assert out == (velho, 10.5)  # rede falhou ⇒ serviu o stale
            conn.rollback()
    finally:
        investments_db._fetch_sgs_latest_json = original


def test_mapa_diario_janela_cacheada_zero_http_e_busca_so_o_faltante():
    calls: list[tuple] = []

    def stub_series(series_code, start, end):
        calls.append((series_code, start, end))
        # Rede devolve só dias novos, com valor DIFERENTE do cache para
        # denunciar qualquer sobrescrita de dia cacheado.
        return [
            {"data": start.strftime("%d/%m/%Y"), "valor": "0,09"},
        ]

    # Abril/2026: 15-17 = qua-sex; 18-19 = fim de semana; 20 = segunda útil.
    cached_days = [date(2026, 4, 15), date(2026, 4, 16), date(2026, 4, 17)]
    original = investments_db._fetch_sgs_series_json
    investments_db._fetch_sgs_series_json = stub_series
    try:
        with db.get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("delete from market_rates where code='CDI' and ref_date >= %s", (date(2026, 4, 1),))
                for d in cached_days:
                    cur.execute(
                        "insert into market_rates(code, ref_date, value) values ('CDI', %s, %s) "
                        "on conflict (code, ref_date) do update set value=excluded.value",
                        (d, 0.05),
                    )

                # Positivo 1 (sexta→domingo): cauda sem dia útil ⇒ 0 HTTP.
                out = investments_db._get_cdi_daily_map(cur, date(2026, 4, 15), date(2026, 4, 19))
                assert calls == [], f"janela coberta foi à rede: {calls}"
                assert out == {d: 0.05 for d in cached_days}

                # Positivo 2 (sexta→segunda): dia útil publicável na cauda ⇒
                # busca SÓ o faltante (18..20); dia cacheado permanece (final).
                out = investments_db._get_cdi_daily_map(cur, date(2026, 4, 15), date(2026, 4, 20))
                assert calls == [(12, date(2026, 4, 18), date(2026, 4, 20))]
                assert out[date(2026, 4, 15)] == 0.05  # dia cacheado é final
                assert out[date(2026, 4, 18)] == 0.09  # dia novo veio da rede

                # Negativo: fix desligado (cauda "sempre fresca") ⇒ a mesma
                # célula sexta→segunda deixa de ir à rede. Memo limpo (o fetch
                # do Positivo 2 confirmou hoje, e confirmação segura a rede).
                calls.clear()
                investments_db._sgs_confirmed_until.clear()
                original_fresh = investments_db._sgs_tail_is_fresh
                investments_db._sgs_tail_is_fresh = lambda newest, end: True
                try:
                    investments_db._get_cdi_daily_map(cur, date(2026, 4, 15), date(2026, 4, 20))
                finally:
                    investments_db._sgs_tail_is_fresh = original_fresh
                assert calls == [], "sanidade do toggle: com 'sempre fresco' não podia buscar"
                investments_db._get_cdi_daily_map(cur, date(2026, 4, 15), date(2026, 4, 20))
                assert len(calls) == 1, "com o fix a segunda publicável tinha de ir à rede"
            conn.rollback()
    finally:
        investments_db._fetch_sgs_series_json = original


def test_mapa_sgs_generico_tem_o_mesmo_cache_primeiro():
    """_get_sgs_daily_map (SELIC/IPCA) — irmão do CDI, mesma classe de bug."""
    calls: list[tuple] = []

    def stub_series(series_code, start, end):
        calls.append((series_code, start, end))
        return []

    original = investments_db._fetch_sgs_series_json
    investments_db._fetch_sgs_series_json = stub_series
    try:
        with db.get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("delete from market_rates where code='SELIC_DAILY'")
                for d in (date(2026, 4, 15), date(2026, 4, 16), date(2026, 4, 17)):
                    cur.execute(
                        "insert into market_rates(code, ref_date, value) values ('SELIC_DAILY', %s, %s)",
                        (d, 0.04),
                    )
                # sexta (17) no cache, fim de semana na cauda ⇒ 0 fetch
                out = investments_db._get_selic_daily_map(cur, date(2026, 4, 15), date(2026, 4, 19))
                assert calls == []
                assert out == {d: 0.04 for d in (date(2026, 4, 15), date(2026, 4, 16), date(2026, 4, 17))}
            conn.rollback()
    finally:
        investments_db._fetch_sgs_series_json = original


# ── Cache FURADO não pode ser servido como completo (achado BLOQUEIA do
#    Tester: juro sumia sem retentativa). Abril/2026: 13-17 e 20 são úteis;
#    18-19 fim de semana; 21 feriado (Tiradentes). ─────────────────────────

def _stub_series_biz(calls, valor="0.07"):
    def stub(series_code, start, end):
        calls.append((series_code, start, end))
        out, d = [], start
        while d <= end:
            if investments_db.is_br_business_day(d):
                out.append({"data": d.strftime("%d/%m/%Y"), "valor": valor})
            d += timedelta(days=1)
        return out
    return stub


def test_buraco_na_cabeca_forca_janela_inteira():
    """Só a véspera cacheada + janela de meses ⇒ rede pela janela INTEIRA
    (o bug servia 1 dia como se fosse a janela toda)."""
    calls: list[tuple] = []
    original = investments_db._fetch_sgs_series_json
    investments_db._fetch_sgs_series_json = _stub_series_biz(calls)
    try:
        with db.get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("delete from market_rates where code='CDI' and ref_date >= %s", (date(2026, 1, 1),))
                cur.execute(
                    "insert into market_rates(code, ref_date, value) values ('CDI', %s, %s)",
                    (date(2026, 4, 17), 0.05),
                )
                start, end = date(2026, 2, 2), date(2026, 4, 20)
                out = investments_db._get_cdi_daily_map(cur, start, end)
                assert calls == [(12, start, end)], "cabeça furada tinha que buscar a janela inteira"
                assert out[date(2026, 2, 2)] == 0.07  # dia da cabeça veio da rede
                # O BANCO não re-grava dia já cacheado (final); o mapa em
                # memória usa a rede na sobreposição — paridade com o antigo.
                cur.execute(
                    "select value from market_rates where code='CDI' and ref_date=%s",
                    (date(2026, 4, 17),),
                )
                assert float(cur.fetchone()["value"]) == 0.05
            conn.rollback()
    finally:
        investments_db._fetch_sgs_series_json = original


def test_buraco_no_meio_forca_janela_inteira():
    """Dia útil faltando no MEIO do cache ⇒ rede cura o buraco."""
    calls: list[tuple] = []
    original = investments_db._fetch_sgs_series_json
    investments_db._fetch_sgs_series_json = _stub_series_biz(calls)
    try:
        with db.get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("delete from market_rates where code='CDI' and ref_date >= %s", (date(2026, 4, 1),))
                for d in (date(2026, 4, 13), date(2026, 4, 14), date(2026, 4, 16), date(2026, 4, 17)):
                    cur.execute(
                        "insert into market_rates(code, ref_date, value) values ('CDI', %s, %s)",
                        (d, 0.05),
                    )  # 15/04 (útil) faltando de propósito
                start, end = date(2026, 4, 13), date(2026, 4, 20)
                out = investments_db._get_cdi_daily_map(cur, start, end)
                assert calls == [(12, start, end)], "buraco no meio tinha que buscar a janela inteira"
                assert date(2026, 4, 15) in out, "o dia do buraco tem que existir no mapa"
            conn.rollback()
    finally:
        investments_db._fetch_sgs_series_json = original


def test_segunda_publicada_invalida_cache_que_termina_na_sexta():
    """Codex-1 do PR #218: cache até SEXTA e accrual na TERÇA ⇒ a segunda já
    publicada tem de ser buscada. A condição antiga ('fresco por ≤4 dias
    corridos') servia o cache e o accrual parava na sexta — juro DEFERIDO por
    até 2 dias úteis no valor exibido."""
    calls: list[tuple] = []
    original = investments_db._fetch_sgs_series_json
    investments_db._fetch_sgs_series_json = _stub_series_biz(calls)
    try:
        with db.get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("delete from market_rates where code='CDI' and ref_date >= %s", (date(2026, 4, 1),))
                d = date(2026, 4, 13)
                while d <= date(2026, 4, 24):  # contíguo até sexta 24/04
                    if investments_db.is_br_business_day(d):
                        cur.execute(
                            "insert into market_rates(code, ref_date, value) values ('CDI', %s, %s)",
                            (d, 0.05),
                        )
                    d += timedelta(days=1)
                sexta, terca = date(2026, 4, 24), date(2026, 4, 28)

                # Positivo: terça busca a cauda [25..28] e a SEGUNDA (27) entra.
                out = investments_db._get_cdi_daily_map(cur, date(2026, 4, 14), terca)
                assert calls == [(12, date(2026, 4, 25), terca)], (
                    "segunda publicável na cauda tinha de ir à rede"
                )
                assert date(2026, 4, 27) in out and date(2026, 4, 28) in out

                # Negativo: condição ANTIGA reinjetada ((end-newest).days<=4 ⇒
                # 'fresco') ⇒ a mesma terça serve o cache sem a segunda.
                calls.clear()
                investments_db._sgs_confirmed_until.clear()
                cur.execute("delete from market_rates where code='CDI' and ref_date > %s", (sexta,))
                original_fresh = investments_db._sgs_tail_is_fresh
                investments_db._sgs_tail_is_fresh = lambda newest, end: (end - newest).days <= 4
                try:
                    out = investments_db._get_cdi_daily_map(cur, date(2026, 4, 14), terca)
                finally:
                    investments_db._sgs_tail_is_fresh = original_fresh
                assert calls == [] and date(2026, 4, 27) not in out, (
                    "com a condição antiga o bug tinha de reaparecer — o teste não discrimina"
                )
            conn.rollback()
    finally:
        investments_db._fetch_sgs_series_json = original


def test_confirmacao_curta_pre_publicacao_pega_o_ponto_da_noite():
    """Codex-3: célula 4b da máquina — manhã de dia útil, cauda publicável,
    resposta vazia ⇒ confirmação CURTA (≤2h); quando ela vence, o ponto
    publicado à noite entra AINDA no mesmo dia. Resposta que COMPLETA a
    cauda ⇒ confirmação de dia cheio (meia-noite UTC)."""
    from datetime import datetime, timezone

    calls: list[tuple] = []
    payload: dict = {"itens": []}  # muda de "vazio" (manhã) p/ ponto (noite)

    def stub(series_code, start, end):
        calls.append((start, end))
        return list(payload["itens"])

    original = investments_db._fetch_sgs_series_json
    investments_db._fetch_sgs_series_json = stub
    try:
        with db.get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("delete from market_rates where code='CDI' and ref_date >= %s", (date(2026, 4, 1),))
                d = date(2026, 4, 13)
                while d <= date(2026, 4, 17):
                    if investments_db.is_br_business_day(d):
                        cur.execute(
                            "insert into market_rates(code, ref_date, value) values ('CDI', %s, 0.05)", (d,)
                        )
                    d += timedelta(days=1)
                win = (date(2026, 4, 13), date(2026, 4, 20))  # 20 = segunda publicável

                # manhã: vazio ⇒ 1 fetch + confirmação CURTA (não meia-noite)
                out = investments_db._get_cdi_daily_map(cur, *win)
                assert len(calls) == 1 and date(2026, 4, 20) not in out
                now = datetime.now(timezone.utc)
                until = investments_db._sgs_confirmed_until["CDI"]
                assert until - now <= investments_db.SGS_CONFIRM_SHORT, (
                    f"vazio pré-publicação confirmou até {until} — era pra ser curto"
                )
                # memo vigente segura a rede
                investments_db._get_cdi_daily_map(cur, *win)
                assert len(calls) == 1

                # "2h depois": confirmação venceu; o BCB publicou o ponto
                investments_db._sgs_confirmed_until["CDI"] = now
                payload["itens"] = [{"data": "20/04/2026", "valor": "0.06"}]
                out = investments_db._get_cdi_daily_map(cur, *win)
                assert len(calls) == 2 and out[date(2026, 4, 20)] == 0.06, (
                    "o ponto da noite tinha de entrar no MESMO dia"
                )
                # cauda agora completa ⇒ confirmação de dia cheio
                meia_noite = (datetime.now(timezone.utc) + timedelta(days=1)).replace(
                    hour=0, minute=0, second=0, microsecond=0
                )
                assert investments_db._sgs_confirmed_until["CDI"] == meia_noite
            conn.rollback()
    finally:
        investments_db._fetch_sgs_series_json = original


def test_get_latest_confirma_curto_na_diaria_e_dia_cheio_na_mensal():
    """Diária com ponto velho (pré-publicação) ⇒ curta; mensal ⇒ dia cheio
    (o ponto mais novo É a resposta — teto de 1 fetch/dia preservado)."""
    from datetime import datetime, timezone

    calls: list[int] = []
    original = investments_db._fetch_sgs_latest_json
    investments_db._fetch_sgs_latest_json = _stub_latest(
        calls, [{"data": "01/07/2026", "valor": "4,85"}]
    )
    try:
        with db.get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("delete from market_rates where code in ('CDI_AA','IPCA_12M')")
                now = datetime.now(timezone.utc)
                meia_noite = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)

                investments_db.get_latest_cdi_aa(cur)  # diária, ponto de 01/07 = velho
                assert investments_db._sgs_confirmed_until["CDI_AA"] - now <= (
                    investments_db.SGS_CONFIRM_SHORT + timedelta(seconds=5)
                )

                investments_db.get_latest_ipca_12m(cur)  # mensal
                assert investments_db._sgs_confirmed_until["IPCA_12M"] == meia_noite
            conn.rollback()
    finally:
        investments_db._fetch_sgs_latest_json = original


def test_payload_lixo_nao_confirma_o_memo():
    """200 com lixo é FALHA (None), não resposta: não pode segurar a rede até
    amanhã. Só '[] = sem valores' e lista com dados confirmam."""
    from types import SimpleNamespace

    def _resp(payload):
        return SimpleNamespace(json=lambda: payload)

    def _resp_json_quebrado():
        def boom():
            raise ValueError("not json")
        return SimpleNamespace(json=boom)

    decode = investments_db._decode_sgs_response
    assert decode(_resp({"weird": 1}), 12, context=("t", 1)) is None
    assert decode(_resp_json_quebrado(), 12, context=("t", 2)) is None
    assert decode(_resp({"erro": {"detail": "Value(s) not found"}}), 12, context=("t", 3)) == []
    assert decode(_resp([{"data": "01/01/2026", "valor": "0,05"}]), 12, context=("t", 4)) == [
        {"data": "01/01/2026", "valor": "0,05"}
    ]

    # Efeito no memo: fetch devolvendo None (lixo/falha) ⇒ re-tenta na chamada
    # seguinte; devolvendo [] (sem valores) ⇒ confirma e segura a rede.
    calls: list[int] = []

    def stub_none(series_code, start, end):
        calls.append(series_code)
        return None

    original = investments_db._fetch_sgs_series_json
    investments_db._fetch_sgs_series_json = stub_none
    try:
        with db.get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("delete from market_rates where code='CDI' and ref_date >= %s", (date(2026, 4, 1),))
                for d in (date(2026, 4, 13), date(2026, 4, 14)):
                    cur.execute(
                        "insert into market_rates(code, ref_date, value) values ('CDI', %s, %s)",
                        (d, 0.05),
                    )
                win = (date(2026, 4, 13), date(2026, 4, 20))  # cauda stale
                investments_db._get_cdi_daily_map(cur, *win)
                investments_db._get_cdi_daily_map(cur, *win)
                assert len(calls) == 2, "falha/lixo confirmou o memo — não podia"
            conn.rollback()
    finally:
        investments_db._fetch_sgs_series_json = original


def test_20_nov_pre_2024_e_dia_util_e_conta_como_buraco():
    """Consciência Negra só é feriado bancário nacional desde 2024 (Lei
    14.759/2023). 20/11/2023 foi segunda-feira com CDI publicado: faltando no
    cache, a cobertura tem que acusar o buraco e buscar a janela inteira."""
    assert investments_db.is_br_business_day(date(2023, 11, 20)) is True
    assert investments_db.is_br_business_day(date(2024, 11, 20)) is False

    calls: list[tuple] = []
    original = investments_db._fetch_sgs_series_json
    investments_db._fetch_sgs_series_json = _stub_series_biz(calls)
    try:
        with db.get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "delete from market_rates where code='CDI' and ref_date between %s and %s",
                    (date(2023, 11, 1), date(2023, 11, 30)),
                )
                d = date(2023, 11, 13)
                while d <= date(2023, 11, 24):
                    if investments_db.is_br_business_day(d) and d != date(2023, 11, 20):
                        cur.execute(
                            "insert into market_rates(code, ref_date, value) values ('CDI', %s, %s)",
                            (d, 0.05),
                        )
                    d += timedelta(days=1)
                start, end = date(2023, 11, 13), date(2023, 11, 24)
                out = investments_db._get_cdi_daily_map(cur, start, end)
                assert calls == [(12, start, end)], (
                    "20/11/2023 faltando tinha que ser buraco (dia útil pré-lei)"
                )
                assert date(2023, 11, 20) in out
            conn.rollback()
    finally:
        investments_db._fetch_sgs_series_json = original


def test_cache_vazio_com_memo_vigente_nao_vai_a_rede():
    """Célula 5 da máquina: NADA cacheado na janela + memo vigente ⇒ 0 fetch.
    O check do memo vivia sob `if cached:` — com janela vazia (manhã de
    segunda, lote acruado na sexta, BCB ainda não publicou) toda abertura
    pagava um fetch, o dia inteiro."""
    calls: list[tuple] = []

    def stub_vazio(series_code, start, end):
        calls.append((series_code, start, end))
        return []  # BCB: "sem valores no período" — confirma (curto)

    original = investments_db._fetch_sgs_series_json
    investments_db._fetch_sgs_series_json = stub_vazio
    try:
        with db.get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("delete from market_rates where code='CDI' and ref_date >= %s", (date(2026, 4, 1),))
                investments_db._sgs_confirmed_until.pop("CDI", None)
                win = (date(2026, 4, 18), date(2026, 4, 20))  # sáb..seg, cache vazio

                # 1ª chamada: sem memo ⇒ busca (e a resposta vazia confirma).
                assert investments_db._get_cdi_daily_map(cur, *win) == {}
                assert len(calls) == 1

                # Positivo: memo vigente + cache vazio ⇒ 0 fetch novo.
                assert investments_db._get_cdi_daily_map(cur, *win) == {}
                investments_db._get_cdi_daily_map(cur, *win)
                assert len(calls) == 1, f"cache vazio ignorou o memo: {len(calls)} fetches"

                # Negativo: sem o memo de janela vazia ⇒ 2 fetches.
                calls.clear()
                original_covers = investments_db._sgs_empty_window_covers
                investments_db._sgs_empty_window_covers = lambda code, s, e: False
                try:
                    investments_db._get_cdi_daily_map(cur, *win)
                    investments_db._get_cdi_daily_map(cur, *win)
                finally:
                    investments_db._sgs_empty_window_covers = original_covers
                assert len(calls) == 2, "sem consultar o memo tinham de ser 2 fetches"

                # Célula 1 intacta: com dado cacheado FURADO o memo não segura.
                calls.clear()
                for d in (date(2026, 4, 13), date(2026, 4, 16)):  # falta 14 e 15 (úteis)
                    cur.execute(
                        "insert into market_rates(code, ref_date, value) values ('CDI', %s, 0.05)", (d,)
                    )
                investments_db._get_cdi_daily_map(cur, date(2026, 4, 13), date(2026, 4, 16))
                assert len(calls) == 1, "cobertura furada tem de ir à rede mesmo com memo vigente"
            conn.rollback()
    finally:
        investments_db._fetch_sgs_series_json = original


def test_janela_estreita_vazia_nao_cala_janela_historica():
    """Codex-6 (regressão da célula 5): uma janela estreita que veio vazia
    confirmava a SÉRIE inteira, e uma janela histórica sem cache recebia {}
    sem ir à rede — o lote antigo ficava sem render até a confirmação vencer.
    A confirmação de vazio é escopada ao INTERVALO consultado."""
    calls: list[tuple] = []
    vazio = {"on": True}

    def stub(series_code, start, end):
        calls.append((start, end))
        if vazio["on"]:
            return []
        out, d = [], start
        while d <= end:
            if investments_db.is_br_business_day(d):
                out.append({"data": d.strftime("%d/%m/%Y"), "valor": "0.05"})
            d += timedelta(days=1)
        return out

    original = investments_db._fetch_sgs_series_json
    investments_db._fetch_sgs_series_json = stub
    try:
        with db.get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("delete from market_rates where code='CDI'")
                investments_db._sgs_empty_window.pop("CDI", None)

                # 1) janela estreita (sáb..dom) volta vazia e confirma SÓ ela
                assert investments_db._get_cdi_daily_map(cur, date(2026, 4, 18), date(2026, 4, 19)) == {}
                assert len(calls) == 1

                # 2) POSITIVO do achado: janela histórica sem cache TEM de ir
                # à rede (antes recebia {} calada) e trazer os dias.
                vazio["on"] = False
                out = investments_db._get_cdi_daily_map(cur, date(2026, 1, 2), date(2026, 4, 1))
                assert len(calls) == 2, "janela histórica foi calada pela confirmação da estreita"
                assert len(out) > 50, f"janela histórica veio com {len(out)} dias"

                # 3) Positivo de perf preservado: repetir a MESMA janela
                # estreita (⊆ intervalo confirmado) segue sem rede.
                vazio["on"] = True
                cur.execute("delete from market_rates where code='CDI'")
                investments_db._sgs_empty_window.pop("CDI", None)
                calls.clear()
                win = (date(2026, 4, 18), date(2026, 4, 20))
                investments_db._get_cdi_daily_map(cur, *win)
                investments_db._get_cdi_daily_map(cur, *win)
                investments_db._get_cdi_daily_map(cur, date(2026, 4, 19), date(2026, 4, 20))  # ⊆
                assert len(calls) == 1, f"janela contida devia usar o memo: {calls}"
            conn.rollback()
    finally:
        investments_db._fetch_sgs_series_json = original


def test_memo_de_confirmacao_segura_a_rede_no_mesmo_dia():
    """Cauda stale mas rede já consultada hoje ⇒ 0 fetch (série parada num
    feriadão não paga um fetch por chamada); sem o memo ⇒ fetch de novo."""
    calls: list[tuple] = []

    def stub_vazio(series_code, start, end):
        calls.append((series_code, start, end))
        return []  # BCB: "sem valores no período" (resposta real, confirma)

    original = investments_db._fetch_sgs_series_json
    investments_db._fetch_sgs_series_json = stub_vazio
    try:
        with db.get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("delete from market_rates where code='CDI' and ref_date >= %s", (date(2026, 4, 1),))
                for d in (date(2026, 4, 13), date(2026, 4, 14)):
                    cur.execute(
                        "insert into market_rates(code, ref_date, value) values ('CDI', %s, %s)",
                        (d, 0.05),
                    )
                win = (date(2026, 4, 13), date(2026, 4, 20))  # cauda 6d > 4 ⇒ stale
                investments_db._get_cdi_daily_map(cur, *win)
                assert len(calls) == 1  # 1ª chamada do dia consulta a rede
                investments_db._get_cdi_daily_map(cur, *win)
                investments_db._get_cdi_daily_map(cur, *win)
                assert len(calls) == 1, "rede confirmou hoje — chamadas seguintes não podem re-fetchar"

                # get_latest com ref_date velho + memo do fetch acima limpo:
                # 1º fetch confirma, 2º e 3º servem do cache (caso IPCA_12M).
                calls_latest: list[int] = []
                orig_latest = investments_db._fetch_sgs_latest_json
                investments_db._fetch_sgs_latest_json = _stub_latest(
                    calls_latest, [{"data": "01/07/2026", "valor": "4,85"}]
                )
                investments_db._sgs_confirmed_until.clear()
                try:
                    cur.execute("delete from market_rates where code='IPCA_12M'")
                    cur.execute(
                        "insert into market_rates(code, ref_date, value) values ('IPCA_12M', '2026-07-01', 4.85)"
                    )
                    for _ in range(3):
                        out = investments_db.get_latest_ipca_12m(cur)
                    assert len(calls_latest) == 1, f"3 chamadas ⇒ 1 fetch, veio {len(calls_latest)}"
                    assert out == (date(2026, 7, 1), 4.85)
                finally:
                    investments_db._fetch_sgs_latest_json = orig_latest
            conn.rollback()
    finally:
        investments_db._fetch_sgs_series_json = original
