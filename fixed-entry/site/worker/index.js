const enc = new TextEncoder();
const headers = {
  "Cache-Control": "no-store", "Referrer-Policy": "no-referrer", "X-Content-Type-Options": "nosniff",
  "Content-Security-Policy": "default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; form-action 'self'; frame-ancestors 'none'; base-uri 'none'"
};
const text = (body, status = 200) => new Response(body, {status, headers});
const bytes = s => Uint8Array.from(atob(s), v => v.charCodeAt(0));
const hex = b => Array.from(new Uint8Array(b), x => x.toString(16).padStart(2, '0')).join('');
const hostPattern = /^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.trycloudflare\.com$/;
const algorithm = {name:"RSASSA-PKCS1-v1_5", hash:"SHA-256"};
async function keyFrom(record) {
  const key = await crypto.subtle.importKey('spki', bytes(record.pub), algorithm, false, ['verify']);
  if (key.algorithm.modulusLength !== 2048) throw Error();
  return key;
}
async function live(record, origin, fetcher) {
  const key = await keyFrom(record);
  if (!hostPattern.test(record.host)) return false;
  const nonce = hex(crypto.getRandomValues(new Uint8Array(24)));
  const response = await fetcher('https://' + record.host + '/.well-known/mac-remote-proof/' + nonce,
    {redirect:'error', signal:AbortSignal.timeout(5500), headers:{Accept:'application/json'}});
  if (!response.ok || !response.headers.get('content-type')?.startsWith('application/json')) return false;
  const {signature} = await boundedJSON(response, 1024);
  return crypto.subtle.verify(algorithm, key, bytes(signature), enc.encode(['proof',origin,record.host,nonce].join('\n')));
}
async function boundedJSON(message, limit) {
  const reader = message.body.getReader();
  const chunks = [];
  let size = 0;
  for (;;) {
    const {value,done} = await reader.read();
    if (done) break;
    size += value.length;
    if (size > limit) { await reader.cancel(); throw Error('payload too large'); }
    chunks.push(value);
  }
  return new Response(new Blob(chunks)).json();
}
const css = 'body{font:17px/1.6 system-ui,sans-serif;background:#131714;color:#eff5ee;margin:0}main{max-width:30rem;margin:12vh auto;padding:1.5rem}h1{font-size:2rem}a,button{display:inline-block;padding:.7rem 1rem;border:0;border-radius:.5rem;background:#c4efaa;color:#182017;text-decoration:none;font:inherit}input{font:inherit;padding:.6rem;max-width:100%}p{overflow-wrap:anywhere}';
const js = `
const status = document.querySelector('#status');
const form = document.querySelector('form');
const hash = location.hash.slice(1);
history.replaceState(null, '', location.pathname);
let data;
try { data = JSON.parse(decodeURIComponent(hash)); } catch {}
async function send(fingerprint = '') {
  if (!data) {status.textContent = 'Mac 연결 도우미에서 등록을 시작해 주세요.'; return;}
  try {
    const r = await fetch('/api/register', {method:'POST', headers:{'Content-Type':'application/json'},body:JSON.stringify({...data,fingerprint})});
    status.textContent = await r.text();
    form.hidden = r.status !== 428;
  } catch {status.textContent = '네트워크 연결을 확인하고 Mac에서 등록을 다시 열어 주세요.';}
}
form.onsubmit = e => {e.preventDefault();send(form.elements.fingerprint.value.trim().toLowerCase());};
send();`;
function page(title, body) {
  return new Response('<!doctype html><html lang="ko"><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>'+title+'</title><link rel="stylesheet" href="/style.css"><main><h1>'+title+'</h1>'+body+'</main></html>', {headers:{...headers,'Content-Type':'text/html; charset=utf-8'}});
}
export async function handle(req, env, fetcher = fetch) {
  const u = new URL(req.url);
  if (req.method === 'GET' && u.pathname === '/health') {
    await env.BUCKET.head('health');
    return text('ready');
  }
  const user = req.headers.get('oai-authenticated-user-id');
  if (!user) return text('로그인이 필요합니다.', 401);
  const name = 'mac/' + hex(await crypto.subtle.digest('SHA-256',enc.encode(user)));
  if (req.method === 'GET') {
    if (u.pathname === '/style.css') return new Response(css,{headers:{...headers,'Content-Type':'text/css'}});
    if (u.pathname === '/register.js') return new Response(js,{headers:{...headers,'Content-Type':'text/javascript'}});
    if (u.pathname === '/register') return page('Mac 등록','<p role="status" id="status">등록 정보를 확인하는 중입니다.</p><form hidden><label>Mac 도우미에 표시된 16자리 기기 지문 <input name="fingerprint" required maxlength="16" autocomplete="off"></label> <button>이 Mac 등록</button></form><script src="/register.js"></script>');
    if (!['/', '/connect'].includes(u.pathname)) return text('찾을 수 없습니다.',404);
    const stored = await env.BUCKET.get(name);
    if (!stored) return page('내 Mac 연결','<p>아직 등록된 Mac이 없습니다. Mac에서 선택 설치한 연결 도우미를 열고 인터넷 연결을 켜 주세요.</p><a href="/">다시 확인</a>');
    if (u.pathname === '/') return page('내 Mac 연결','<p>연결을 열면 Mac의 4자리 코드와 승인이 필요합니다.</p><a href="/connect">현재 Mac 열기</a>');
    const record = await stored.json();
    if (await live(record,u.origin,fetcher).catch(()=>false)) return new Response(null,{status:303,headers:{...headers,Location:'https://'+record.host+'/'}});
    return page('Mac이 응답하지 않습니다','<p>Mac의 인터넷 연결과 도우미 등록 상태를 확인해 주세요.</p><a href="/connect">다시 연결</a>');
  }
  if (req.method !== 'POST' || u.pathname !== '/api/register') return text('찾을 수 없습니다.',404);
  if (req.headers.get('Origin') !== u.origin || !req.headers.get('Content-Type')?.startsWith('application/json')) return text('요청 출처 오류',403);
  const length = Number(req.headers.get('content-length'));
  if (length > 2400) return text('요청 크기 오류',400);
  let rec, key;
  try {
    rec = await boundedJSON(req, 2400);
    if (typeof rec.host !== 'string' || !hostPattern.test(rec.host) || typeof rec.pub !== 'string' || rec.pub.length > 450 ||
        !Number.isSafeInteger(rec.ts) || Math.abs(Date.now()/1000-rec.ts)>300) throw Error();
    key = await keyFrom(rec);
    if (!await crypto.subtle.verify(algorithm,key,bytes(rec.signature),enc.encode(['register',u.origin,rec.host,String(rec.ts)].join('\n')))) throw Error();
  } catch {return text('등록 정보가 만료됐거나 서명이 올바르지 않습니다. Mac에서 인터넷 연결을 다시 켜 주세요.',403);}
  const obj = await env.BUCKET.get(name);
  if (obj) {
    const prev = await obj.json();
    if (prev.pub !== rec.pub || prev.ts > rec.ts) return text('이미 등록된 Mac의 현재 키가 필요합니다.',403);
  } else {
    const fp = hex(await crypto.subtle.digest('SHA-256',bytes(rec.pub))).slice(0,16);
    if (rec.fingerprint !== fp) return text('처음 한 번 Mac에 표시된 기기 지문을 입력해 주세요.',428);
  }
  if (!await live(rec,u.origin,fetcher).catch(()=>false)) return text('Mac의 HTTPS 주소와 기기 서명을 검증하지 못했습니다. Mac에서 인터넷 연결을 다시 켜 주세요.',409);
  const stored = await env.BUCKET.put(name, JSON.stringify({host:rec.host,pub:rec.pub,ts:rec.ts}),{
    onlyIf: obj ? {etagMatches:obj.etag} : {etagDoesNotMatch:'*'}
  });
  return stored ? text('등록됐습니다. 휴대폰에서 고정 주소를 열어 주세요.') : text('다른 등록이 진행됐습니다. 다시 시도해 주세요.',409);
}
export default {fetch:(req,env)=>handle(req,env)};
