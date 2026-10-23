'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

// 直接执行页面发布的鉴权函数及启动分支，避免复制实现掩盖回归。
const appSource = fs.readFileSync(path.join(__dirname, '..', 'web', 'app.js'), 'utf8');
const authStart = appSource.indexOf('let dingtalkInfo=');
const bootStart = appSource.indexOf('if(/DingTalk/i.test(navigator.userAgent))', authStart);
assert.ok(authStart >= 0 && bootStart > authStart, '必须找到页面实际钉钉鉴权与启动分支');
const authSource = appSource.slice(authStart, bootStart);
const bootSource = appSource.slice(bootStart);

function harness(options = {}) {
  const calls = [], navigations = [], timers = new Map(), elements = new Map();
  let timerId = 0, loadCount = 0, showLoginCount = 0, nativeCount = 0;
  const info = { enabled: true, login_url: '/auth/dingtalk/start', ...options.info };
  const submit = { textContent: '登录看板', disabled: false };
  const element = id => {
    if (!elements.has(id)) elements.set(id, {
      hidden: false, checked: false, textContent: '', disabled: false,
      listeners: {}, addEventListener(type, listener) { this.listeners[type] = listener; },
      querySelector(selector) { assert.equal(selector, 'button[type=submit]'); return submit; },
    });
    return elements.get(id);
  };
  const sdk = options.sdk === 'missing' ? {} : {
    requestAuthCode: async corpId => {
      nativeCount++;
      assert.equal(corpId, 'fixture-corp');
      if (options.sdk === 'reject') throw options.nativeError || { errorCode: '4040' };
      if (options.sdk === 'timeout') return new Promise(() => {});
      if (options.sdk === 'empty') return {};
      return { code: 'fixture-auth-result' };
    },
  };
  const location = {
    origin: 'https://dashboard.example.test', pathname: '/', search: options.search || '', hash: '',
    assign: url => navigations.push(url),
  };
  const context = vm.createContext({
    $, URL, URLSearchParams, AbortController, navigator: { userAgent: options.browser ? 'Mozilla/5.0' : 'Mozilla/5.0 DingTalk/8.0.0' },
    location, history: { replaceState(_state, _title, url) { location.search = new URL(url, location.origin).search; } },
    window: { DinoDingTalk: options.loadScript ? undefined : sdk },
    document: {
      createElement(tag) { assert.equal(tag, 'script'); return {}; },
      head: { append(script) {
        calls.push({ path: script.src, kind: 'script' });
        if (options.script === 'timeout') return;
        if (options.script === 'reject') return script.onerror();
        context.window.DinoDingTalk = sdk;
        script.onload();
      } },
    },
    setTimeout(callback, delay) { const id = ++timerId; timers.set(id, { callback, delay }); return id; },
    clearTimeout(id) { timers.delete(id); },
    async fetch(url, settings = {}) {
      calls.push({ path: url, settings });
      if (url === '/api/auth/dingtalk') return { ok: true, json: async () => ({ ...info }) };
      if (url === '/api/auth/dingtalk/workbench/start') {
        if (options.start === 'timeout') return new Promise((_resolve, reject) => {
          settings.signal.addEventListener('abort', () => { const error = new Error('aborted'); error.name = 'AbortError'; reject(error); });
        });
        return { ok: true, json: async () => ({ corpId: 'fixture-corp', nonce: 'fixture-nonce' }) };
      }
      if (url === '/api/auth/dingtalk/workbench/login') return {
        ok: !options.loginRejected,
        json: async () => options.loginRejected ? { error: '该钉钉身份没有看板权限。' } : { binding_pending: options.nativeBinding === true },
      };
      throw new Error('Unexpected test fetch: ' + url);
    },
    showLogin() {
      showLoginCount++;
      element('login-screen').hidden = false;
      element('application').hidden = true;
    },
    async load() { loadCount++; },
    showError() {}, api() { throw new Error('此流程不应使用账号绑定或业务 API'); },
  });
  function $(id) { return element(id); }
  vm.runInContext(authSource + '\nglobalThis.authTest={loadDingTalk,workbenchLogin,dingtalkDiagnosticCode};', context);
  return {
    calls, navigations, timers, element, submit, context,
    get loadCount() { return loadCount; }, get showLoginCount() { return showLoginCount; }, get nativeCount() { return nativeCount; },
    async flush() { for (let count = 0; count < 24; count++) await Promise.resolve(); },
    async ready() { await context.authTest.loadDingTalk(); },
    run() { return context.authTest.workbenchLogin(); },
    async boot() { await vm.runInContext(bootSource, context); },
    async fire(delay) {
      await this.flush();
      const pending = [...timers.entries()].find(([, value]) => value.delay === delay);
      assert.ok(pending, '必须注册预期超时定时器 ' + delay);
      timers.delete(pending[0]); pending[1].callback(); await this.flush();
    },
  };
}

