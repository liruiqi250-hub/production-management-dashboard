'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, '..', 'web', 'production-documents.js'), 'utf8');
const begin = source.indexOf('async function downloadProductionDocument(');
const end = source.indexOf('function documentFieldList(', begin);
assert.ok(begin >= 0 && end > begin, '必须测试实际发布的下载函数');
const functionSource = source.slice(begin, end);
const deferred = () => {
  let resolve, reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
};
const flush = () => new Promise(resolve => setImmediate(resolve));
const MIME = {
  xlsx: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
  pdf: 'application/pdf',
};
function binary(format, content) {
  const bytes = content === undefined
    ? (format === 'pdf' ? Buffer.from('%PDF-1.7\n合成前端下载数据\n%%EOF') : Buffer.from([80, 75, 3, 4, 20, 0, 0, 0, 8, 0, 23, 42]))
    : content;
  return new Blob([bytes], { type: MIME[format] });
}
function response(format, blob, options = {}) {
  const headers = new Map(Object.entries({ 'content-type': MIME[format], ...options.headers }).map(([key, value]) => [key.toLowerCase(), value]));
  return {
    status: options.status || 200,
    ok: (options.status || 200) >= 200 && (options.status || 200) < 300,
    headers: { get: name => headers.get(name.toLowerCase()) ?? null },
    blob: options.blob || (async () => blob),
    json: options.json || (async () => options.error || {}),
  };
}

function harness() {
  const requests = [], events = [], urls = [], anchors = [], logins = [], loads = [];
  const timers = new Map(), elements = new Map();
  const state = { csrf: 'session-one', user: { username: 'first' } };
  const feedback = { textContent: '', className: '' };
  let timerSequence = 0;
  const context = vm.createContext({
    state, feedback, Blob, Uint8Array, AbortController, encodeURIComponent, decodeURIComponent,
    fetch: (url, options) => {
      const pending = deferred();
      requests.push({ url, options, ...pending });
      options.signal.addEventListener('abort', () => {
        const error = new Error('synthetic aborted request');
        error.name = 'AbortError';
        pending.reject(error);
      }, { once: true });
      return pending.promise;
    },
    setTimeout: (callback, delay) => {
      const id = ++timerSequence;
      timers.set(id, { callback, delay, canceled: false, fired: false });
      return id;
    },
    clearTimeout: id => { if (timers.has(id)) timers.get(id).canceled = true; },
    URL: {
      createObjectURL: blob => { const value = 'blob:synthetic/' + (urls.length + 1); urls.push({ value, blob }); events.push('url-created'); return value; },
      revokeObjectURL: url => { events.push('url-revoked:' + url); },
    },
    node: (tag, attributes) => {
      assert.equal(tag, 'a');
      const anchor = { ...attributes,
        click: () => { events.push('clicked:' + anchor.download); },
        remove: () => { events.push('anchor-removed'); },
      };
      anchors.push(anchor);
      return anchor;
    },
    document: { body: { append: anchor => { assert.ok(anchors.includes(anchor)); events.push('anchor-appended'); } } },
    showLogin: () => { logins.push(state.user?.username); state.csrf = null; state.user = null; },
    $: id => { if (!elements.has(id)) elements.set(id, { textContent: '' }); return elements.get(id); },
    load: async () => { loads.push(true); },
  });
  vm.runInContext(functionSource, context);
  return { state, feedback, requests, events, urls, anchors, logins, loads, elements, timers,
    start(format = 'xlsx', metadata = { id: '生成批次/一', filename: '俄罗斯生产单_产品质检表_V006.xlsx' }) {
      context.metadata = metadata; context.format = format;
      return vm.runInContext('downloadProductionDocument(metadata,format,feedback)', context);
    },
    switchSession() { state.csrf = 'session-two'; state.user = { username: 'second' }; feedback.textContent = '新账号当前状态'; },
    fire(id) { const timer = timers.get(id); assert.ok(timer && !timer.canceled && !timer.fired); timer.fired = true; timer.callback(); },
  };
}

for (const format of ['xlsx', 'pdf']) test(`${format} 必须收完整个正文后才创建并保存 Blob，携带同源会话`, async () => {
  const h = harness(), body = deferred(), file = binary(format), pending = h.start(format);
  assert.equal(h.requests.length, 1);
  assert.equal(h.requests[0].url, '/api/production/document-files/' + encodeURIComponent('生成批次/一') + '?format=' + format);
  assert.equal(h.requests[0].options.credentials, 'same-origin');
  assert.ok(h.requests[0].options.signal instanceof AbortSignal);
  assert.match(h.feedback.textContent, /正在接收/);
  h.requests[0].resolve(response(format, file, { blob: () => body.promise, headers: { 'content-length': String(file.size) } }));
  await flush();
  assert.equal(h.urls.length, 0, '正文尚未完整接收时不能触发浏览器保存');
  assert.equal(h.anchors.length, 0);
  body.resolve(file); await pending;
  assert.equal(h.urls.length, 1);
  assert.equal(h.urls[0].blob, file);
  assert.match(h.anchors[0].download, new RegExp('俄罗斯生产单_产品质检表_V006\\.' + format + '$'));
  assert.ok(h.events.some(event => event.startsWith('clicked:')));
  assert.match(h.feedback.textContent, /文件已接收完整.*交给浏览器保存/);
  assert.doesNotMatch(h.feedback.textContent, /已保存到|下载完成/);
});

