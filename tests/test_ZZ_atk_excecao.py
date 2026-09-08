"""Nomeia a excecao exata (sem TestClient engolir)."""
import pytest
from fastapi.testclient import TestClient
import core.admin_dashboard as admin_dashboard
import frontend.finance_bot_websocket_custom as dashboard

def test_nomeia_excecao_note():
    from db.affiliates import mark_payout_paid
    try:
        mark_payout_paid(999_999_999, "a\x00b")
        print("\n  note NUL -> SEM EXCECAO")
    except Exception as e:
        print(f"\n  mark_payout_paid(note='a\\x00b') -> {type(e).__module__}.{type(e).__name__}: {str(e)[:140]}")
    try:
        mark_payout_paid(999_999_999, "\ud800")
        print("  note surrogate -> SEM EXCECAO")
    except Exception as e:
        print(f"  mark_payout_paid(note='\\ud800')  -> {type(e).__module__}.{type(e).__name__}: {str(e)[:140]}")

def test_nomeia_excecao_jsonb():
    from db.open_finance import update_pluggy_open_finance_item_status as up
    for nome, ev in [("NUL", {"event":"item/error","x":"a\x00b"}),
                     ("surrogate", {"event":"item/error","x":"\ud800"})]:
        try:
            up("atk-nomeia", "ERROR", ev)
            print(f"\n  Jsonb {nome} -> SEM EXCECAO")
        except Exception as e:
            print(f"\n  update_pluggy..._status(raw com {nome}) -> {type(e).__module__}.{type(e).__name__}: {str(e).splitlines()[0][:140]}")
