# core/handlers/help_handler.py
from __future__ import annotations
from core.help_text import render_full, render_help, TUTORIAL_TEXT, resolve_section


def help_general(platform: str) -> str:
    return render_full(platform)


def help_section(section: str, platform: str) -> str:
    key = resolve_section(section)
    return render_help(key, platform)


def tutorial(platform: str) -> str:
    return render_help("tutorial", platform)
