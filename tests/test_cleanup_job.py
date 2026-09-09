"""scripts/cleanup_job.py — uma limpeza que falha não pode derrubar as outras."""


def test_falha_em_uma_tabela_nao_impede_as_outras(monkeypatch, capsys):
    from scripts import cleanup_job

    chamadas: list[str] = []

    def _ok(nome, n):
        def _fn():
            chamadas.append(nome)
            return n
        return _fn

    def _explode():
        chamadas.append("mfa_login_challenges")
        raise RuntimeError("conexão caiu no meio")

    monkeypatch.setattr(cleanup_job, "log_event", lambda *a, **k: None)
    monkeypatch.setattr("core.services.table_cleanup._cleanups", lambda: [
        ("auth_refresh_tokens", _ok("auth_refresh_tokens", 3)),
        ("mfa_login_challenges", _explode),
        ("pending_google_signups", _ok("pending_google_signups", 0)),
    ])

    rc = cleanup_job.run()
    saida = capsys.readouterr()

    assert chamadas == ["auth_refresh_tokens", "mfa_login_challenges", "pending_google_signups"]
    assert "auth_refresh_tokens: 3 linha(s) removida(s)" in saida.out
    assert "pending_google_signups: 0 linha(s) removida(s)" in saida.out
    assert "mfa_login_challenges: FALHOU" in saida.err
    assert rc == 1  # a execução manual precisa sair vermelha


def test_falha_no_delete_de_refresh_tokens_chega_ao_resultado(monkeypatch, capsys):
    """Sem try/except engolindo a exceção, a falha do DELETE tem de sair no rc e em errors."""
    from scripts import cleanup_job

    def _get_conn_quebrado(*a, **k):
        raise RuntimeError("connection refused")

    # get_conn real quebrado: exercita cleanup_expired_refresh_tokens de verdade,
    # via o _cleanups() real. As outras duas viram no-op pra não tocar no banco.
    monkeypatch.setattr("core.refresh_tokens.get_conn", _get_conn_quebrado)
    monkeypatch.setattr("db.mfa.cleanup_expired_challenges", lambda: 0)
    monkeypatch.setattr("db.google_auth.cleanup_expired_pending_signups", lambda: 0)

    eventos: list[tuple[str, dict]] = []
    monkeypatch.setattr(cleanup_job, "log_event", lambda level, msg, det: eventos.append((level, det)))

    rc = cleanup_job.run()
    saida = capsys.readouterr()

    assert rc == 1, "o job ficaria verde sobre tabela que não foi podada"
    assert [(lvl, [e["table"] for e in det["errors"]]) for lvl, det in eventos] == [
        ("error", ["auth_refresh_tokens"])
    ]
    assert "auth_refresh_tokens" not in eventos[0][1]["removed"]
    assert "auth_refresh_tokens: FALHOU" in saida.err
