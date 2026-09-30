# Vídeo PigBank · Open Finance · 30s

Anúncio vertical (1080×1920, 60 fps, 30 s) feito de uma única animação contínua: cada cena nasce de um
objeto da anterior (vórtice → celular → botão → linhas → hub → dashboard → donut → cartão → parcelas →
timeline → gráfico → dashboard claro → formas → linhas → escudo → contorno do Piggy → marca).
Só assets oficiais de `frontend/brand/` e `app/assets/brand/`; valores ilustrativos (há nota no vídeo).

| arquivo | papel |
|---|---|
| `index.html`, `main.js` | boot, fontes, imagens; `window.__seek(t)` desenha o quadro (motion blur por subamostragem + grão) |
| `lib.js` | easings, cores/temas, texto cinético (`rollText`), morph de caminhos |
| `items.js` | objetos financeiros (linhas, cartão, pílulas, mini gráficos) usados no caos e no app |
| `hook.js` | 0–5s: câmera em perspectiva, título que se espalha e é sugado pelo vórtice |
| `connect.js` | 5–9,5s: celular, toque, bordas do botão viram linhas, nós, hub |
| `dash.js`, `payoff.js` | 9,5–23s: dashboard no "mundo" e a câmera que viaja por ele; íris para o tema claro |
| `finale.js` | 23–30s: cards → formas; toggle desligado (a conexão bate no escudo) e ligado (passa ao PigBank); contorno do Piggy (traçado do alfa do mascote) |
| `scenes.js` | orquestra as passadas de tema (íris) e converte o tempo do vídeo no tempo de composição (`TIME_KNOTS` do `lib.js`) |
| `soundtrack.py` | partitura e sound design (numpy), 120 bpm, sincronizados aos mesmos instantes |
| `synth.py` | primitivas de síntese (osciladores, filtros, percussão, efeitos) da trilha |
| `render.mjs` | seek quadro a quadro no Chromium → ffmpeg → MP4 |

As dependências Python ficam num venv local (`.venv/` está no `.gitignore`), fora do `requirements.txt`.

```bash
cd marketing/video-30s-open-finance
python3 -m venv .venv && .venv/bin/pip install numpy imageio-ffmpeg
.venv/bin/python soundtrack.py                          # out/soundtrack.wav
FFMPEG=$(.venv/bin/python -c "import imageio_ffmpeg as f; print(f.get_ffmpeg_exe())") \
  node render.mjs                                       # out/pigbank-open-finance-30s.mp4 (~20 min)
node render.mjs --stills 2.5,8.9                        # PNGs soltos, para revisar
node render.mjs --sheet 0,1,2,3 --cols 4                # folha de contato (out/sheet.png)
```

`render.mjs` precisa do pacote `playwright` resolvível a partir da raiz do repo (`npm ci`) e de um
Chromium; se o do Playwright não bater com a versão, passe `CHROMIUM=/caminho/chrome`.

Ao mudar um instante de animação, mude o efeito correspondente em `soundtrack.py`.

As animações foram escritas num "tempo de composição" (0–22 s) e cada cena é esticada por `TIME_KNOTS` (lib.js) para ganhar tempo de leitura:
gancho 5 s, conexão 4,5 s, payoff 9,5 s, organização 4 s, segurança e marca 7 s. O `soundtrack.py` usa a mesma tabela (`KNOTS`).