test('优先采用服务端 UTF-8 中文文件名，并过滤文件路径字符', async () => {
  const h = harness(), pending = h.start('pdf'), wanted = '客户/俄罗斯:产品质检表.pdf';
  h.requests[0].resolve(response('pdf', binary('pdf'), {
    headers: { 'Content-Disposition': 'attachment; filename="fallback.pdf"; filename*=UTF-8\'\'' + encodeURIComponent(wanted) },
  }));
  await pending;
  assert.equal(h.anchors[0].download, '客户_俄罗斯_产品质检表.pdf');
});

test('损坏的扩展文件名编码回退到中文批次名，不丢失请求扩展名', async () => {
  const h = harness(), pending = h.start('pdf');
  h.requests[0].resolve(response('pdf', binary('pdf'), { headers: { 'Content-Disposition': "attachment; filename*=UTF-8''bad%ZZ" } }));
  await pending;
  assert.equal(h.anchors[0].download, '俄罗斯生产单_产品质检表_V006.pdf');
});

const invalidFiles = [
  { name: '错误 MIME', format: 'xlsx', blob: binary('xlsx'), headers: { 'Content-Type': 'text/html' }, message: /文件格式不正确/ },
  { name: '缺少 MIME', format: 'pdf', blob: binary('pdf'), headers: { 'Content-Type': '' }, message: /文件格式不正确/ },
  { name: '空正文', format: 'pdf', blob: binary('pdf', Buffer.alloc(0)), headers: {}, message: /文件为空/ },
  { name: '声明长度不完整', format: 'xlsx', blob: binary('xlsx'), headers: { 'Content-Length': '99999' }, message: /未接收完整/ },
  { name: 'Excel 签名异常', format: 'xlsx', blob: binary('xlsx', Buffer.from('<html>error</html>')), headers: {}, message: /文件内容异常/ },
  { name: 'PDF 签名异常', format: 'pdf', blob: binary('pdf', Buffer.from('PK\x03\x04wrong-kind')), headers: {}, message: /文件内容异常/ },
];
for (const item of invalidFiles) test(`${item.name} 显示中文错误并禁止保存`, async () => {
  const h = harness(), pending = h.start(item.format);
  h.requests[0].resolve(response(item.format, item.blob, { headers: item.headers }));
  await pending;
  assert.match(h.feedback.textContent, item.message);
  assert.match(h.feedback.className, /error/);
  assert.equal(h.urls.length, 0);
  assert.equal(h.anchors.length, 0);
});

test('MIME 参数及大小写允许，压缩传输不将解压后 Blob 大小与压缩长度误比', async () => {
  const h = harness(), pending = h.start('pdf');
  h.requests[0].resolve(response('pdf', binary('pdf'), { headers: {
    'Content-Type': 'Application/PDF; charset=binary', 'Content-Encoding': 'gzip', 'Content-Length': '1',
  } }));
  await pending;
  assert.equal(h.urls.length, 1);
});

test('当前会话 401 返回登录并说明需要重新下载，不保存错误正文', async () => {
  const h = harness(), pending = h.start();
  h.requests[0].resolve(response('xlsx', null, { status: 401 }));
  await pending;
  assert.deepEqual(h.logins, ['first']);
  assert.equal(h.state.user, null);
  assert.match(h.elements.get('login-error').textContent, /登录已过期.*重新登录.*下载/);
  assert.equal(h.urls.length, 0);
});

for (const status of [200, 401]) test(`旧会话迟到的 ${status} 响应不保存也不能退出新账号`, async () => {
  const h = harness(), pending = h.start();
  h.switchSession();
  h.requests[0].resolve(response('xlsx', binary('xlsx'), { status }));
  await pending;
  assert.equal(h.urls.length, 0);
  assert.equal(h.anchors.length, 0);
  assert.equal(h.logins.length, 0);
  assert.equal(h.state.user.username, 'second');
  assert.equal(h.feedback.textContent, '新账号当前状态');
});

test('接收 Blob 期间切换账号，旧文件不得进入新会话保存流程', async () => {
  const h = harness(), body = deferred(), pending = h.start();
  h.requests[0].resolve(response('xlsx', null, { blob: () => body.promise }));
  await flush(); h.switchSession(); body.resolve(binary('xlsx'));
  await pending;
  assert.equal(h.urls.length, 0);
  assert.equal(h.anchors.length, 0);
  assert.equal(h.feedback.textContent, '新账号当前状态');
});

