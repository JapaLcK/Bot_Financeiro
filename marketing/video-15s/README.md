# Vídeo PigBank · 15s

Motion graphics vertical (1080×1920, 60 fps) com trilha sintetizada, sem sample de terceiros.
Registrar → Organizar → Open Finance → agentes → final, sobre o texto do hero
("Sua grana. Uma conversa. Tudo mais claro."). Só assets de `frontend/brand/`; valores ilustrativos.

- `index.html`: a composição. Aberta no navegador, toca em loop.
- `soundtrack.py`: gera `out/soundtrack.wav` (numpy). Os efeitos usam os mesmos instantes das animações.
- `render.mjs`: seek quadro a quadro no Chromium → ffmpeg → `out/pigbank-15s.mp4`.

```bash
npm ci
python3 marketing/video-15s/soundtrack.py
FFMPEG=/caminho/do/ffmpeg node marketing/video-15s/render.mjs          # ~4 min
node marketing/video-15s/render.mjs --stills 2.5,8.9                    # só PNGs, para revisar
```

Precisa de um ffmpeg com libx264 (`pip install imageio-ffmpeg` traz um). Se o Chromium do
Playwright não bater com a versão instalada, passe `CHROMIUM=/caminho/do/chrome`.
