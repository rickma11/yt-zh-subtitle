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

    log('填入 URL 到 input#keyword');
    const input = page.locator('input#keyword');
    await input.waitFor({ state: 'visible', timeout: 20000 });
    await input.fill(YT_URL);

    log('提交表单');
    // 优先点 OK 按钮，失败则回车
    const submit = page.locator('#submit-button');
    if (await submit.count() > 0 && await submit.isEnabled().catch(() => false)) {
      await submit.click();
    } else {
      await input.press('Enter');
    }

    log('等待转换完成 ...');
    await page.waitForSelector('text=Conversion complete', { timeout: 180000 });
    log('转换完成！当前页面:', page.url());
    await page.screenshot({ path: `${OUTDIR}/v8_notube_complete.png`, fullPage: false });

    // 记录当前页面所有链接
    const links = await page.evaluate(() => [...document.querySelectorAll('a')].map(a => ({ href: a.href, text: (a.textContent || '').trim().slice(0, 40) })).filter(x => x.href));
    writeFileSync(`${OUTDIR}/v8_links.json`, JSON.stringify(links, null, 1));
    log(`页面共 ${links.length} 个链接`);

    // 找绿色 Download 按钮（排除 "Convert another video"）
    const dlBtn = page.locator('a:has-text("Download"), button:has-text("Download")').first();
    if (await dlBtn.count() > 0) {
      log('点击 Download 按钮');
      // 同时等待导航或下载
      await Promise.race([
        dlBtn.click().then(() => page.waitForTimeout(5000)),
      ]).catch(e => log('点击异常:', e.message));
      log('点击后页面:', page.url());
      await page.screenshot({ path: `${OUTDIR}/v8_after_download.png`, fullPage: false });

      // 如果跳到了带 token 的下载页，再解析里面的直链
      const html = await page.content();
      const hrefs = [...html.matchAll(/href="([^"]+\.(?:mp4|webm)[^"]*)"/g)].map(m => m[1]);
      hrefs.forEach(h => found.downloadUrls.add(h.startsWith('http') ? h : new URL(h, page.url()).href));
      const gv = [...html.matchAll(/https?:\/\/[^"'\s<>]+googlevideo[^"'\s<>]*/g)].map(m => m[0]);
      gv.forEach(h => found.downloadUrls.add(h));
      log(`解析到直链 ${found.downloadUrls.size} 个`);
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
