// downsub 交互探针 v3：带超时保护 + 即时日志 + 全局看门狗
// 目标：1) 英文 SRT 真实下载  2) 全字幕轨列表(查中文)  3) 完整视频+字幕下载
import { chromium } from 'playwright';
import fs from 'fs';

const YT = process.env.YT_URL || 'https://www.youtube.com/watch?v=bajG5ZDyydI';
const UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 ' +
  '(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36';
const out = { events: [] };
const log = (...a) => { console.log(...a); };

function rec(o) { out.events.push(o); log('[EVENT]', JSON.stringify(o).slice(0, 400)); }

// 全局看门狗：4 分钟强制收尾
const watchdog = setTimeout(() => {
  log('WATCHDOG: 超时强制收尾');
  fs.writeFileSync('probe_v3_results.json', JSON.stringify(out, null, 2));
  process.exit(0);
}, 240000);

const race = (p, ms, tag) => Promise.race([
  p, new Promise((_, rej) => setTimeout(() => rej(new Error(tag + ' timeout')), ms)),
]);

const browser = await chromium.launch({
  headless: true,
  args: ['--no-sandbox', '--disable-dev-shm-usage',
    '--disable-blink-features=AutomationControlled'],
});
const ctx = await browser.newContext({
  userAgent: UA, viewport: { width: 1366, height: 900 },
  locale: 'en-US', acceptDownloads: true, ignoreHTTPSErrors: true,
});
await ctx.addInitScript(() => {
  Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
});
const page = await ctx.newPage();

page.on('download', async (dl) => {
  const o = { type: 'download', file: dl.suggestedFilename(), url: dl.url().slice(0, 300) };
  rec(o);
  try {
    const p = 'dl_' + dl.suggestedFilename().replace(/[^\w.-]/g, '_');
    await race(dl.saveAs(p), 30000, 'saveAs');
    o.saved = p; o.size = fs.statSync(p).size;
    log('[DL SAVED]', p, o.size, 'bytes');
  } catch (e) { o.saveErr = String(e.message).slice(0, 120); log('[DL ERR]', o.saveErr); }
});
page.on('popup', async (pop) => {
  rec({ type: 'popup', url: pop.url().slice(0, 300) });
  try { await pop.waitForLoadState('domcontentloaded', { timeout: 10000 }); } catch { /* ignore */ }
  try { await pop.screenshot({ path: 'probe_v3_popup.png' }); } catch { /* ignore */ }
});
page.on('request', (req) => {
  const u = req.url();
  if (/get\.downsub|download|\.srt|\.vtt|subtitle\.to/i.test(u)) {
    rec({ type: 'request', url: u.slice(0, 300) });
  }
});

log('STEP 1: 打开 downsub');
await race(page.goto('https://downsub.com/?url=' + encodeURIComponent(YT),
  { waitUntil: 'domcontentloaded', timeout: 60000 }), 60000, 'goto');
log('STEP 2: 等字幕轨列表 15s');
await page.waitForTimeout(15000);
await page.screenshot({ path: 'probe_v3_loaded.png' });

log('STEP 3: 列全部字幕轨');
const tracks = await page.evaluate(() => {
  const rows = [];
  document.querySelectorAll('*').forEach(el => {
    const t = (el.innerText || '').trim();
    const m = t.match(/^([A-Za-z\u4e00-\u9fff ()-]+)\s*\((auto-generated|auto-translated|translated)\)$/);
    if (m && el.children.length === 0 && !rows.some(r => r.lang === m[1])) {
      rows.push({ lang: m[1], kind: m[2] });
    }
  });
  return rows;
});
out.tracks = tracks;
log('字幕轨数量:', tracks.length);
log('语言列表:', tracks.map(t => `${t.lang}(${t.kind})`).join(', '));
log('中文相关轨:', JSON.stringify(tracks.filter(t =>
  /chinese|中文|mandarin|cantonese/i.test(t.lang))));

log('STEP 4: 点击英文行 SRT');
try {
  const row = page.locator('div:has-text("English (auto-generated)")').last();
  await race(row.locator('button:has-text("SRT"), a:has-text("SRT")').first()
    .click({ timeout: 15000 }), 20000, 'srt click');
  log('SRT 点击成功，等待下载/请求 25s');
  await page.waitForTimeout(25000);
  await page.screenshot({ path: 'probe_v3_after_srt.png' });
} catch (e) {
  out.srtErr = String(e.message).slice(0, 150);
  log('SRT 点击失败:', out.srtErr);
}

log('STEP 5: 点击 Download Full Video With Subtitle');
try {
  const fullBtn = page.locator('text=Download Full Video With Subtitle').last();
  await race(fullBtn.click({ timeout: 15000 }), 20000, 'fullvideo click');
  log('完整视频按钮点击成功，等待 45s');
  await page.waitForTimeout(45000);
  await page.screenshot({ path: 'probe_v3_after_fullvideo.png' });
} catch (e) {
  out.fullVideoErr = String(e.message).slice(0, 150);
  log('完整视频点击失败:', out.fullVideoErr);
}

clearTimeout(watchdog);
fs.writeFileSync('probe_v3_results.json', JSON.stringify(out, null, 2));
log('DONE. 事件数:', out.events.length);
process.exit(0);
