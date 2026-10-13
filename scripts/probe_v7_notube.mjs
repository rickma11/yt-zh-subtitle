// 探针 v7：专注 notube.net 完整下载流程
// v5 已证实 notube 从 Actions IP 转换成功（"Conversion complete"），但点错了元素
import { chromium } from 'playwright';
import { writeFileSync } from 'node:fs';

const VID = process.env.VID || 'KUf0x6FG7p0';
const YT_URL = `https://www.youtube.com/watch?v=${VID}`;

(async () => {
  const browser = await chromium.launch({
    headless: true,
    args: ['--no-sandbox', '--disable-dev-shm-usage', '--disable-blink-features=AutomationControlled'],
  });
  const ctx = await browser.newContext({ acceptDownloads: true });
  const page = await ctx.newPage();
  const net = [];
  page.on('request', (r) => {
    const u = r.url();
    if (/googlevideo|\.mp4|download|cdn|media/i.test(u) && !/notube\.net\/(en|js|css|img)/.test(u)) {
      net.push(r.method() + ' ' + u.slice(0, 200));
    }
  });
  page.on('response', (r) => {
    const u = r.url();
    if (/\.mp4|googlevideo|download/i.test(u) && !/notube\.net\/(en|js|css|img)/.test(u)) {
      net.push('RESP ' + r.status() + ' ' + u.slice(0, 200));
    }
  });

  const result = { video_id: VID, status: 'UNKNOWN' };
  try {
    console.log('[v7] 打开 notube.net');
    await page.goto('https://notube.net/en/youtube-app-437', { waitUntil: 'domcontentloaded', timeout: 45000 });
    await page.waitForTimeout(4000);

    // 输入 URL
    const input = await page.$('input[type="text"]') || await page.$('input[name="url"]');
    if (!input) throw new Error('找不到输入框');
    await input.fill(YT_URL);
    await input.press('Enter');
    console.log('[v7] 已提交，等待转换（最多 60s）...');

    // 等 "Conversion complete"
    await page.waitForFunction(
      () => /Conversion complete/i.test(document.body.innerText),
      { timeout: 60000 }
    ).catch(() => console.log('[v7] 未见到 Conversion complete，继续尝试'));
    await page.waitForTimeout(3000);
    await page.screenshot({ path: 'v7_notube_converted.png' });

    // 精准找绿色 Download 按钮：文本恰为 Download，排除推广
    const btn = await page.evaluate(() => {
      const els = [...document.querySelectorAll('a, button, div[role="button"], span')];
      const cands = els.filter((el) => {
        const t = (el.innerText || '').trim();
        return /^Download$/i.test(t) && !/playlist|app/i.test(t);
      });
      if (!cands.length) return null;
      // 优先可见的、最上层的
      const vis = cands.filter((el) => el.offsetParent !== null);
      const target = vis[0] || cands[0];
      const r = target.getBoundingClientRect();
      return { text: target.innerText.trim(), tag: target.tagName, cls: (target.className || '').toString().slice(0, 80), x: r.x, y: r.y, href: target.href || null };
    });
    console.log('[v7] 下载按钮候选:', JSON.stringify(btn));
    if (!btn) throw new Error('找不到 Download 按钮');

    const dlPromise = page.waitForEvent('download', { timeout: 60000 }).catch(() => null);
    if (btn.href && btn.href.startsWith('http')) {
      // 直接导航到 href（或点击）
      const popupPromise = ctx.waitForEvent('page', { timeout: 15000 }).catch(() => null);
      await page.click(`text="${btn.text}"`).catch(async () => {
        // 点击失败就用坐标
        await page.mouse.click(btn.x + 10, btn.y + 10);
      });
      const popup = await popupPromise;
      if (popup) {
        result.popup_url = popup.url();
        console.log('[v7] 新页面 URL:', popup.url().slice(0, 200));
        await popup.waitForTimeout(5000).catch(() => {});
        await popup.screenshot({ path: 'v7_notube_dlpage.png' }).catch(() => {});
      }
    } else {
      await page.mouse.click(btn.x + 10, btn.y + 10);
    }
    const dl = await dlPromise;
    if (dl) {
      const fn = 'v7_notube_video';
      try { await dl.saveAs(fn); } catch (e) { console.log('[v7] saveAs 失败:', String(e).slice(0, 100)); }
      result.status = 'DOWNLOAD_EVENT';
      result.file = dl.suggestedFilename();
      console.log('[v7] ★★ 下载事件:', dl.suggestedFilename(), '->', fn);
    } else {
      await page.waitForTimeout(8000);
      // 当前 URL 可能已变
      console.log('[v7] 点击后当前 URL:', page.url().slice(0, 200));
      await page.screenshot({ path: 'v7_notube_after_click.png' });
      for (const p of ctx.pages()) {
        if (!p.isClosed()) console.log('[v7] 页面:', p.url().slice(0, 180));
      }
      result.status = net.some((n) => /googlevideo|\.mp4/.test(n)) ? 'GOT_URLS' : 'NO_DOWNLOAD';
    }
    console.log('[v7] 网络请求(视频相关):');
    net.slice(0, 15).forEach((n) => console.log('   ', n));
    result.net = net.slice(0, 20);
  } catch (e) {
    result.status = 'ERROR';
    result.error = String(e).slice(0, 200);
    console.log('[v7] ERROR:', String(e).slice(0, 200));
    try { await page.screenshot({ path: 'v7_notube_error.png' }); } catch (e2) {}
  }
  writeFileSync('v7_notube_results.json', JSON.stringify(result, null, 1));
  console.log('[v7] DONE', result.status);
  await browser.close();
})().catch((e) => { console.error('FATAL', e); process.exit(1); });
