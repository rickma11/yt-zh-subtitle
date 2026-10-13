// 探针 v4-B：Playwright 从 Actions IP 实测第三方视频下载站（y2mate.mx / y2meta.com）
// 目标：拿到可直接下载的 googlevideo 直链或触发真实下载
import { chromium } from 'playwright';
import { writeFileSync } from 'fs';

const VID = process.env.VID || 'KUf0x6FG7p0';

(async () => {
  const browser = await chromium.launch({
    headless: true,
    args: ['--no-sandbox', '--disable-dev-shm-usage', '--disable-blink-features=AutomationControlled'],
  });
  const results = {};

  // ---------- 站点 1：y2mate.mx ----------
  await (async () => {
    const ctx = await browser.newContext({ acceptDownloads: true });
    const page = await ctx.newPage();
    const videoUrls = [];
    page.on('request', (r) => {
      const u = r.url();
      if (/googlevideo\.com|\.mp4|redirector/.test(u)) videoUrls.push(u.slice(0, 220));
    });
    try {
      console.log('=== [y2mate] 打开 https://y2mate.mx/youtube/' + VID);
      await page.goto(`https://y2mate.mx/youtube/${VID}`, { waitUntil: 'domcontentloaded', timeout: 60000 });
      await page.waitForTimeout(8000);
      const title = await page.title();
      console.log('[y2mate] title:', title);
      // dump 可点击元素
      const els = await page.evaluate(() => {
        const out = [];
        document.querySelectorAll('a, button, input[type=submit], div.btn').forEach((el) => {
          const t = (el.innerText || el.value || el.getAttribute('title') || '').trim().replace(/\s+/g, ' ');
          if (t && t.length < 70) out.push(t + ' <' + el.tagName.toLowerCase() + '>');
        });
        return [...new Set(out)].slice(0, 50);
      });
      console.log('[y2mate] 可点击元素:', JSON.stringify(els));
      await page.screenshot({ path: 'v4_y2mate_loaded.png' });

      // 点第一个 download 类按钮
      const dlPromise = page.waitForEvent('download', { timeout: 30000 }).catch(() => null);
      const clicked = await page.evaluate(() => {
        const btns = [...document.querySelectorAll('a, button, div[onclick], input[type=submit]')];
        const b = btns.find((x) => /download/i.test(x.innerText || x.value || ''));
        if (b) { b.click(); return (b.innerText || b.value || '').trim(); }
        return null;
      });
      console.log('[y2mate] 点击:', clicked);
      const dl = await dlPromise;
      if (dl) {
        const p = 'v4_y2mate_video';
        await dl.saveAs(p).catch(() => {});
        console.log('[y2mate] 下载事件:', dl.suggestedFilename());
        results.y2mate = 'DOWNLOAD_EVENT: ' + dl.suggestedFilename();
      } else {
        await page.waitForTimeout(8000);
        const after = await page.evaluate(() => document.body.innerText.slice(0, 400).replace(/\s+/g, ' '));
        console.log('[y2mate] 点击后页面文本:', after);
        await page.screenshot({ path: 'v4_y2mate_after_click.png' });
        // 新标签页？
        const pages = ctx.pages();
        for (const p of pages) {
          if (p !== page) {
            console.log('[y2mate] 新标签页 URL:', p.url().slice(0, 150));
            try { await p.screenshot({ path: 'v4_y2mate_popup.png' }); } catch (e) {}
          }
        }
        results.y2mate = videoUrls.length ? 'VIDEO_URLS: ' + videoUrls.slice(0, 5).join(' | ') : 'NO_DOWNLOAD';
      }
      console.log('[y2mate] 捕获到的视频类请求:', videoUrls.slice(0, 8));
    } catch (e) {
      console.log('[y2mate] ERROR:', String(e).slice(0, 200));
      results.y2mate = 'ERROR: ' + String(e).slice(0, 150);
      try { await page.screenshot({ path: 'v4_y2mate_error.png' }); } catch (e2) {}
    }
    await ctx.close();
  })();

  // ---------- 站点 2：y2meta.com ----------
  await (async () => {
    const ctx = await browser.newContext({ acceptDownloads: true });
    const page = await ctx.newPage();
    const videoUrls = [];
    page.on('request', (r) => {
      const u = r.url();
      if (/googlevideo\.com|\.mp4|redirector/.test(u)) videoUrls.push(u.slice(0, 220));
    });
    try {
      console.log('=== [y2meta] 打开 https://www.y2meta.com/en/youtube/' + VID);
      await page.goto(`https://www.y2meta.com/en/youtube/${VID}`, { waitUntil: 'domcontentloaded', timeout: 60000 });
      await page.waitForTimeout(8000);
      console.log('[y2meta] title:', await page.title());
      const els = await page.evaluate(() => {
        const out = [];
        document.querySelectorAll('a, button, input[type=submit], div.btn').forEach((el) => {
          const t = (el.innerText || el.value || el.getAttribute('title') || '').trim().replace(/\s+/g, ' ');
          if (t && t.length < 70) out.push(t + ' <' + el.tagName.toLowerCase() + '>');
        });
        return [...new Set(out)].slice(0, 50);
      });
      console.log('[y2meta] 可点击元素:', JSON.stringify(els));
      await page.screenshot({ path: 'v4_y2meta_loaded.png' });

      const dlPromise = page.waitForEvent('download', { timeout: 30000 }).catch(() => null);
      const clicked = await page.evaluate(() => {
        const btns = [...document.querySelectorAll('a, button, div[onclick], input[type=submit]')];
        const b = btns.find((x) => /download/i.test(x.innerText || x.value || ''));
        if (b) { b.click(); return (b.innerText || b.value || '').trim(); }
        return null;
      });
      console.log('[y2meta] 点击:', clicked);
      const dl = await dlPromise;
      if (dl) {
        await dl.saveAs('v4_y2meta_video').catch(() => {});
        console.log('[y2meta] 下载事件:', dl.suggestedFilename());
        results.y2meta = 'DOWNLOAD_EVENT: ' + dl.suggestedFilename();
      } else {
        await page.waitForTimeout(8000);
        const after = await page.evaluate(() => document.body.innerText.slice(0, 400).replace(/\s+/g, ' '));
        console.log('[y2meta] 点击后页面文本:', after);
        await page.screenshot({ path: 'v4_y2meta_after_click.png' });
        for (const p of ctx.pages()) {
          if (p !== page) {
            console.log('[y2meta] 新标签页 URL:', p.url().slice(0, 150));
            try { await p.screenshot({ path: 'v4_y2meta_popup.png' }); } catch (e) {}
          }
        }
        results.y2meta = videoUrls.length ? 'VIDEO_URLS: ' + videoUrls.slice(0, 5).join(' | ') : 'NO_DOWNLOAD';
      }
      console.log('[y2meta] 捕获到的视频类请求:', videoUrls.slice(0, 8));
    } catch (e) {
      console.log('[y2meta] ERROR:', String(e).slice(0, 200));
      results.y2meta = 'ERROR: ' + String(e).slice(0, 150);
      try { await page.screenshot({ path: 'v4_y2meta_error.png' }); } catch (e2) {}
    }
    await ctx.close();
  })();

  writeFileSync('v4_video_results.json', JSON.stringify(results, null, 1));
  console.log('[v4-B] DONE', JSON.stringify(results));
  await browser.close();
})().catch((e) => { console.error('FATAL', e); process.exit(1); });