function assertFallback(value) {
  assert.equal(value.element('login-screen').hidden, false);
  assert.equal(value.element('application').hidden, true);
  assert.equal(value.element('dingtalk-auto-screen').hidden, true);
  assert.equal(value.element('dingtalk-binding').hidden, true);
  assert.equal(value.element('dingtalk-bind-confirm').checked, false);
  assert.equal(value.submit.textContent, '登录看板');
  assert.equal(value.element('dingtalk-login').textContent, '改用钉钉授权登录');
  assert.match(value.element('login-error').textContent, /改用钉钉授权登录/);
  assert.match(value.element('login-error').textContent, /原账号登录/);
  assert.doesNotMatch(value.element('login-error').textContent, /重新打开|重新从|工作台重新/);
  assert.equal(value.loadCount, 0, '免登失败不能沿用旧会话读取业务数据');
  assert.equal(value.calls.filter(call => call.path === '/api/auth/dingtalk/workbench/login').length, 0);
}

test('正常原生免登仍先确认当前钉钉身份，再加载业务', async () => {
  const value = harness(); await value.ready(); assert.equal(await value.run(), true);
  assert.equal(value.nativeCount, 1); assert.equal(value.loadCount, 1);
  const request = value.calls.find(call => call.path === '/api/auth/dingtalk/workbench/login');
  assert.deepEqual(JSON.parse(request.settings.body), { nonce: 'fixture-nonce', code: 'fixture-auth-result' });
  assert.equal(request.settings.credentials, 'same-origin');
  assert.equal(value.element('dingtalk-auto-screen').hidden, true);
});

test('普通浏览器不调用原生 SDK，按既有流程读取登录状态', async () => {
  const value = harness({ browser: true }); await value.boot();
  assert.equal(value.nativeCount, 0); assert.equal(value.loadCount, 1);
  assert.equal(value.calls.filter(call => call.path.includes('/workbench/')).length, 0);
});

test('服务端确认 OAuth 会话后启动只加载现有认证，不重新创建原生挑战', async () => {
  const value = harness({ info: { oauth_handoff: 'session' }, sdk: 'reject' }); await value.boot();
  assert.equal(value.nativeCount, 0); assert.equal(value.loadCount, 1); assert.equal(value.showLoginCount, 0);
  assert.equal(value.calls.filter(call => call.path.includes('/workbench/')).length, 0);
});

test('服务端确认 OAuth 待绑定后停在本人账号确认表单，不自动绑定或加载业务', async () => {
  const value = harness({ info: { oauth_handoff: 'binding', binding_pending: true }, sdk: 'reject' }); await value.boot();
  assert.equal(value.nativeCount, 0); assert.equal(value.loadCount, 0); assert.equal(value.showLoginCount, 1);
  assert.equal(value.element('dingtalk-binding').hidden, false);
  assert.equal(value.element('dingtalk-bind-confirm').checked, false);
  assert.equal(value.element('dingtalk-auto-screen').hidden, true);
  assert.equal(value.submit.textContent, '仅用账号登录');
  assert.equal(value.element('login-error').textContent, '', '授权成功的绑定提示不能显示为红色错误');
  assert.equal(value.calls.filter(call => call.path.includes('/workbench/')).length, 0);
});

test('伪造地址栏 OAuth 参数不能跳过当前钉钉身份确认', async () => {
  const value = harness({ search: '?dingtalk=success&oauth_handoff=session&binding_pending=true' }); await value.boot();
  assert.equal(value.nativeCount, 1); assert.equal(value.loadCount, 1);
  assert.equal(value.calls.filter(call => call.path === '/api/auth/dingtalk/workbench/start').length, 1);
});

test('无可信 handoff 时已有待绑定标记仍先确认端内当前人身份', async () => {
  const value = harness({ info: { binding_pending: true } }); await value.ready(); await value.run();
  assert.equal(value.nativeCount, 1); assert.equal(value.loadCount, 1);
});

test('原生免登首次待绑定时仍由本人账号勾选确认，不自动加载业务', async () => {
  const value = harness({ info: { binding_pending: true }, nativeBinding: true }); await value.ready(); await value.run();
  assert.equal(value.nativeCount, 1); assert.equal(value.loadCount, 0); assert.equal(value.showLoginCount, 1);
  assert.equal(value.element('dingtalk-binding').hidden, false);
  assert.equal(value.element('dingtalk-bind-confirm').checked, false);
  assert.equal(value.submit.textContent, '仅用账号登录');
});

