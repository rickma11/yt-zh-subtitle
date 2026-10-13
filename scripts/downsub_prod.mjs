// 生产：downsub 字幕获取（纯 HTTP，零依赖）
// 优先 zh-Hans（T1 直取中文）；否则 en_auto（T2 源，待翻译）
// 产物：work/zh.srt（若直取成功）/ work/en.srt + work/subs_meta.json
import { createCipheriv, createHash, randomBytes } from 'node:crypto';
import { writeFileSync } from 'node:fs';
import { spawnSync } from 'node:child_process';

const PASS = 'zthxw34cdp6wfyxmpad38v52t3hsz6c5';
const UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36';
const VID = process.env.VID || 'KUf0x6FG7p0';
const OUTDIR = process.env.OUTDIR || 'work';

function log(...a) { console.log('[subs]', ...a); }

function evp(password, salt, klen = 32, ilen = 16) {
  let d = Buffer.alloc(0);
  let prev = Buffer.alloc(0);
  while (d.length < klen + ilen) {
    prev = createHash('md5').update(Buffer.concat([prev, password, salt])).digest();
    d = Buffer.concat([d, prev]);
  }
  return [d.subarray(0, klen), d.subarray(klen, klen + ilen)];
}

function encode(obj, key = PASS) {
  const salt = randomBytes(8);
  const [k, iv] = evp(Buffer.from(key, 'utf8'), salt);
  const pt = Buffer.from(JSON.stringify(obj), 'utf8');
  const cipher = createCipheriv('aes-256-cbc', k, iv);
  const ct = Buffer.concat([cipher.update(pt), cipher.final()]);
  const ser = JSON.stringify({ ct: ct.toString('base64'), iv: iv.toString('hex'), s: salt.toString('hex') });
  return Buffer.from(ser, 'utf8').toString('base64').replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
}

// curl HTTP GET（返回 Buffer）
function curlBuf(url, timeoutSec = 60) {
  const r = spawnSync('curl', ['-sS', '-L', '--max-time', String(timeoutSec), '-A', UA, '-H', 'Referer: https://downsub.com/', url], { maxBuffer: 64 * 1024 * 1024 });
  return r.stdout || Buffer.alloc(0);
}

function fetchInfo(vid, retries = 4) {
  const enc = encode(vid);
  for (let i = 0; i < retries; i++) {
    const body = curlBuf(`https://get-info.downsub.com/${enc}`);
    if (body.length > 100) {
      try { return JSON.parse(body.toString('utf8')); } catch (e) { log('JSON 解析失败，重试', i + 1); }
    }
    log(`get-info 空/异常（第 ${i + 1} 次），10s 后重试 ...`);
    Atomics.wait(new Int32Array(new SharedArrayBuffer(4)), 0, 0, 10000);
  }
  throw new Error('get-info.downsub.com 不可用');
}

function pickTrack(info) {
  const zhList = (info.subtitles || []).concat(info.subtitlesAutoTrans || []).filter(t => /^(zh-Hans|zh-CN)$/.test(t.code));
  const enAuto = (info.subtitles || []).find(t => t.code === 'en_auto') || (info.subtitles || []).find(t => /^en/.test(t.code));
  return { zh: zhList[0] || null, en: enAuto || null };
}

async function main() {
  log('视频:', VID);
  const info = fetchInfo(VID);
  log('标题:', (info.title || '').slice(0, 60));
  const { zh, en } = pickTrack(info);
  const meta = { title: info.title, duration: info.duration, zhTrack: !!zh, enTrack: !!en };

  if (zh) {
    const url = `https://subtitle.downsub.com/srt/${zh.url}`;
    const body = curlBuf(url, 90);
    if (body.length > 200) {
      writeFileSync(`${OUTDIR}/zh.srt`, body);
      meta.zhBytes = body.length;
      log(`T1 命中：zh-Hans 直取 ${meta.zhBytes}B -> zh.srt`);
    } else log('zh-Hans 下载为空，降级 T2');
  }
  if (!meta.zhBytes && en) {
    const url = `https://subtitle.downsub.com/srt/${en.url}`;
    const body = curlBuf(url, 90);
    if (body.length > 200) {
      writeFileSync(`${OUTDIR}/en.srt`, body);
      meta.enBytes = body.length;
      log(`T2 源就绪：en_auto ${meta.enBytes}B -> en.srt（待翻译）`);
    } else throw new Error('en_auto 下载为空');
  }
  if (!meta.zhBytes && !meta.enBytes) throw new Error('无可用字幕轨');
  writeFileSync(`${OUTDIR}/subs_meta.json`, JSON.stringify(meta, null, 1));
  log('DONE');
}
main().catch(e => { console.error('FATAL', e.message); process.exit(1); });
