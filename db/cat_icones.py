"""Ícone Phosphor de uma categoria, derivado do emoji que o banco guarda.

Fonte única no servidor: o emoji (inclusive o que o usuário escolheu) continua no banco e no payload; `icone` é só o
nome do ícone para o cliente desenhar. O mapa é o MESMO `EMOJI_TO_PH` do `frontend/dashboard.js` (HTML/JS legado não
importa Python; `tests/test_cat_icones.py` compara os dois e confere cada nome contra `frontend/phosphor.css`).
"""
ICONE_NEUTRO = "tag"

EMOJI_PARA_ICONE = {
    "🏷": "tag", "🍔": "hamburger", "🍟": "hamburger", "🛒": "shopping-cart", "🚗": "car", "💊": "pill",
    "🏠": "house", "🎬": "film-slate", "📚": "books", "📖": "book-open", "📺": "television", "🐾": "paw-print",
    "🖥": "desktop", "🖨": "printer", "✈": "airplane-tilt", "🎮": "game-controller", "👕": "t-shirt",
    "👟": "sneaker-move", "👜": "handbag", "🍺": "beer-stein", "☕": "coffee", "⛽": "gas-pump", "💼": "briefcase",
    "💻": "laptop", "🎁": "gift", "📈": "trend-up", "💰": "coins", "🤝": "handshake", "₿": "currency-btc",
    "🎓": "graduation-cap", "🐶": "dog", "👶": "baby", "🏋": "barbell", "🎵": "music-notes", "💄": "heart-straight",
    "🧴": "spray-bottle", "🎂": "cake", "🎯": "target", "🛟": "lifebuoy", "📱": "device-mobile", "💎": "diamond",
    "📸": "camera", "🎸": "guitar", "🛏": "bed", "🏖": "umbrella", "🐷": "piggy-bank", "🍽": "fork-knife",
    "🍴": "fork-knife", "🥖": "bread", "🥩": "fork-knife", "🍕": "pizza", "🌐": "globe", "💧": "drop",
    "💡": "lightbulb", "🔥": "flame", "🚇": "train", "🚌": "bus", "❤": "heart", "🔧": "wrench", "🍎": "apple-logo",
    "🔎": "magnifying-glass", "⛪": "church", "🎟": "ticket", "🚕": "taxi", "🏦": "bank", "🏢": "buildings",
    "🏛": "bank", "🦷": "tooth", "🩺": "stethoscope", "🛡": "shield", "🧺": "basket", "🧹": "broom",
    "🎭": "mask-happy", "🎤": "microphone", "🎄": "tree-evergreen", "💐": "flower", "☁": "cloud", "🍷": "wine",
    "💇": "scissors", "🖌": "paint-brush", "🎨": "paint-brush", "🪴": "tree", "🩹": "first-aid", "📄": "file-text",
    "🧾": "receipt", "📅": "calendar-dots", "📊": "chart-bar", "💳": "credit-card", "💸": "trend-down",
    "📡": "broadcast",
}


def icone_da_categoria(emoji) -> str:
    """O nome do ícone do `emoji` (sem U+FE0F nem espaços, como o `phIcon` do dashboard); vazio ou desconhecido = `tag`."""
    return EMOJI_PARA_ICONE.get((emoji or "").replace("\ufe0f", "").strip(), ICONE_NEUTRO)