test('原生 SDK 拒绝后显示受控诊断与授权重试，原生详细信息不进入界面', async () => {
  const value = harness({ sdk: 'reject', nativeError: { errorCode: '4040', errorMessage: 'private-native-detail authCode=fixture-private-detail' } });
  await value.ready(); assert.equal(await value.run(), true); assertFallback(value);
  assert.match(value.element('login-error').textContent, /诊断码 4040/);
  assert.doesNotMatch(value.element('login-error').textContent, /private-native-detail|authCode|fixture-private-detail/);
  assert.equal(value.timers.size, 0);
  value.element('dingtalk-login').listeners.click();
  assert.deepEqual(value.navigations, ['https://dashboard.example.test/auth/dingtalk/start']);
});

test('原生 SDK 没有 requestAuthCode 方法时仍可改用钉钉授权', async () => {
  const value = harness({ sdk: 'missing' }); await value.ready(); assert.equal(await value.run(), true); assertFallback(value);
  assert.match(value.element('login-error').textContent, /暂不支持自动免登/);
});

test('真实 SDK ERROR 空格数字格式仅显示诊断码，不回显组件原文', async () => {
  const value = harness({ sdk: 'reject', nativeError: new Error('[DINGTALK-JSAPI] ERROR 4040: Do not support this API on the current platform; fixture-private-detail') });
  await value.ready(); assert.equal(await value.run(), true); assertFallback(value);
  assert.match(value.element('login-error').textContent, /诊断码 4040/);
  assert.doesNotMatch(value.element('login-error').textContent, /DINGTALK-JSAPI|Do not support|platform|fixture-private-detail/);
});

test('身份识别超时后可改用授权且不沿用旧会话', async () => {
  const value = harness({ sdk: 'timeout' }); await value.ready(); const pending = value.run(); await value.fire(10000);
  assert.equal(await pending, true); assertFallback(value); assert.match(value.element('login-error').textContent, /超时/);
});

test('创建原生挑战超时后显示同一可恢复入口', async () => {
  const value = harness({ start: 'timeout' }); await value.ready(); const pending = value.run(); await value.fire(8000);
  assert.equal(await pending, true); assertFallback(value); assert.match(value.element('login-error').textContent, /超时/);
});

for (const script of ['timeout', 'reject']) test('原生组件加载' + script + '可恢复为授权登录', async () => {
  const value = harness({ loadScript: true, script }); await value.ready(); const pending = value.run();
  if (script === 'timeout') await value.fire(8000);
  assert.equal(await pending, true); assertFallback(value);
});

test('组件从当前 V0.3.32 缓存地址加载后仍能完成正常免登', async () => {
  const value = harness({ loadScript: true }); await value.ready(); assert.equal(await value.run(), true);
  assert.equal(value.calls.find(call => call.kind === 'script').path, '/vendor/dingtalk-workbench.js?v=0.3.32');
  assert.equal(value.loadCount, 1);
});

test('原生身份返回缺少授权码时不发送后端认证请求', async () => {
  const value = harness({ sdk: 'empty' }); await value.ready(); assert.equal(await value.run(), true); assertFallback(value);
});

test('再次读取钉钉入口时恢复原按钮文字', async () => {
  const value = harness({ sdk: 'reject' }); await value.ready(); await value.run();
  assert.equal(value.element('dingtalk-login').textContent, '改用钉钉授权登录');
  await value.ready(); assert.equal(value.element('dingtalk-login').textContent, '使用钉钉登录');
});

for (const [error, expected] of [
  [{ errorCode: 4040 }, '4040'], [{ error: '19' }, '19'], ['ERROR4040: native detail', '4040'],
  [{ message: 'client rejected (ERROR4040) native detail' }, '4040'], [{ errorMessage: 'ERROR4040 native detail' }, '4040'],
  [{ error: 'ERROR4040 native detail' }, '4040'], [{ errorCode: '1234567' }, ''], [{ error: 'code=4040' }, ''],
  [{ message: '[DINGTALK-JSAPI] ERROR 4040: Do not support this API on the current platform' }, '4040'],
  [{ errorMessage: 'ERROR: 4040 private native detail' }, '4040'], [{ message: 'ERROR = 4040 private native detail' }, '4040'],
  [{ errorCode: '19 native-detail' }, ''], [{ errorCode: -1 }, ''], [{ message: 'ERROR1234567' }, ''],
  [{ message: 'ERROR4040native-detail' }, ''], [{ message: 'authCode=fixture-private-detail' }, ''],
]) test('诊断字段仅允许短数字：' + JSON.stringify(error), () => {
  const value = harness(); assert.equal(value.context.authTest.dingtalkDiagnosticCode(error), expected);
});
