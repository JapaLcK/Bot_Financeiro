/** Approved homepage: accessible static product story, real routes and artwork. */
import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import { startServer } from "./_server.mjs";
import { chromium } from "playwright";

let origin, server, browser;
before(async () => {
  ({ proc: server, origin } = await startServer());
  browser = await chromium.launch();
});
after(async () => { await browser?.close(); server?.kill(); });

async function openPage(width = 1280, options = {}) {
  const page = await browser.newPage({ viewport: { width, height: 900 }, ...options });
  await page.goto(`${origin}/index.html`);
  await page.evaluate(() => document.fonts.ready);
  return page;
}

for (const width of [320, 390, 768, 1280, 1440]) {
  test(`história e três passos legíveis sem overflow a ${width}px`, async () => {
    const page = await openPage(width);
    const steps = page.locator('.lp-step');
    assert.equal(await steps.count(), 3);
    for (let i = 0; i < 3; i++) {
      await steps.nth(i).scrollIntoViewIfNeeded();
      const box = await steps.nth(i).evaluate(el => {
        const card = el.getBoundingClientRect();
        const title = el.querySelector('h3').getBoundingClientRect();
        const demo = el.querySelector('.lp-surface').getBoundingClientRect();
        return { left: card.left, right: card.right, card, title, demo,
          overlap: Math.min(title.right, demo.right) > Math.max(title.left, demo.left) + 1
            && Math.min(title.bottom, demo.bottom) > Math.max(title.top, demo.top) + 1 };
      });
      assert.ok(box.left >= 0 && box.right <= width + 1, JSON.stringify(box));
      assert.equal(box.overlap, false, 'a demonstração cobre o título');
    }
    assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
    await page.close();
  });
}

test('VSL aparece antes dos passos e permite cadastro imediato', async () => {
  const page = await openPage();
  assert.equal(await page.locator('#vsl-cta').getAttribute('href'), '/cadastro');
  assert.ok(await page.evaluate(() =>
    !!(document.querySelector('#vsl').compareDocumentPosition(document.querySelector('#como-funciona'))
      & Node.DOCUMENT_POSITION_FOLLOWING)));
  assert.equal(await page.locator('#vsl-video').getAttribute('preload'), 'none');
  await page.close();
});

test('FAQ funciona pelo teclado sem depender de JavaScript', async () => {
  const page = await openPage(390, { javaScriptEnabled: false });
  const question = page.locator('.lp-faq details').nth(1);
  await question.locator('summary').focus();
  await page.keyboard.press('Enter');
  assert.equal(await question.getAttribute('open'), '');
  await page.keyboard.press('Enter');
  assert.equal(await question.getAttribute('open'), null);
  await page.close();
});

test('agentes mostram panorama desktop e três recortes quadrados no celular', async () => {
  for (const width of [320, 390, 640, 1280]) {
    const page = await openPage(width);
    await page.locator('#agentes').scrollIntoViewIfNeeded();
    assert.equal(await page.locator('.lp-agents-panorama').isVisible(), width > 640);
    const mobile = page.locator('.lp-agent-mobile-art');
    assert.equal(await mobile.count(), 3);
    for (let i = 0; i < 3; i++) {
      if (width <= 640) {
        // Percorre as três artes antes de verificar seus recortes e espaçamentos.
        await mobile.nth(i).scrollIntoViewIfNeeded();
        await mobile.nth(i).waitFor({ state: 'visible' });
      }
      assert.equal(await mobile.nth(i).isVisible(), width <= 640);
    }
    if (width <= 640) {
      assert.deepEqual(await mobile.evaluateAll(images => images.map(img => getComputedStyle(img).objectPosition)),
        ['0% 50%', '50% 50%', '100% 50%']);
      const box = await mobile.first().boundingBox();
      assert.ok(Math.abs(box.width - box.height) < 1);
      const insets = await page.locator('.lp-agent').evaluateAll(cards => cards.map(card => {
        const outer = card.getBoundingClientRect();
        const art = card.querySelector('img').getBoundingClientRect();
        const badge = card.querySelector('.lp-agent-name').getBoundingClientRect();
        const title = card.querySelector('h3').getBoundingClientRect();
        const description = card.querySelector('p').getBoundingClientRect();
        return { left: title.left - outer.left, right: outer.right - title.right,
          top: badge.top - art.bottom, bottom: outer.bottom - description.bottom };
      }));
      for (const inset of insets) {
        assert.ok(Object.values(inset).every(space => space >= 20),
          `${width}px: o texto deve respirar dentro do card: ${JSON.stringify(inset)}`);
      }
    }
    await page.close();
  }
});

