'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const web = path.join(__dirname, '..', 'web');
const appSource = fs.readFileSync(path.join(web, 'app.js'), 'utf8');
const productionSource = fs.readFileSync(path.join(web, 'production.js'), 'utf8');
function section(source, start, end) {
  const first = source.indexOf(start), last = source.indexOf(end, first);
  assert.ok(first >= 0 && last > first, '必须找到实际发布函数：' + start);
  return source.slice(first, last);
}
const apiSource = section(appSource, 'async function api(', 'function clearPrivateView(');
const loadSource = section(appSource, 'let loadSequence=', 'function initFilters(');
const productionHandlers = section(productionSource, 'async function reloadProduction(', 'function stageEditor(');
const deferred = () => { let resolve, reject; const promise = new Promise((yes, no) => { resolve = yes; reject = no; }); return { promise, resolve, reject }; };
const response = (body, status = 200) => ({ ok: status >= 200 && status < 300, status, json: async () => body });
const flush = () => new Promise(resolve => setImmediate(resolve));

function apiHarness() {
  const calls = [], login = [], state = { csrf: 'session-one', user: { username: 'first' } };
  const context = vm.createContext({ state,
    fetch: (url, options) => { const pending = deferred(); calls.push({ url, options, ...pending }); return pending.promise; },
    showLogin: () => { login.push(state.user?.username); state.csrf = null; state.user = null; },
  });
  vm.runInContext(apiSource, context);
  const call = (url, options) => { context.url = url; context.options = options; return vm.runInContext('api(url,options)', context); };
  return { calls, login, state, call };
}

test('正常 GET 返回数据并使用同源 cookie，不添加写操作 CSRF 头', async () => {
  const h = apiHarness(), pending = h.call('/api/dashboard');
  assert.equal(h.calls[0].url, '/api/dashboard');
  assert.equal(h.calls[0].options.credentials, 'same-origin');
  assert.equal(h.calls[0].options.headers['X-CSRF-Token'], undefined);
  h.calls[0].resolve(response({ items: ['test'] }));
  assert.deepEqual(await pending, { items: ['test'] });
  assert.equal(h.login.length, 0);
});

test('正常 PATCH 序列化正文，保留调用者请求头并添加当前 CSRF', async () => {
  const h = apiHarness();
  const pending = h.call('/api/items/test', { method: 'PATCH', body: { closure_note: '虚构说明' }, headers: { 'X-Test': 'fixture' } });
  const request = h.calls[0].options;
  assert.equal(request.method, 'PATCH');
  assert.equal(request.headers['X-Test'], 'fixture');
  assert.equal(request.headers['Content-Type'], 'application/json');
  assert.equal(request.headers['X-CSRF-Token'], 'session-one');
  assert.deepEqual(JSON.parse(request.body), { closure_note: '虚构说明' });
  h.calls[0].resolve(response({ ok: true }));
  assert.deepEqual(await pending, { ok: true });
});

test('当前会话的 401 返回登录页并保留服务端错误', async () => {
  const h = apiHarness(), pending = h.call('/api/dashboard');
  h.calls[0].resolve(response({ error: '登录已过期' }, 401));
  await assert.rejects(pending, /登录已过期/);
  assert.deepEqual(h.login, ['first']);
  assert.equal(h.state.user, null);
});

test('账号密码错误的登录请求不会额外清空当前界面', async () => {
  const h = apiHarness(), pending = h.call('/api/login', { method: 'POST', body: { username: 'test' } });
  h.calls[0].resolve(response({ error: '账号或密码错误' }, 401));
  await assert.rejects(pending, /账号或密码错误/);
  assert.equal(h.login.length, 0);
});

for (const status of [200, 401]) test(`旧会话迟到的 ${status} 响应被拒绝，不能清除新账号`, async () => {
  const h = apiHarness(), pending = h.call('/api/dashboard');
  h.state.csrf = 'session-two'; h.state.user = { username: 'second' };
  h.calls[0].resolve(response(status === 200 ? { private: 'old-data' } : { error: '旧会话失效' }, status));
  await assert.rejects(pending, /登录状态已变化/);
  assert.equal(h.state.user.username, 'second');
  assert.equal(h.state.csrf, 'session-two');
  assert.equal(h.login.length, 0);
});

test('解析 JSON 期间切换会话也拒绝旧响应', async () => {
  const h = apiHarness(), pending = h.call('/api/production'), json = deferred();
  h.calls[0].resolve({ ok: true, status: 200, json: () => json.promise });
  await flush();
  h.state.csrf = 'session-two'; h.state.user = { username: 'second' };
  json.resolve({ orders: ['old'] });
  await assert.rejects(pending, /登录状态已变化/);
  assert.equal(h.login.length, 0);
});

function productionHarness() {
  const calls = [], renders = [], acknowledgements = [], errors = [];
  const state = { production: { orders: ['current'] } };
  const context = vm.createContext({ state, current: true,
    api: (url, options) => { const pending = deferred(); calls.push({ url, options, ...pending }); return pending.promise; },
    draftRequestCurrent: () => context.current,
    pruneBusinessDrafts: () => {}, renderProduction: openId => renders.push(openId),
    draftSnapshot: () => ({ state: '待发货', note: '虚构交付说明', source_ref: 'test-ref' }),
    acknowledgeDraft: (...values) => acknowledgements.push(values), showError: message => errors.push(message),
  });
  vm.runInContext(productionHandlers, context);
  return { context, calls, renders, acknowledgements, errors, state, run: code => vm.runInContext(code, context) };
}

