// 生产：notube.net 视频下载器（带重试，抗 notube 偶发 error=2）
// 流程：notube.net/en -> input#keyword -> mp4hd -> OK -> 等待 Conversion complete
//       -> 进入下载页 -> 捕获 download 事件 -> 当场落盘（URL 短时效）
// 产物：work/video.mp4 + work/notube_meta.json
import { chromium } from 'playwright';
import { writeFileSync, existsSync, statSync } from 'node:fs';
import { spawnSync } from 'node:child_process';

const VID = process.env.VID || 'KUf0x6FG7p0';
const YT_URL = `https://www.youtube.com/watch?v=${VID}`;
const OUTDIR = process.env.OUTDIR || 'work';
const FMT = process.env.FMT || 'mp4hd'; // mp4=360p, mp4hd=720p/1080p
const MAX_TRIES = 3;

function log(...a) { console.log('[video]', ...a); }

async function tryOnce(page) {
  log('打开 notube.net/en');
  await page.goto('https://notube.net/en', { waitUntil: 'domcontentloaded', timeout: 45000 });
  const accept = page.locator('button:has-text("Accept"), a:has-text("Accept")').first();
  if (await accept.count() > 0) await accept.click({ timeout: 5000 }).catch(() => {});

  const input = page.locator('input#keyword');
  await input.waitFor({ state: 'visible', timeout: 20000 });
  await input.fill(YT_URL);
  await page.selectOption('#myDropdown', FMT);
  log('URL 已填，格式:', FMT);

  const submit = page.locator('#submit-button');
  await submit.waitFor({ state: 'visible', timeout: 20000 });
  await page.waitForFunction(() => {
    const b = document.getElementById('submit-button');
    return b && !b.disabled;
  }, { timeout: 45000 });
  await submit.click();
  log('已提交，等待转换 ...');

  // 同时等待「转换完成」或「错误页」；错误页会跳到 ...&error=2，快速失败以便重试
  const result = await page.waitForFunction(() => {
    if (/[?&]error=/.test(location.href)) return 'error';
    const t = (document.body && document.body.innerText) || '';
    if (/Conversion complete/i.test(t)) return 'done';
    return false;
  }, { timeout: 360000, polling: 4000 }).then(h => h.jsonValue()).catch(() => 'timeout');
  if (result === 'error') throw new Error('notube 返回错误页: ' + page.url());
  if (result === 'timeout') throw new Error('notube 等待转换超时');

  const title = await page.locator('.page-title h1').first().textContent().catch(() => null);
  log('转换完成，标题:', (title || '').trim().slice(0, 60));

  // 进下载页
  const dlBtn = page.locator('a:has-text("Download"), button:has-text("Download")').first();
  if (await dlBtn.count() > 0) await dlBtn.click({ timeout: 15000, noWaitAfter: true }).catch(() => {});
  await page.waitForTimeout(3000);
  if (!/\/download\?token=/.test(page.url())) throw new Error('未进入下载页: ' + page.url());
  log('下载页:', page.url().slice(0, 90));
  await page.waitForTimeout(5000);

  // JS 原生点击绿色 Download（精确匹配文本），等待下载事件
  const dlPromise = page.waitForEvent('download', { timeout: 60000 });
  const clicked = await page.evaluate(() => {
    const btns = [...document.querySelectorAll('a, button')];
    let b = btns.find(x => (x.textContent || '').trim() === 'Download');
    if (b) { b.click(); return true; }
    return false;
  });
  if (!clicked) throw new Error('下载页找不到 Download 按钮');
  const dl = await dlPromise;
  const dlUrl = dl.url();
  log('直链已捕获:', dlUrl.slice(0, 120));

  const videoPath = `${OUTDIR}/video.mp4`;
  await dl.saveAs(videoPath);
  const size = statSync(videoPath).size;
  log('视频已保存:', videoPath, (size / 1048576).toFixed(1) + 'MB');
  if (size < 1024 * 1024) throw new Error('视频文件异常过小: ' + size + 'B');
  return { title: (title || '').trim(), dlUrl, size };
}

async function main() {
  const browser = await chromium.launch({ headless: true, args: ['--no-sandbox', '--disable-blink-features=AutomationControlled'] });
  const ctx = await browser.newContext({
    userAgent: 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36',
    viewport: { width: 1280, height: 800 },
    acceptDownloads: true,
  });
  const page = await ctx.newPage();
  let dlUrl = null, title = null, size = 0, ok = false, lastErr = null;

  for (let attempt = 1; attempt <= MAX_TRIES && !ok; attempt++) {
    try {
      log(`=== 尝试 ${attempt}/${MAX_TRIES} ===`);
      const r = await tryOnce(page);
      title = r.title; dlUrl = r.dlUrl; size = r.size; ok = true;
      log('下载成功');
    } catch (e) {
      lastErr = e;
      log(`尝试 ${attempt} 失败:`, e.message);
      if (attempt < MAX_TRIES) {
        log('5s 后重试 ...');
        await page.waitForTimeout(5000).catch(() => {});
      }
    }
  }

  try {
    if (ok) {
      writeFileSync(`${OUTDIR}/notube_meta.json`, JSON.stringify({
        vid: VID, title, url: page.url(), dlUrl, size,
      }, null, 1));
      log('DONE');
    } else {
      throw lastErr || new Error('未知失败');
    }
  } catch (e) {
    log('ERROR:', e.message);
    await page.screenshot({ path: `${OUTDIR}/video_error.png`, fullPage: false }).catch(() => {});
    // 若直链已捕获但 saveAs 失败，立即 curl 兜底（秒级窗口）
    if (dlUrl && !ok) {
      log('尝试 curl 兜底下载 ...');
      const r = spawnSync('curl', ['-sL', '--max-time', '900', '-o', `${OUTDIR}/video.mp4`, '-A', 'Mozilla/5.0', dlUrl], { encoding: 'utf8' });
      if (existsSync(`${OUTDIR}/video.mp4`) && statSync(`${OUTDIR}/video.mp4`).size > 1024 * 1024) {
        const s = statSync(`${OUTDIR}/video.mp4`).size;
        log('curl 兜底成功:', (s / 1048576).toFixed(1) + 'MB');
        writeFileSync(`${OUTDIR}/notube_meta.json`, JSON.stringify({ vid: VID, dlUrl, size: s }, null, 1));
        ok = true;
      }
    }
    if (!ok) {
      writeFileSync(`${OUTDIR}/video_error.txt`, (lastErr && lastErr.message) || 'unknown');
      process.exitCode = 2;
    }
  }
  await browser.close();
}
main().catch(e => { console.error('FATAL', e); process.exit(1); });