test('读取签名字节期间切换账号，也不创建下载链接', async () => {
  const h = harness(), signature = deferred(), pending = h.start('pdf');
  const file = { size: 20, slice: () => ({ arrayBuffer: () => signature.promise }) };
  h.requests[0].resolve(response('pdf', file));
  await flush(); h.switchSession(); signature.resolve(Uint8Array.from([37, 80, 68, 70, 45]).buffer);
  await pending;
  assert.equal(h.urls.length, 0);
  assert.equal(h.feedback.textContent, '新账号当前状态');
});

test('服务端拒绝时保留中文原因，不把错误 JSON 保存成 Excel', async () => {
  const h = harness(), pending = h.start();
  h.requests[0].resolve(response('xlsx', null, { status: 404, error: { error: '生成资料不存在，请重新生成。' } }));
  await pending;
  assert.match(h.feedback.textContent, /生成资料不存在/);
  assert.equal(h.urls.length, 0);
});

test('初始密码未修改时刷新账号状态并给出中文处理说明', async () => {
  const h = harness(), pending = h.start();
  h.requests[0].resolve(response('xlsx', null, { status: 403, error: { code: 'password_change_required' } }));
  await pending;
  assert.equal(h.loads.length, 1);
  assert.match(h.elements.get('first-password-error').textContent, /请先修改初始密码.*下载/);
  assert.equal(h.urls.length, 0);
});

test('网络断开显示中文重试提示，不暴露英文 fetch 错误', async () => {
  const h = harness(), pending = h.start();
  h.requests[0].reject(new TypeError('Failed to fetch'));
  await pending;
  assert.match(h.feedback.textContent, /下载连接中断.*内网连接.*重试/);
  assert.doesNotMatch(h.feedback.textContent, /Failed to fetch/);
  assert.equal(h.urls.length, 0);
});

test('响应头已返回但正文传输中断，仍禁止保存并显示中文网络错误', async () => {
  const h = harness(), body = deferred(), pending = h.start();
  h.requests[0].resolve(response('xlsx', null, { blob: () => body.promise }));
  await flush(); body.reject(new TypeError('body stream interrupted'));
  await pending;
  assert.match(h.feedback.textContent, /下载连接中断.*重试/);
  assert.equal(h.urls.length, 0);
});

test('请求超时会中止请求并显示中文反馈', async () => {
  const h = harness(), pending = h.start();
  const requestTimer = [...h.timers.keys()][0];
  h.fire(requestTimer);
  await pending;
  assert.equal(h.requests[0].options.signal.aborted, true);
  assert.match(h.feedback.textContent, /接收文件超时.*重试/);
  assert.equal(h.urls.length, 0);
});

test('正文尚未接收完整时超时中止，不能保存已经收到的部分数据', async () => {
  const h = harness(), body = deferred(), pending = h.start();
  h.requests[0].options.signal.addEventListener('abort', () => {
    const error = new Error('body aborted'); error.name = 'AbortError'; body.reject(error);
  }, { once: true });
  h.requests[0].resolve(response('xlsx', null, { blob: () => body.promise }));
  await flush(); h.fire([...h.timers.keys()][0]); await pending;
  assert.equal(h.requests[0].options.signal.aborted, true);
  assert.match(h.feedback.textContent, /接收文件超时.*重试/);
  assert.equal(h.urls.length, 0);
});

test('旧会话的网络失败不会覆盖新账号状态', async () => {
  const h = harness(), pending = h.start();
  h.switchSession(); h.requests[0].reject(new TypeError('Failed to fetch'));
  await pending;
  assert.equal(h.feedback.textContent, '新账号当前状态');
  assert.equal(h.urls.length, 0);
});

test('对象 URL 保持到点击保存之后，延迟清理且取消请求超时器', async () => {
  const h = harness(), pending = h.start();
  h.requests[0].resolve(response('xlsx', binary('xlsx'))); await pending;
  const click = h.events.findIndex(event => event.startsWith('clicked:'));
  assert.ok(h.events.indexOf('url-created') < click);
  assert.ok(h.events.indexOf('anchor-appended') < click);
  assert.ok(h.events.indexOf('anchor-removed') > click);
  assert.ok(!h.events.some(event => event.startsWith('url-revoked:')), '不能在 click 后立即撤销 URL');
  const requestTimer = h.timers.get(1);
  assert.equal(requestTimer.canceled, true);
  const cleanup = [...h.timers.entries()].find(([, timer]) => !timer.canceled && !timer.fired);
  assert.ok(cleanup && cleanup[1].delay >= 1000, '保存动作应有充分时间读取 Blob');
  h.fire(cleanup[0]);
  assert.equal(h.events.at(-1), 'url-revoked:' + h.urls[0].value);
  assert.ok(h.events.findIndex(event => event.startsWith('url-revoked:')) > click);
});