test('hero mantém arte completa e resolução responsiva', async () => {
  for (const width of [390, 1440]) {
    const page = await openPage(width);
    const image = page.locator('.lp-piggy');
    assert.equal(await image.evaluate(el => getComputedStyle(el).objectFit), 'contain');
    assert.ok(await image.evaluate(el => el.complete && el.naturalWidth >= 300 && Math.abs(el.naturalHeight / el.naturalWidth - 1.5) < 0.01));
    assert.equal(await image.getAttribute('fetchpriority'), 'high');
    await page.close();
  }
});

test('prefers-reduced-motion remove movimento sem esconder conteúdo', async () => {
  const page = await openPage(390, { reducedMotion: 'reduce' });
  await page.locator('#como-funciona').scrollIntoViewIfNeeded();
  assert.equal(await page.locator('.lp-step').count(), 3);
  assert.equal(await page.locator('html').evaluate(el => getComputedStyle(el).scrollBehavior), 'auto');
  assert.equal(await page.evaluate(() => document.getAnimations().length), 0);
  await page.close();
});

test('menu leva ao Open Finance sem âncora inexistente em outra página', async () => {
  const page = await openPage(390, { reducedMotion: 'reduce' });
  await page.locator('.pb-burger').click();
  await page.locator('.nav-links a[href="#open-finance"]').click();
  assert.equal(await page.locator('#open-finance .lp-btn').getAttribute('href'), '/funcionalidades');
  assert.equal(await page.locator('.pb-burger').getAttribute('aria-expanded'), 'false');
  assert.ok(await page.locator('#finance-title').isVisible());
  await page.close();
});

test('âncora direta no celular posiciona a seção após o layout inicial', async () => {
  const page = await browser.newPage({ viewport: { width: 390, height: 844 } });
  await page.addInitScript(() => {
    window.__landingScrollEnded = false;
    document.addEventListener('scrollend', () => { window.__landingScrollEnded = true; });
  });
  for (const id of ['agentes', 'open-finance', 'como-funciona']) {
    await page.evaluate(() => { window.__landingScrollEnded = false; });
    await page.goto(`${origin}/index.html#${id}`);
    await page.evaluate(() => document.fonts.ready);
    await page.waitForFunction(() => window.__landingScrollEnded);
    const position = await page.evaluate(async target => {
      await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
      return { top: document.getElementById(target).getBoundingClientRect().top,
        nav: document.querySelector('.nav').getBoundingClientRect().bottom, y: scrollY };
    }, id);
    assert.ok(position.top >= position.nav - 1 && position.top < 422,
      `#${id}: alvo fora da área visível após o layout: ${JSON.stringify(position)}`);
  }
  await page.close();
});

test('rolar a landing sem fragmento mantém a altura documental estável', async () => {
  for (const width of [320, 390, 640]) {
    const page = await openPage(width, { reducedMotion: 'reduce' });
    const initial = await page.evaluate(() => document.documentElement.scrollHeight);
    const sections = page.locator('main > section');
    for (let i = 0; i < await sections.count(); i++) {
      await sections.nth(i).scrollIntoViewIfNeeded();
      const height = await page.evaluate(async () => {
        await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
        return document.documentElement.scrollHeight;
      });
      assert.ok(Math.abs(height - initial) <= 2,
        `${width}px: revelar a seção ${i + 1} mudou a altura da página de ${initial}px para ${height}px`);
    }
    await page.close();
  }
});


test('recarregar sem fragmento preserva a posição de leitura nos agentes', async () => {
  const page = await openPage(390, { reducedMotion: 'reduce' });
  const position = () => page.evaluate(async () => {
    await document.fonts.ready;
    await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
    return { height: document.documentElement.scrollHeight, y: scrollY,
      top: document.getElementById('agentes').getBoundingClientRect().top };
  });
  await page.locator('#agentes').evaluate(el => el.scrollIntoView({ block: 'start', behavior: 'instant' }));
  const before = await position();
  assert.ok(before.y > 1000, 'o teste precisa começar longe do topo');
  assert.equal(new URL(page.url()).hash, '');
  await page.reload();
  const after = await position();
  assert.ok(Math.abs(after.height - before.height) <= 2, `altura mudou: ${JSON.stringify({ before, after })}`);
  assert.ok(Math.abs(after.top - before.top) <= 2, `posição de leitura mudou: ${JSON.stringify({ before, after })}`);
  await page.close();
});