test('生产刷新请求迟到时不会覆盖当前账号的生产状态', async () => {
  const h = productionHarness(); h.context.form = {};
  const pending = h.run('reloadProduction("piece-one",form)');
  h.context.current = false; h.calls[0].resolve({ orders: ['old-session'] });
  await pending;
  assert.deepEqual(h.state.production, { orders: ['current'] });
  assert.equal(h.renders.length, 0);
});

test('生产刷新在当前会话成功更新并直达指定单件', async () => {
  const h = productionHarness(); h.context.form = {};
  const pending = h.run('reloadProduction("piece-one",form)');
  h.calls[0].resolve({ orders: ['fresh'] }); await pending;
  assert.deepEqual(h.state.production, { orders: ['fresh'] });
  assert.deepEqual(h.renders, ['piece-one']);
});

function bindProductionSave(h, endpoint) {
  let handler;
  h.context.form = { addEventListener: (_type, callback) => { handler = callback; } };
  h.context.button = { disabled: false }; h.context.feedback = { textContent: '', className: '' }; h.context.endpoint = endpoint;
  h.run('productionSave(form,button,feedback,endpoint,()=>({state:"待发货",note:"虚构交付说明",source_ref:"test-ref"}),"piece-one")');
  return () => handler({ preventDefault() {} });
}

test('旧会话的生产保存返回后不确认草稿也不发起刷新', async () => {
  const h = productionHarness(), submit = bindProductionSave(h, '/api/production/pieces/test/stage');
  const pending = submit();
  h.context.current = false; h.calls[0].resolve({ ok: true }); await pending;
  assert.equal(h.acknowledgements.length, 0);
  assert.equal(h.calls.length, 1);
  assert.equal(h.context.button.disabled, false);
});

test('当前交付保存确认一次性说明，随后刷新指定单件', async () => {
  const h = productionHarness(), submit = bindProductionSave(h, '/api/production/pieces/test/delivery');
  const pending = submit();
  h.calls[0].resolve({ ok: true }); await flush();
  assert.equal(h.acknowledgements.length, 1);
  assert.deepEqual(JSON.parse(JSON.stringify(h.acknowledgements[0][2])), { note: '', source_ref: '' });
  assert.equal(h.calls[1].url, '/api/production');
  h.calls[1].resolve({ orders: ['updated'] }); await pending;
  assert.deepEqual(h.renders, ['piece-one']);
  assert.deepEqual(h.errors, ['']);
});

function loadHarness() {
  const calls = [], users = [], renders = [];
  const state = { user: { username: 'original' }, csrf: 'same-session', snapshot: { marker: 'original' }, production: { marker: 'original' } };
  const elements = new Map();
  const element = id => {
    if (!elements.has(id)) elements.set(id, { hidden: false, textContent: '', append() {}, querySelector: () => element(id + '-select') });
    return elements.get(id);
  };
  const context = vm.createContext({ state,
    api: url => { const pending = deferred(); calls.push({ url, ...pending }); return pending.promise; },
    setBusinessDraftUser: user => users.push(user), $: element,
    clear: value => value, node: () => ({}), showPasswordRequired() {}, refreshJob() {}, initFilters() {},
    initLoginView: () => null, render: () => renders.push(state.user.username), showError() {}, downloadNotificationReport() {},
  });
  vm.runInContext(loadSource, context);
  return { calls, users, renders, state, load: () => vm.runInContext('load()', context) };
}
const me = username => ({ user: { username, display_name: username, role: 'viewer' }, csrf: 'same-session', lan_url: 'http://test.invalid' });

test('同会话并发加载时，较早的 me 返回不能覆盖后发加载', async () => {
  const h = loadHarness(), first = h.load(), second = h.load();
  h.calls[1].resolve(me('latest')); await flush();
  h.calls[2].resolve({ office_staff: [], marker: 'latest' }); await flush();
  h.calls[3].resolve({ marker: 'latest' }); await second;
  h.calls[0].resolve(me('old')); await first;
  assert.equal(h.state.user.username, 'latest');
  assert.equal(h.state.snapshot.marker, 'latest');
  assert.equal(h.state.production.marker, 'latest');
  assert.deepEqual(h.users, ['latest']); assert.deepEqual(h.renders, ['latest']);
});

test('同会话旧 dashboard 迟到时不能写入新加载的状态', async () => {
  const h = loadHarness(), first = h.load();
  h.calls[0].resolve(me('old')); await flush();
  const second = h.load(); h.calls[2].resolve(me('latest')); await flush();
  h.calls[3].resolve({ office_staff: [], marker: 'latest' }); await flush();
  h.calls[4].resolve({ marker: 'latest' }); await second;
  h.calls[1].resolve({ office_staff: [], marker: 'old' }); await first;
  assert.equal(h.state.user.username, 'latest');
  assert.equal(h.state.snapshot.marker, 'latest');
  assert.equal(h.state.production.marker, 'latest');
  assert.equal(h.calls.length, 5, '旧加载不应在迟到 dashboard 后继续请求生产数据');
  assert.deepEqual(h.renders, ['latest']);
});
