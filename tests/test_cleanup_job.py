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

    monkeypatch.setattr(cleanup_job, "_log_event", lambda *a, **k: None)
    monkeypatch.setattr(cleanup_job, "_cleanups", lambda: [
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
    assert rc == 1  # o cron precisa marcar a execução como falha
