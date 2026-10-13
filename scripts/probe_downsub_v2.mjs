// downsub 交互探针 v2：从 Actions IP 实际点击下载
// 目标：1) 抓英文 SRT 真实下载 URL  2) 列全部字幕轨(看有无中文)  3) 试"完整视频+字幕"下载
import { chromium } from 'playwright';
import fs from 'fs';

const YT = process.env.YT_URL || 'https://www.youtube.com/watch?v=bajG5ZDyydI';
const VID = (YT.match(/[?&]v=([\w-]{11})/) || [])[1];
const UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 ' +
  '(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36';
const out = {};

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

const dlEvents = [];
page.on('download', async (dl) => {
  dlEvents.push({ file: dl.suggestedFilename(), url: dl.url().slice(0, 250) });
  try {
    const p = 'dl_' + dl.suggestedFilename().replace(/[^\w.-]/g, '_');
    await dl.saveAs(p);
    dlEvents[dlEvents.length - 1].saved = p;
    dlEvents[dlEvents.length - 1].size = fs.statSync(p).size;
  } catch (e) { dlEvents[dlEvents.length - 1].saveErr = String(e.message).slice(0, 100); }
});
page.on('request', (req) => {
  if (/downsub|subtitle\.to|get\./i.test(req.url()) && !req.url().includes('downsub.com/?url')) {
    dlEvents.push({ req: req.url().slice(0, 250) });
  }
});

await page.goto('https://downsub.com/?url=' + encodeURIComponent(YT),
  { waitUntil: 'domcontentloaded', timeout: 60000 });
await page.waitForTimeout(15000); // 等字幕轨列表渲染
await page.screenshot({ path: 'probe_v2_loaded.png' });

// 1) 列全部字幕轨
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
console.log('字幕轨数量:', tracks.length);
console.log('语言列表:', tracks.map(t => `${t.lang}(${t.kind})`).join(', '));
const zh = tracks.filter(t => /chinese|中文|mandarin|zh|cantonese/i.test(t.lang));
console.log('中文相关轨:', JSON.stringify(zh));

// 2) 点英文 SRT
try {
  const enRow = page.locator('text=/English \\(auto-generated\\)/').last();
  await enRow.waitFor({ timeout: 10000 });
  // 找该行容器里的 SRT 按钮
  const srtBtn = page.locator('button:has-text("SRT"), a:has-text("SRT")').nth(2);
  await srtBtn.click({ timeout: 10000 });
  console.log('已点击英文行 SRT 按钮');
  await page.waitForTimeout(20000);
  await page.screenshot({ path: 'probe_v2_after_srt.png' });
} catch (e) {
  out.srtClickErr = String(e.message).slice(0, 150);
  console.log('SRT 点击失败:', out.srtClickErr);
}
out.dlEvents = dlEvents.slice(0, 15);

// 3) 试完整视频下载
try {
  const fullBtn = page.locator('text=Download Full Video With Subtitle').last();
  await fullBtn.click({ timeout: 10000 });
  console.log('已点击 Download Full Video With Subtitle');
  await page.waitForTimeout(60000);
  await page.screenshot({ path: 'probe_v2_after_fullvideo.png' });
} catch (e) {
  out.fullVideoErr = String(e.message).slice(0, 150);
  console.log('完整视频点击失败:', out.fullVideoErr);
}
out.dlEventsFinal = dlEvents.slice(0, 20);

fs.writeFileSync('probe_v2_results.json', JSON.stringify(out, null, 2));
console.log('\n===== 下载事件 =====');
console.log(JSON.stringify(dlEvents.slice(0, 20), null, 1));
