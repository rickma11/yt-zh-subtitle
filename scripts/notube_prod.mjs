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
const MAX_TRIES = 5;

function log(...a) { console.log('[video]', ...a); }

async function tryOnce(page, fmt) {
  log('打开 notube.net/en');
  await page.goto('https://notube.net/en', { waitUntil: 'domcontentloaded', timeout: 45000 });
  const accept = page.locator('button:has-text("Accept"), a:has-text("Accept")').first();
  if (await accept.count() > 0) await accept.click({ timeout: 5000 }).catch(() => {});

  const input = page.locator('input#keyword');
  await input.waitFor({ state: 'visible', timeout: 20000 });
  await input.fill(YT_URL);
  await page.selectOption('#myDropdown', fmt);
  log('URL 已填，格式:', fmt);

  const submit = page.locator('#submit-button');
  await submit.waitFor({ state: 'visible', timeout: 20000 });
  await page.waitForFunction(() => {
    const b = document.getElementById('submit-button');
    return b && !b.disabled;
  }, { timeout: 45000 });
  await submit.click({ noWaitAfter: true, timeout: 60000 }).catch(() => {});
  await page.waitForLoadState('domcontentloaded', { timeout: 60000 }).catch(() => {});
  log('已提交，等待转换 ...');

  // 抗导航轮询：notube 转换中会多次跳转/重定向，waitForFunction 会因上下文销毁而立即 reject，
  // 故用 page.evaluate 在 try/catch 内轮询，遇到「上下文销毁」类错误则等待后重试，不视为失败。
  const status = await pollConversion(page, 540000);
  if (status === 'error') throw new Error('notube 返回错误页: ' + page.url());
  if (status === 'timeout') throw new Error('notube 等待转换超时');
  log('转换完成（Conversion complete / 直链出现）');

  const title = await page.locator('.page-title h1').first().textContent().catch(() => null);
  log('标题:', (title || '').trim().slice(0, 60));

  // 直链就嵌在转换页：<a href="https://sXX.notube.net/download.php?token=...&key=...">Download</a>
  // 注意：notube 当前不会跳转到 /download?token=，直链直接在此页。用浏览器会话点击该 <a> 触发下载事件（带 cookie）。
  const dlSelector = 'a[href*="download.php?token="]';
  const dlLink = page.locator(dlSelector).first();
  if (await dlLink.count() === 0) throw new Error('转换完成后未找到 download.php 直链');
  const dlUrl = await dlLink.getAttribute('href');
  log('直链已定位:', (dlUrl || '').slice(0, 120));

  const videoPath = `${OUTDIR}/video.mp4`;
  const dlPromise = page.waitForEvent('download', { timeout: 120000 });
  await dlLink.click({ timeout: 30000 });
  const dl = await dlPromise;
  await dl.saveAs(videoPath);
  const size = statSync(videoPath).size;
  log('视频已保存:', videoPath, (size / 1048576).toFixed(1) + 'MB');
  if (size < 1024 * 1024) throw new Error('视频文件异常过小: ' + size + 'B');
  return { title: (title || '').trim(), dlUrl: dlUrl || '', size };
}

// 抗导航的轮询等待：notube 转换过程中会多次跳转/重定向，waitForFunction 会因上下文销毁而失败，
// 故用 page.evaluate 在 try/catch 内轮询，遇到「上下文销毁」类错误则等待后重试，不视为失败。
// 判定完成：body 出现 "Conversion complete" 文本，或页面已出现 download.php?token= 直链。
async function pollConversion(page, maxMs) {
  const deadline = Date.now() + maxMs;
  while (Date.now() < deadline) {
    try {
      const info = await page.evaluate(() => ({
        url: location.href,
        text: (document.body && document.body.innerText) || '',
        hasLink: !!document.querySelector('a[href*="download.php?token="]'),
      }));
      if (/[?&]error=/.test(info.url)) return 'error';
      if (/Conversion complete/i.test(info.text) || info.hasLink) return 'done';
    } catch (e) {
      if (/context|destroyed|detached|navigated|Execution/i.test(e.message || '')) {
        await page.waitForTimeout(2000).catch(() => {});
        continue; // 导航中，短暂等待后重试
      }
      throw e;
    }
    await page.waitForTimeout(4000);
  }
  return 'timeout';
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
    const fmt = (attempt <= 2) ? FMT : 'mp4'; // 前两次尝试高清，后续降级 360p 加速 notube 转换
    try {
      log(`=== 尝试 ${attempt}/${MAX_TRIES} (格式 ${fmt}) ===`);
      const r = await tryOnce(page, fmt);
      title = r.title; dlUrl = r.dlUrl; size = r.size; ok = true;
      log('下载成功');
    } catch (e) {
      lastErr = e;
      log(`尝试 ${attempt} 失败:`, e.message);
      if (attempt < MAX_TRIES) {
        const back = 60000 * attempt; // 60s,120s,180s,240s 退避，给 notube 队列/限流恢复时间
        log(`${back / 1000}s 后重试 ...`);
        await page.waitForTimeout(back).catch(() => {});
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
