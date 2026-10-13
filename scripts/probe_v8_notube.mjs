// notube.net Playwright 完整下载流程（v8）
// input#keyword -> 提交 -> 等 "Conversion complete" -> 点绿色 Download -> 捕获下载事件/直链
import { chromium } from 'playwright';
import { writeFileSync, mkdirSync } from 'node:fs';

const VID = process.env.VID || 'KUf0x6FG7p0';
const YT_URL = `https://www.youtube.com/watch?v=${VID}`;
const OUTDIR = process.env.OUTDIR || '.';

function log(...a) { console.log('[v8]', ...a); }

const found = { downloadUrls: new Set(), requests: [], finalUrl: null, downloadFile: null };

async function main() {
  mkdirSync(OUTDIR, { recursive: true });
  const browser = await chromium.launch({ headless: true, args: ['--no-sandbox', '--disable-blink-features=AutomationControlled'] });
  const ctx = await browser.newContext({
    userAgent: 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36',
    viewport: { width: 1280, height: 800 },
    acceptDownloads: true,
  });
  const page = await ctx.newPage();

  // 捕获所有视频相关请求
  page.on('request', req => {
    const u = req.url();
    if (/googlevideo|\.mp4|\.webm|\.mkv|download/i.test(u) && !/notube\.net\/(en\/)?(get-app|plus|faq|blog|compatibility)/.test(u)) {
      found.requests.push(u.slice(0, 300));
      if (/googlevideo|\.mp4|\.webm/i.test(u)) found.downloadUrls.add(u);
    }
  });
  page.on('download', async dl => {
    log('捕获下载事件:', dl.url());
    found.downloadUrls.add(dl.url());
    found.downloadFile = dl.url();
    try { await dl.saveAs(`${OUTDIR}/v8_video.bin`); } catch (e) { log('saveAs 失败:', e.message); }
  });
  page.on('popup', async p => {
    const u = p.url();
    log('popup:', u);
    if (/\.mp4|\.webm|googlevideo/i.test(u)) found.downloadUrls.add(u);
  });

  try {
    log('打开 notube.net/en');
    await page.goto('https://notube.net/en', { waitUntil: 'domcontentloaded', timeout: 45000 });

    // cookie 横幅
    const accept = page.locator('button:has-text("Accept"), a:has-text("Accept")').first();
    if (await accept.count() > 0) {
      await accept.click({ timeout: 5000 }).catch(() => {});
      log('已点 cookie Accept');
    }

    log('填入 URL 到 input#keyword');
    const input = page.locator('input#keyword');
    await input.waitFor({ state: 'visible', timeout: 20000 });
    await input.fill(YT_URL);

    // 选择 MP4 HD 格式
    const fmt = process.env.FORMAT || 'mp4hd';
    await page.selectOption('#myDropdown', fmt).catch(async e => {
      log('selectOption 失败，尝试点击式:', e.message);
      await page.click('#fmtBtn').catch(() => {});
      await page.locator(`[role="option"][data-value="${fmt}"], li:has-text("MP4")`).first().click({ timeout: 5000 }).catch(() => {});
    });
    log('格式已选:', fmt);

    log('等待 OK 按钮启用（weight.php 响应）...');
    const submit = page.locator('#submit-button');
    await submit.waitFor({ state: 'visible', timeout: 20000 });
    await page.waitForFunction(() => {
      const b = document.getElementById('submit-button');
      return b && !b.disabled;
    }, { timeout: 30000 });
    log('OK 按钮已启用，点击提交');
    await submit.click();

    log('等待转换完成 ...');
    await page.waitForSelector('text=Conversion complete', { timeout: 300000 });
    log('转换完成！当前页面:', page.url());
    await page.screenshot({ path: `${OUTDIR}/v8_notube_complete.png`, fullPage: false });

    // 点绿色 Download → 应跳到 /en/download?token=
    const dlBtn = page.locator('a:has-text("Download"), button:has-text("Download")').first();
    if (await dlBtn.count() > 0) {
      await dlBtn.click({ timeout: 15000, noWaitAfter: true }).catch(e => log('点击异常(继续):', e.message));
    }
    // 等待进入下载页或直接触发下载
    await page.waitForTimeout(3000);
    log('当前页面:', page.url());
    if (/\/download\?token=/.test(page.url())) {
      log('已进入下载页，等待 JS 渲染 ...');
      await page.waitForTimeout(5000);
      await page.screenshot({ path: `${OUTDIR}/v8_download_page.png`, fullPage: false });
      // 记录所有链接
      const links2 = await page.evaluate(() => [...document.querySelectorAll('a')].map(a => ({ href: a.href, text: (a.textContent || '').trim().slice(0, 40) })).filter(x => x.href));
      writeFileSync(`${OUTDIR}/v8_download_links.json`, JSON.stringify(links2, null, 1));
      // 找直接 .mp4/.webm 链接
      for (const l of links2) {
        if (/\.(mp4|webm|mkv)(\?|$)/i.test(l.href)) found.downloadUrls.add(l.href);
      }
      // 页面 HTML 里找直链
      const html = await page.content();
      [...html.matchAll(/https?:\/\/[^"'\s<>]+(?:googlevideo|\.mp4|\.webm)[^"'\s<>]*/g)].forEach(m => found.downloadUrls.add(m[0]));
      // 点击下载按钮捕获 download 事件
      const realDl = page.locator('a:has-text("ownload"), button:has-text("ownload")').first();
      if (await realDl.count() > 0) {
        log('点击下载页 Download 按钮');
        const dlPromise = page.waitForEvent('download', { timeout: 30000 }).catch(() => null);
        await realDl.click({ timeout: 10000, noWaitAfter: true }).catch(e => log('点击异常(继续):', e.message));
        const dl = await dlPromise;
        if (dl) {
          log('下载事件 URL:', dl.url());
          found.downloadUrls.add(dl.url());
          found.downloadFile = dl.url();
        } else {
          log('无下载事件，等待后重查直链');
          await page.waitForTimeout(4000);
          const html2 = await page.content();
          [...html2.matchAll(/https?:\/\/[^"'\s<>]+(?:googlevideo|\.mp4|\.webm)[^"'\s<>]*/g)].forEach(m => found.downloadUrls.add(m[0]));
        }
      }
      await page.screenshot({ path: `${OUTDIR}/v8_after_download.png`, fullPage: false });
    }
    found.finalUrl = page.url();
  } catch (e) {
    log('ERROR:', e.message);
    await page.screenshot({ path: `${OUTDIR}/v8_error.png`, fullPage: false }).catch(() => {});
    writeFileSync(`${OUTDIR}/v8_error.txt`, e.message);
    process.exitCode = 2;
  }

  writeFileSync(`${OUTDIR}/v8_results.json`, JSON.stringify({
    vid: VID,
    finalUrl: found.finalUrl,
    downloadUrls: [...found.downloadUrls],
    downloadFile: found.downloadFile,
    requestSample: found.requests.slice(0, 20),
  }, null, 1));
  log('DONE, 直链数:', found.downloadUrls.size);
  await browser.close();
}
main().catch(e => { console.error('FATAL', e); process.exit(1); });
