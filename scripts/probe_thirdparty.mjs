// 第三方源浏览器探针：在 Actions(Azure IP) 用真实 Chromium 测试
// 1) 第三方字幕站能否抓取字幕  2) 下载站能否下视频
// 产物：probe_results.json + probe_<site>.png（workflow 上传 artifact）

import { chromium } from 'playwright';
import fs from 'fs';

const YT = process.env.YT_URL || 'https://www.youtube.com/watch?v=bajG5ZDyydI';
const VID = (YT.match(/[?&]v=([\w-]{11})/) || [])[1] || 'bajG5ZDyydI';
const results = [];

const UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 ' +
  '(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36';

async function probeSite(name, url, opts = {}) {
  const { waitMs = 12000, extraSteps } = opts;
  const r = { name, url, status: 'unknown', title: '', captured: [], text: '' };
  let browser;
  try {
    browser = await chromium.launch({
      headless: true,
      args: [
        '--no-sandbox',
        '--disable-dev-shm-usage',
        '--disable-blink-features=AutomationControlled',
      ],
    });
    const ctx = await browser.newContext({
      userAgent: UA,
      viewport: { width: 1366, height: 900 },
      locale: 'en-US',
      ignoreHTTPSErrors: true,
    });
    // 抹掉 webdriver 痕迹
    await ctx.addInitScript(() => {
      Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
    });
    const page = await ctx.newPage();

    page.on('response', async (resp) => {
      const u = resp.url();
      if (/\.(srt|vtt|txt)(\?|$)/i.test(u) ||
          /subtitle|caption|transcript|get_dl|download|dl\./i.test(u)) {
        let body = '';
        try { body = (await resp.text()).slice(0, 200); } catch { /* ignore */ }
        r.captured.push({
          url: u.slice(0, 180), status: resp.status(),
          ct: (resp.headers()['content-type'] || '').slice(0, 60),
          body: body.replace(/\s+/g, ' ').slice(0, 120),
        });
      }
    });
    page.on('download', async (dl) => {
      r.captured.push({ download: dl.suggestedFilename(), url: dl.url().slice(0, 180) });
    });

    await page.goto(url, { waitUntil: 'domcontentloaded', timeout: 60000 });
    if (extraSteps) {
      try { await extraSteps(page); } catch (e) {
        r.extraError = String(e.message || e).slice(0, 120);
      }
    }
    await page.waitForTimeout(waitMs);

    r.title = (await page.title()).slice(0, 100);
    const content = await page.content();
    const text = (await page.evaluate(() => document.body?.innerText || '')).slice(0, 600);
    r.text = text.replace(/\s+/g, ' ').trim();

    if (/just a moment|verify you are human|cf-challenge|attention required|checking your browser/i.test(content)) {
      r.status = 'CLOUDFLARE_CHALLENGE';
    } else if (/"status"\s*:\s*false|BLOCKED|Access Denied|403 Forbidden/i.test(content)) {
      r.status = 'BLOCKED';
    } else {
      r.status = 'PAGE_LOADED';
    }
    await page.screenshot({ path: `probe_${name}.png` });
  } catch (e) {
    r.status = 'ERROR: ' + String(e.message || e).slice(0, 120);
    try { if (browser) await browser.close(); } catch { /* ignore */ }
    browser = null;
  } finally {
    if (browser) await browser.close();
  }
  r.captured = r.captured.slice(0, 12);
  results.push(r);
  console.log(`\n===== ${name} =====`);
  console.log(`status: ${r.status}`);
  console.log(`title: ${r.title}`);
  console.log(`text: ${r.text.slice(0, 200)}`);
  if (r.captured.length) console.log(`captured: ${JSON.stringify(r.captured, null, 1)}`);
  return r;
}

// ---------- Phase 1: 第三方字幕站 ----------

// downsub：?url= 参数会自动拉取字幕列表
await probeSite('downsub', 'https://downsub.com/?url=' + encodeURIComponent(YT),
  { waitMs: 18000 });

// savesubs：同上
await probeSite('savesubs', 'https://savesubs.com/?url=' + encodeURIComponent(YT),
  { waitMs: 15000 });

// youtubetotranscript：Cloudflare 挑战站点，多等一会
await probeSite('yt2transcript',
  `https://youtubetotranscript.com/transcript?v=${VID}`,
  { waitMs: 25000 });

// ---------- Phase 2: 视频下载站 ----------

// yt1s：表单站，尝试填 URL 提交
await probeSite('yt1s', 'https://yt1s.pro/en/youtube-to-mp4', {
  waitMs: 10000,
  extraSteps: async (page) => {
    const input = await page.$('input[type="text"], input[name="url"], #txtUrl, input[placeholder]');
    if (input) {
      await input.fill(YT);
      await page.keyboard.press('Enter');
      await page.waitForTimeout(8000);
    }
  },
});

// cobalt web UI：主站下载器，填 URL 后抓它对 api 的请求
await probeSite('cobalt_web', 'https://cobalt.tools/', {
  waitMs: 10000,
  extraSteps: async (page) => {
    const input = await page.$('input[type="url"], input[type="text"], textarea');
    if (input) {
      await input.fill(YT);
      await page.keyboard.press('Enter');
      await page.waitForTimeout(10000);
    }
  },
});

// ---------- 汇总 ----------
fs.writeFileSync('probe_results.json', JSON.stringify(results, null, 2));
const okSub = results.filter(r => /downsub|savesubs|yt2transcript/.test(r.name) &&
  r.captured.some(c => /\.(srt|vtt|txt)/i.test(c.url) || c.download));
console.log('\n===== SUMMARY =====');
console.log('字幕站有抓到文件:', okSub.map(r => r.name).join(',') || '无');
console.log('各站状态:', results.map(r => `${r.name}=${r.status}`).join(' | '));
