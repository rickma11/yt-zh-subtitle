// 探针 v4-A（Node 零依赖版）：验证 downsub HTTP API 从 Actions IP 可用
// - get-info.downsub.com/{enc_id} 拿视频信息+字幕轨
// - subtitle.downsub.com/srt/{track_url} 直链下载 SRT（en_auto + zh-Hans）
import { createCipheriv, createHash, randomBytes } from 'node:crypto';
import { writeFileSync } from 'node:fs';

const PASS = 'zthxw34cdp6wfyxmpad38v52t3hsz6c5';
const UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36';
const VID = process.env.VID || 'KUf0x6FG7p0';

// OpenSSL EVP_BytesToKey（MD5, 1 iter），与 CryptoJS 字符串口令派生一致
function evp(password, salt, klen = 32, ilen = 16) {
  let d = Buffer.alloc(0);
  let prev = Buffer.alloc(0);
  while (d.length < klen + ilen) {
    prev = createHash('md5').update(Buffer.concat([prev, password, salt])).digest();
    d = Buffer.concat([d, prev]);
  }
  return [d.subarray(0, klen), d.subarray(klen, klen + ilen)];
}

// 复刻前端 $encode：base64url(JSON.stringify(AES-CBC(JSON.stringify(obj), key)))
// 注意：createCipheriv 默认开启 PKCS7 自动填充（与 CryptoJS 一致），不要手动 pad
function encode(obj, key = PASS) {
  const salt = randomBytes(8);
  const [k, iv] = evp(Buffer.from(key, 'utf8'), salt);
  const pt = Buffer.from(JSON.stringify(obj), 'utf8');
  const cipher = createCipheriv('aes-256-cbc', k, iv);
  const ct = Buffer.concat([cipher.update(pt), cipher.final()]);
  const ser = JSON.stringify({ ct: ct.toString('base64'), iv: iv.toString('hex'), s: salt.toString('hex') });
  return Buffer.from(ser, 'utf8').toString('base64')
    .replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
}

async function fetchUrl(url, timeout = 60000) {
  const ctrl = new AbortController();
  const t = setTimeout(() => ctrl.abort(), timeout);
  try {
    const r = await fetch(url, {
      headers: {
        'User-Agent': UA,
        Accept: 'application/json, text/plain, */*',
        Referer: 'https://downsub.com/',
        Origin: 'https://downsub.com',
      },
      signal: ctrl.signal,
    });
    const ct = r.headers.get('content-type') || '';
    const buf = Buffer.from(await r.arrayBuffer());
    return { status: r.status, ct, buf };
  } finally {
    clearTimeout(t);
  }
}

async function main() {
  console.log(`[v4-A] video_id = ${VID}`);
  const enc = encode(VID);
  console.log('[v4-A] enc_id 前40位:', enc.slice(0, 40));

  const info = await fetchUrl(`https://get-info.downsub.com/${enc}`);
  const data = JSON.parse(info.buf.toString('utf-8'));
  console.log(`[v4-A] get-info: status=${info.status} state=${data.state} title=${String(data.title).slice(0, 60)} duration=${data.duration}`);
  const subs = data.subtitles || [];
  const auto = data.subtitlesAutoTrans || [];
  console.log(`[v4-A] 自动生成轨 ${subs.length} 条：${subs.map((t) => t.code).join(',')}`);
  console.log(`[v4-A] 自动翻译轨 ${auto.length} 条（含 zh-Hans: ${auto.some((t) => t.code === 'zh-Hans')}）`);

  const results = { video_id: VID, state: data.state, downloads: {} };
  for (const [code, lst] of [['en_auto', subs], ['zh-Hans', auto]]) {
    const track = lst.find((t) => t.code === code);
    if (!track) { console.log(`[v4-A] ${code}: 轨道不存在`); results.downloads[code] = 'MISSING'; continue; }
    try {
      const r = await fetchUrl(`https://subtitle.downsub.com/srt/${track.url}`);
      const text = r.buf.toString('utf-8');
      writeFileSync(`v4_${code}.srt`, text);
      const lines = text.split('\n');
      const first = lines.length > 2 ? lines[2] : '';
      console.log(`[v4-A] ${code}: status=${r.status} ctype=${r.ct} bytes=${r.buf.length} 首行: ${first.slice(0, 60)}`);
      results.downloads[code] = { status: r.status, bytes: r.buf.length };
    } catch (e) {
      console.log(`[v4-A] ${code}: ERROR ${String(e).slice(0, 150)}`);
      results.downloads[code] = 'ERROR: ' + String(e).slice(0, 120);
    }
  }
  writeFileSync('v4_downsub_api_results.json', JSON.stringify(results, null, 1));
  console.log('[v4-A] DONE');
}

main().catch((e) => { console.error('FATAL', e); process.exit(1); });
