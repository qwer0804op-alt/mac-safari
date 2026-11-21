// Registers a Python-signed device with the real site worker and follows the redirect.
// The worker verifies the device over a real TLS request to the Python server's proof endpoint.
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import https from 'node:https';
import {handle} from '../fixed-entry/site/worker/index.js';

const config = JSON.parse(readFileSync(process.argv[2], 'utf8'));
const origin = config.origin;
const bucket = new Map();
const env = {BUCKET: {
  async get(key) { const value = bucket.get(key); return value && {etag: 'test', json: async () => JSON.parse(value)}; },
  async put(key, value) { bucket.set(key, value); return {}; },
  async head() { return null; },
}};

// Reach the local test server as if it were the tunnel address.
const fetcher = (url) => new Promise((resolve, reject) => {
  const target = new URL(url);
  const request = https.request({
    host: '127.0.0.1', port: config.port, path: target.pathname, method: 'GET',
    headers: {Host: target.host, Accept: 'application/json'}, ca: config.ca, servername: '127.0.0.1',
  }, (response) => {
    const chunks = [];
    response.on('data', (chunk) => chunks.push(chunk));
    response.on('end', () => resolve(new Response(Buffer.concat(chunks), {status: response.statusCode, headers: response.headers})));
  });
  request.on('error', reject);
  request.end();
});

function call(path, data) {
  const headers = {'oai-authenticated-user-id': 'owner', Origin: origin, 'Content-Type': 'application/json'};
  return handle(new Request(origin + path, {method: data ? 'POST' : 'GET', headers, body: data ? JSON.stringify(data) : null}), env, fetcher);
}

const first = await call('/api/register', {...config.registration, fingerprint: ''});
assert.equal(first.status, 428, 'the first registration needs the device fingerprint');
const registered = await call('/api/register', {...config.registration, fingerprint: config.fingerprint});
assert.equal(registered.status, 200, await registered.text());
const connect = await call('/connect');
assert.equal(connect.status, 303);
assert.equal(connect.headers.get('location'), 'https://' + config.host + '/');
console.log('ok');
