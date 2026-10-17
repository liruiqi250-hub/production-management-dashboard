import test from 'node:test';
import assert from 'node:assert/strict';
import worker from '../automation/cloudflare-gateway/worker.mjs';
const OriginalRequest=globalThis.Request;
globalThis.Request=class extends OriginalRequest{constructor(input,options={}){super(input,{duplex:'half',...options});}};
const origin='https://dashboard.example.workers.dev';
test('fixed service forwards methods, cookie and exact origin; replaces untrusted gateway headers',async()=>{
 let seen;
 const env={PUBLIC_ORIGIN:origin,GATEWAY_SECRET:'example-shared-gateway-secret',DASHBOARD:{fetch:async request=>{seen=request;return new Response('ok',{headers:{'Set-Cookie':'example=value; Secure; HttpOnly'}});}}};
 const response=await worker.fetch(new Request(origin+'/api/login?x=1',{method:'POST',headers:{Host:'example.workers.dev',Origin:origin,Cookie:'example=value','X-Dino-Gateway-Key':'forged','X-Forwarded-Proto':'http'},body:'example-json'}),env);
 assert.equal(response.status,200);assert.equal(seen.url,'http://127.0.0.1:8769/api/login?x=1');
 assert.equal(seen.headers.get('X-Dino-Gateway-Key'),env.GATEWAY_SECRET);
 assert.equal(seen.headers.get('Host'),null);
 assert.equal(seen.headers.get('Origin'),origin);assert.equal(seen.headers.get('Cookie'),'example=value');
 assert.equal(seen.headers.get('X-Forwarded-Proto'),null);assert.equal(await seen.text(),'example-json');
 assert.equal(response.headers.get('Cache-Control'),'no-store');assert.match(response.headers.get('Set-Cookie'),/Secure/);
});
test('other host or missing gateway secret never reaches private service',async()=>{
 let calls=0;const env={PUBLIC_ORIGIN:origin,GATEWAY_SECRET:'example-secret',DASHBOARD:{fetch:()=>{calls++;}}};
 assert.equal((await worker.fetch(new Request('https://other.example/'),env)).status,421);
 assert.equal((await worker.fetch(new Request(origin),{...env,GATEWAY_SECRET:''})).status,421);assert.equal(calls,0);
});
test('origin outage returns bounded recovery message without secrets',async()=>{
 const env={PUBLIC_ORIGIN:origin,GATEWAY_SECRET:'example-secret',DASHBOARD:{fetch:()=>{throw new Error('example-private-path-or-token');}}};
 const response=await worker.fetch(new Request(origin),env);assert.equal(response.status,503);
 assert.doesNotMatch(await response.text(),/example-private-path-or-token/);
});
