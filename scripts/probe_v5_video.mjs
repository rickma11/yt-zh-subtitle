// 探针 v5：多站点 Playwright 批量实测（从 Actions IP）
// 每站：输入 YouTube URL → 提交 → 等结果 → 尝试点下载 → 捕获 googlevideo 直链 / download 事件
import { chromium } from 'playwright';
import { writeFileSync } from 'node:fs';

const VID = process.env.VID || 'KUf0x6FG7p0';
const YT_URL = `https://www.youtube.com/watch?v=${VID}`;

const SITES = [
  {
    name: 'clipconverter',
    url: 'https://www.clipconverter.cc/3/',
    inputSel: '#mediaurl',
    submitSel: '#submiturl',
  },
  {
    name: 'notube',
    url: `https://notube.net/en/youtube-app-437`,
    inputSel: 'input[type="text"], input[name="url"], input[placeholder*="URL" i]',
    submitSel: null,
  },
  {
    name: 'yt1s',
    url: 'https://yt1s.mx/',
    inputSel: 'input[name="url"], input#s_input, input[type="text"]',
    submitSel: null,
  },
  {
    name: '9xbuddy',
    url: 'https://9xbuddy.com/',
    inputSel: 'input[name="url"], input[type="text"]',
    submitSel: null,
  },
  {
    name: 'ssyoutube',
    url: 'https://en.ssyoutube.com/1ab/',
    inputSel: 'input[name="query"], input#txt-url, input[type="text"]',
    submitSel: null,
  },
  {
    name: 'savefrom',
    url: 'https://en1.savefrom.net/19wr/',
    inputSel: 'input[name="url"], input[type="text"]',
    submitSel: null,
  },
];

async function testSite(browser, site) {
  const ctx = await browser.newContext({ acceptDownloads: true });
  const page = await ctx.newPage();
  const videoUrls = [];
  const result = { site: site.name, status: 'UNKNOWN', details: '' };
  page.on('request', (r) => {
    const u = r.url();
    if (/googlevideo\.com/.test(u)) videoUrls.push(u.slice(0, 200));
  });
  page.on('popup', (p) => { result.popup = p.url().slice(0, 150); });

  try {
    console.log(`\n===== [${site.name}] ${site.url}`);
    await page.goto(site.url, { waitUntil: 'domcontentloaded', timeout: 45000 });
    await page.waitForTimeout(4000);
    console.log(`[${site.name}] title: ${await page.title()}`);

    // 找输入框
    const input = await page.$(site.inputSel) || await page.$('input[type="text"]');
    if (!input) {
      result.status = 'NO_INPUT';
      result.details = '找不到 URL 输入框';
      console.log(`[${site.name}] 找不到输入框，页面元素:`, JSON.stringify(await page.evaluate(() =>
        [...document.querySelectorAll('input')].map((i) => i.name || i.id || i.type).slice(0, 10))));
      await page.screenshot({ path: `v5_${site.name}_noinput.png` });
      await ctx.close();
      return result;
    }
    await input.fill(YT_URL);
    // 提交
    if (site.submitSel) {
      const btn = await page.$(site.submitSel);
      if (btn) await btn.click();
      else await input.press('Enter');
    } else {
      await input.press('Enter');
    }
    console.log(`[${site.name}] 已提交，等待结果 25s...`);
    await page.waitForTimeout(25000);

    const bodyText = (await page.evaluate(() => document.body.innerText)).replace(/\s+/g, ' ');
    console.log(`[${site.name}] 结果页文本(400): ${bodyText.slice(0, 400)}`);
    await page.screenshot({ path: `v5_${site.name}_result.png` });

    // 点下载类元素
    const dlPromise = page.waitForEvent('download', { timeout: 25000 }).catch(() => null);
    const clicked = await page.evaluate(() => {
      const els = [...document.querySelectorAll('a, button, div[onclick], input[type=submit], span')];
      const b = els.find((x) => /download|mp4|720|1080|get/i.test((x.innerText || x.value || '').trim()));
      if (b) { const t = (b.innerText || b.value || '').trim(); b.click(); return t; }
      return null;
    });
    console.log(`[${site.name}] 点击: ${clicked}`);
    const dl = await dlPromise;
    if (dl) {
      const fn = `v5_${site.name}_video`;
      try { await dl.saveAs(fn); } catch (e) {}
      result.status = 'DOWNLOAD_EVENT';
      result.details = dl.suggestedFilename();
      console.log(`[${site.name}] ★ 下载事件: ${dl.suggestedFilename()}`);
    } else {
      await page.waitForTimeout(10000);
      // 检查新页面
      for (const p of ctx.pages()) {
        if (p !== page && !p.isClosed()) {
          console.log(`[${site.name}] 新页面: ${p.url().slice(0, 140)}`);
          try { await p.screenshot({ path: `v5_${site.name}_popup.png` }); } catch (e) {}
        }
      }
      if (videoUrls.length) {
        result.status = 'GOT_VIDEO_URLS';
        result.details = videoUrls.slice(0, 3).join(' ||| ');
        console.log(`[${site.name}] ★ 捕获 googlevideo 直链 ${videoUrls.length} 条`);
      } else {
        result.status = 'NO_DOWNLOAD';
        result.details = '无下载事件、无 googlevideo 请求';
      }
    }
    await page.screenshot({ path: `v5_${site.name}_final.png` });
  } catch (e) {
    result.status = 'ERROR';
    result.details = String(e).slice(0, 150);
    console.log(`[${site.name}] ERROR: ${String(e).slice(0, 150)}`);
    try { await page.screenshot({ path: `v5_${site.name}_error.png` }); } catch (e2) {}
  }
  await ctx.close();
  return result;
}

(async () => {
  const browser = await chromium.launch({
    headless: true,
    args: ['--no-sandbox', '--disable-dev-shm-usage', '--disable-blink-features=AutomationControlled'],
  });
  const results = [];
  for (const site of SITES) {
    try {
      results.push(await testSite(browser, site));
    } catch (e) {
      results.push({ site: site.name, status: 'FATAL', details: String(e).slice(0, 120) });
    }
  }
  writeFileSync('v5_results.json', JSON.stringify(results, null, 1));
  console.log('\n[v5] ===== 汇总 =====');
  for (const r of results) console.log(` ${r.site}: ${r.status} | ${String(r.details).slice(0, 100)}`);
  console.log('[v5] DONE');
  await browser.close();
})().catch((e) => { console.error('FATAL', e); process.exit(1); });
