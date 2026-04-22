import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from core.ai_rate_limiter import is_allowed, remaining, reset

def test_bloqueia_apos_limite():
    reset(1)
    for i in range(10):
        assert is_allowed(1), f"Falhou na chamada {i+1}, deveria ser permitida"
    assert not is_allowed(1), "Chamada 11 deveria ser BLOQUEADA"
    print("✅ Bloqueio após limite: OK")

def test_usuarios_independentes():
    reset(1); reset(2)
    for _ in range(10):
        is_allowed(1)
    assert is_allowed(2), "Usuário 2 não deveria ser afetado pelo limite do usuário 1"
    print("✅ Isolamento entre usuários: OK")

def test_janela_deslizante():
    """Só roda se AI_RATE_LIMIT_WINDOW_SEC=2 estiver definido."""
    window = float(os.getenv("AI_RATE_LIMIT_WINDOW_SEC", "60"))
    if window > 5:
        print(f"⏭️  Janela deslizante pulado (window={window}s). Rode com AI_RATE_LIMIT_WINDOW_SEC=2 para testar.")
        return
    reset(3)
    for _ in range(10):
        is_allowed(3)
    assert not is_allowed(3), "Deveria estar bloqueado"
    print(f"⏳ Esperando {window + 1:.0f}s para janela expirar...")
    time.sleep(window + 1)
    assert is_allowed(3), "Deveria estar liberado após expirar a janela"
    print("✅ Janela deslizante: OK")

def test_remaining():
    reset(4)
    assert remaining(4) == 10
    is_allowed(4)
    assert remaining(4) == 9
    print("✅ Contagem de restantes: OK")

if __name__ == "__main__":
    test_bloqueia_apos_limite()
    test_usuarios_independentes()
    test_remaining()
    test_janela_deslizante()
    print("\n✅ Todos os testes passaram!")