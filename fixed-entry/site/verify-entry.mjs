import assert from 'node:assert/strict';
import {generateKeyPairSync, sign, createHash} from 'node:crypto';
import {handle} from './worker/index.js';
const origin = 'https://entry.example';
const bucket = new Map();
const env = {BUCKET:{
  async get(k) {const s=bucket.get(k); return s && {etag:'test',json:async()=>JSON.parse(s)};},
  async put(k,v){bucket.set(k,v);return {};},async head(){return null;}
}};
const device = () => generateKeyPairSync('rsa',{modulusLength:2048});
const a=device(), b=device();
const signText=(key,...fields)=>sign('sha256',Buffer.from(fields.join('\n')),key.privateKey).toString('base64');
const alias='cloud-test-fixture.trycloudflare.com';
function record(key,host=alias,ts=Math.floor(Date.now()/1000)) {
  const der=key.publicKey.export({type:'spki',format:'der'});
  return {host,ts,pub:der.toString('base64'),fingerprint:createHash('sha256').update(der).digest('hex').slice(0,16),
    signature:signText(key,'register',origin,host,String(ts))};
}
const live=(key)=>async (url,options)=>{
  assert.equal(options.redirect,'error');
  const u=new URL(url);
  assert.match(u.pathname,/^\/.well-known\/mac-remote-proof\/[a-f0-9]{48}$/);
  return Response.json({signature:signText(key,'proof',origin,u.host,u.pathname.split('/').pop())});
};
function call(path,data,fetcher,user='owner',source=origin){
  const headers={'oai-authenticated-user-id':user,'Origin':source,'Content-Type':'application/json'};
  return handle(new Request(origin+path,{method:data?'POST':'GET',headers,body:data?JSON.stringify(data):null}),env,fetcher);
}
const register='/api/register';
assert.equal((await call('/',null,live(a),'')).status,401);
assert.match(await (await call('/')).text(),/등록된 Mac이 없습니다/);
assert.equal((await call(register,record(a),live(a),'owner','https://other.example')).status,403);
assert.equal((await call(register,{...record(a),fingerprint:''},live(a))).status,428);
assert.equal((await call(register,record(a,'127.0.0.1'),live(a))).status,403);
assert.equal((await call(register,record(a,alias,1),live(a))).status,403);
assert.equal((await call(register,record(a),live(b))).status,409);
assert.equal((await call(register,record(a),live(a))).status,200);
assert.equal((await call(register,record(b),live(b))).status,403);
assert.equal((await call('/connect',null,live(b))).headers.get('location'),null);
assert.equal((await call('/connect',null,live(a))).headers.get('location'),'https://'+alias+'/');
const second='cloud-test-restarted.trycloudflare.com';
assert.equal((await call(register,record(a,second),live(a))).status,200);
assert.equal((await call('/connect',null,live(a))).headers.get('location'),'https://'+second+'/');
assert.equal((await call('/connect',null,live(a),'other')).headers.get('location'),null);
console.log('Directory registration, ownership, origin proof, rotation and isolation passed');
