// Render the per-scene CSS shells (text column, frame, story nav) for every locale.
const fs = require('fs'), path = require('path');
const { chromium } = require(process.env.PLAYWRIGHT || 'playwright');
const here = __dirname, out = path.join(process.env.WORK || '/tmp/ai-switch-clip', 'shells');
const copy = JSON.parse(fs.readFileSync(path.join(here, 'copy.json'), 'utf8'));
const segs = JSON.parse(fs.readFileSync(path.join(here, 'segments.json'), 'utf8'));
const fonts = path.resolve(here, '../../../../../site/assets/fonts');
const css = fs.readFileSync(path.join(here, 'design.css'), 'utf8').replace("url('fonts/", `url('file://${fonts}/`);
const esc = s => String(s).replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('\n', '<br>');
(async () => {
  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1200, height: 720 } });
  for (const lang of ['en', 'ko', 'ja', 'zh-CN']) {
    fs.mkdirSync(path.join(out, lang), { recursive: true });
    for (const [n, s] of segs.entries()) {
      const bridge = 'bridge' in s, stage = bridge ? 1 : s.stage;
      const steps = copy.steps[lang];
      const nav = `<nav class="story-nav">${steps.map((t, i) => `<span class="${i === stage ? 'active' : ''}">${esc(t)}</span>`).join('')}<span class="story-count">0${stage + 1} / 0${steps.length}</span></nav>`;
      const head = `<header class="masthead"><div class="brand">Still</div><div class="meta">${copy.meta[lang]}</div></header>`;
      const title = bridge ? copy.bridge[lang][0] : copy.titles[lang][stage];
      const body = bridge ? copy.bridge[lang][1] : copy.captions[s.key][lang];
      const media = bridge ? '' : `<div class="media"><div class="media-label"><span>TELEGRAM · CONVERSATION</span><span>${copy.excerpt[lang]}</span></div><div class="viewport"></div></div>`;
      const html = `<!doctype html><html lang="${lang}"><meta charset="utf-8"><style>${css}</style><body><main class="movie ${bridge ? 'bridge' : ''}">${head}<section class="copy"><p class="eyebrow">${esc(bridge ? copy.next[lang] : steps[stage])}</p><h1>${esc(title)}</h1><p class="description">${esc(body)}</p></section>${media}${nav}</main></body></html>`;
      const tmp = path.join(out, '..', 'current.html'); fs.writeFileSync(tmp, html);
      await page.goto('file://' + tmp); await page.evaluate(() => document.fonts.ready);
      const b = await page.locator('.copy').boundingBox();
      if (b.y + b.height > 638) throw Error(`copy overflow ${lang} ${n}: ${b.y + b.height}`);
      const h1 = await page.locator('h1').boundingBox(); if (!bridge && h1.height > (lang === 'en' ? 160 : 110)) throw Error(`title wraps ${lang} ${n}: ${h1.height}`);
      await page.screenshot({ path: path.join(out, lang, String(n).padStart(2, '0') + '.png') });
    }
    console.log('shells', lang);
  }
  await browser.close();
})();
