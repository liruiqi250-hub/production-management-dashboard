'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '..', 'web', 'production-documents.js'), 'utf8');
const deferred = () => { let resolve, reject; const promise = new Promise((yes, no) => { resolve = yes; reject = no; }); return { promise, resolve, reject }; };
const flush = () => new Promise(resolve => setImmediate(resolve));

class Element {
  constructor(tag, attributes = {}, text = '') {
    this.tag = tag; this.attrs = {}; this.children = []; this.handlers = {}; this.parent = null;
    this.textContent = text; this.hidden = false; this.disabled = false; this.value = ''; this.className = '';
    for (const [name, value] of Object.entries(attributes)) this.setAttribute(name, value);
  }
  setAttribute(name, value) { this.attrs[name] = String(value); if (name === 'hidden') this.hidden = true; if (name === 'class') this.className = String(value); if (name === 'value') this.value = String(value); this[name] = name === 'hidden' ? true : value; }
  getAttribute(name) { return this.attrs[name] ?? null; }
  removeAttribute(name) { delete this.attrs[name]; if (name === 'href') delete this.href; }
  append(...children) { for (const child of children) { child.parent = this; this.children.push(child); } }
  replaceChildren(...children) { for (const child of this.children) child.parent = null; this.children = []; this.append(...children); }
  addEventListener(type, callback) { (this.handlers[type] ||= []).push(callback); }
  async trigger(type) { if (type === 'click' && this.disabled) return; const event = { defaultPrevented: false, preventDefault() { this.defaultPrevented = true; } }; await Promise.all((this.handlers[type] || []).map(callback => callback(event))); if (type === 'click' && this.tag === 'a' && this.href && !event.defaultPrevented) this.navigate?.(); }
  get isConnected() { return this.tag === 'body' || Boolean(this.parent?.isConnected); }
  closest(selector) { if (selector !== '[hidden]') return null; for (let element = this; element; element = element.parent) if (element.hidden) return element; return null; }
  remove() { if (this.parent) this.parent.children = this.parent.children.filter(child => child !== this); this.parent = null; }
}
function descendants(element) { return [element, ...element.children.flatMap(descendants)]; }
function harness(pages = 43) {
  const calls = [], changes = [], links = [], timers = new Map(), windowEvents = {}, documentEvents = {};
  let sequence = 0, uuid = 0, task = null, active = true;
  const state = { csrf: 'session-one', user: { username: 'reader', role: 'viewer', permissions: { can_write: false } } };
  const body = new Element('body');
  const document = { body, visibilityState: 'visible', addEventListener: (name, callback) => { (documentEvents[name] ||= []).push(callback); } };
  const context = vm.createContext({
    state, document, Uint8Array, AbortController, Blob,
    crypto: { randomUUID: () => '00000000-0000-4000-8000-' + String(++uuid).padStart(12, '0') },
    api: (url, options) => { const pending = deferred(); calls.push({ url, options, ...pending }); return pending.promise; },
    setTimeout: (callback, delay) => { const id = ++sequence; timers.set(id, { callback, delay, canceled: false }); return id; },
    clearTimeout: id => { if (timers.has(id)) timers.get(id).canceled = true; },
    window: { addEventListener: (name, callback) => { (windowEvents[name] ||= []).push(callback); } },
    node: (tag, attributes, text) => { const element = new Element(tag, attributes, text); if (tag === 'a') element.navigate = () => links.push({ href: element.href, target: element.target, rel: element.rel }); return element; },
    textDate: value => value || '', $: () => new Element('p'), showLogin() {},
  });
  vm.runInContext(source, context);
  context.metadata = { id: 'output-one', order_id: 'order-one', kind: 'product', filename: '合成产品质检.xlsx', status: 'ready', pages, pdf_ready: true, preview_ready: true };
  context.options = { isActive: () => active, onTask: value => { task = value; }, onChange: value => changes.push(value) };
  const client = vm.runInContext('documentPrintJobClient(metadata,options)', context);
  return { context, state, calls, changes, links, timers, document, body, client,
    get task() { return task; },
    parse: (value, total = pages) => { context.value = value; context.total = total; return JSON.parse(JSON.stringify(vm.runInContext('parseDocumentPrintPages(value,total)', context))); },
    inactive: () => { active = false; },
    switchSession: () => { state.csrf = 'session-two'; state.user = { username: 'new-reader', role: 'viewer' }; },
    fireTimer() { const found = [...timers.entries()].find(([, timer]) => !timer.canceled); assert.ok(found, '需要有待运行的轮询计时器'); found[1].canceled = true; found[1].callback(); },
    event(name) { for (const handler of windowEvents[name] || []) handler(); },
    visibility(value) { document.visibilityState = value; for (const handler of documentEvents.visibilitychange || []) handler(); },
    ui() { const result = vm.runInContext('productionDocumentPrinting(metadata,"order-one",()=>6)', context); body.append(result.browserButton, result.hostButton, result.element); return result; },
  };
}
const job = (state, extra = {}) => ({ id: 'job-one', state, printer_name: '柯尼卡固定队列', pages: [1, 2], copies: 1, total_sheets: 2, ...extra });
const byLabel = (element, label) => descendants(element).find(item => item.attrs['aria-label'] === label);
const byText = (element, text) => descendants(element).find(item => item.textContent === text);
const byClass = (element, name) => descendants(element).find(item => item.className.split(' ').includes(name));

test('页码严格校验、去重排序，并生成紧凑标准页码', () => {
  const h = harness();
  assert.deepEqual(h.parse('5, 1-3,2，7、6'), { pages: [1, 2, 3, 5, 6, 7], canonical: '1-3,5-7', count: 6 });
  assert.deepEqual(h.parse('43'), { pages: [43], canonical: '43', count: 1 });
});
for (const value of ['', '0', '-1', '1,', ',1', '1,,2', '3-1', '1.5', '1--2', '1-44', '44', '1e2', '1;2', '1-']) test('拒绝非法或越界页码：' + JSON.stringify(value), () => {
  assert.throws(() => harness().parse(value), /页码|范围/);
});

test('不因初始化或普通查看自动提交打印任务', () => {
  const h = harness(); h.ui();
  assert.equal(h.calls.length, 0);
  assert.equal(h.task, null);
});

test('内网 HTTP 没有 randomUUID 时仍用浏览器安全随机数建立UUID', () => {
  const h = harness(); h.context.crypto = { getRandomValues: bytes => { bytes.fill(0); bytes[15] = 1; return bytes; } };
  const requestId = vm.runInContext('documentPrintRequestId()', h.context);
  assert.match(requestId, /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/);
});

test('viewer 可以打开浏览器打印设置；最后一步打开同源打印页，份数留给系统选择', async () => {
  const h = harness(), ui = h.ui();
  await ui.browserButton.trigger('click');
  assert.equal(ui.element.hidden, false);
  assert.equal(byLabel(ui.element, '本机打印份数').parent.hidden, true);
  assert.match(byClass(ui.element, 'document-print-hint').textContent, /份数在系统对话框中选择/);
  const range = byLabel(ui.element, '打印范围'); range.value = 'range'; await range.trigger('change');
  const input = byLabel(ui.element, '指定打印页码'); input.value = '6,1-3,2'; await input.trigger('input');
  await byText(ui.element, '打开打印页面').trigger('click');
  assert.equal(h.calls.length, 0);
  const link = byText(ui.element, '打开打印页面');
  assert.equal(link.tag, 'a');assert.equal(link.href, '/api/production/document-files/output-one/print?pages=1-3%2C6');
  assert.deepEqual(h.links, [{ href: link.href, target: undefined, rel: undefined }]);
  assert.equal(link.getAttribute('target'), null, '真实链接默认同页导航，不依赖新窗口权限');
  assert.match(byClass(ui.element, 'document-print-status').textContent, /打印页面核对.*打印 \/ 云打印/);
  assert.doesNotMatch(byClass(ui.element, 'document-print-status').textContent, /已打开/);
});

test('浏览器当前页来自当前预览，超过80页必须分批', async () => {
  const h = harness(100), ui = h.ui(); await ui.browserButton.trigger('click');
  assert.equal(byText(ui.element, '打开打印页面').getAttribute('aria-disabled'), 'true');
  assert.equal(byText(ui.element, '打开打印页面').getAttribute('href'), null);
  await byText(ui.element, '打开打印页面').trigger('click');assert.equal(h.links.length, 0);
  assert.match(byClass(ui.element, 'document-print-summary').textContent, /最多 80 页/);
  const range = byLabel(ui.element, '打印范围'); range.value = 'current'; await range.trigger('change');
  await byText(ui.element, '打开打印页面').trigger('click');
  assert.equal(h.links[0].href, '/api/production/document-files/output-one/print?pages=6');
});

test('主机设置先读取固定目标；可用前不提交，正文不传任意队列或路径', async () => {
  const h = harness(), ui = h.ui(), opened = ui.hostButton.trigger('click');
  assert.equal(h.calls.length, 1); assert.equal(h.calls[0].url, '/api/production/printing');
  assert.equal(byText(ui.element, '提交至本机打印队列').disabled, true);
  h.calls[0].resolve({ enabled: true, available: true, printer_name: '柯尼卡固定队列' }); await opened;
  assert.match(byClass(ui.element, 'document-print-target').textContent, /柯尼卡固定队列/);
  assert.equal(h.calls.length, 1, '读取可用状态不能自动开始打印');
  const pending = byText(ui.element, '提交至本机打印队列').trigger('click');
  assert.equal(h.calls[1].url, '/api/production/print-jobs');
  assert.deepEqual(Object.keys(h.calls[1].options.body).sort(), ['copies', 'output_id', 'pages', 'request_id']);
  assert.equal(h.calls[1].options.body.pages, '1-43');
  h.calls[1].resolve(job('submitted')); await pending;
  assert.match(byClass(ui.element, 'document-print-status').textContent, /已交给 Windows 打印队列/);
  assert.doesNotMatch(byClass(ui.element, 'document-print-status').textContent, /已出纸|打印完成/);
});

test('固定队列不可用时显示原因并阻止提交', async () => {
  const h = harness(), ui = h.ui(), pending = ui.hostButton.trigger('click');
  h.calls[0].resolve({ enabled: true, available: false, printer_name: '柯尼卡固定队列', reason: '固定队列离线' }); await pending;
  assert.equal(byText(ui.element, '提交至本机打印队列').disabled, true);
  assert.match(byClass(ui.element, 'document-print-status').textContent, /固定队列离线/);
  assert.equal(h.calls.length, 1);
});

test('主机份数、总纸面限制在首次提交前检查', async () => {
  const h = harness(100);
  for (const copies of [0, 6, 1.5]) await assert.rejects(h.client.submit('1-2', copies), /1 至 5/);
  await assert.rejects(h.client.submit('1-100', 3), /300.*250/);
  assert.equal(h.calls.length, 0);
  const pending = h.client.submit('1-50', 5); assert.equal(h.calls[0].options.body.copies, 5);
  h.calls[0].resolve(job('submitted')); await pending;
});

test('双击只发一次；网络失败重试沿用相同UUID及原页码份数', async () => {
  const h = harness(), first = h.client.submit('2,1-2', 2);
  await h.client.submit('1-2', 2); assert.equal(h.calls.length, 1);
  const request = { ...h.calls[0].options.body };
  h.calls[0].reject(new TypeError('Failed to fetch')); await first;
  assert.match(h.changes.at(-1).message, /重试同一任务.*避免重复/);
  const retry = h.client.submit('1-2', 2);
  assert.deepEqual(JSON.parse(JSON.stringify(h.calls[1].options.body)), request);
  h.calls[1].resolve(job('submitted')); await retry;
  assert.match(h.changes.at(-1).message, /已交给 Windows 打印队列/);
});

test('同次任务不能悄悄改页码；显式下一任务才使用新UUID', async () => {
  const h = harness(), first = h.client.submit('1-2', 1);
  h.calls[0].resolve(job('submitted')); await first;
  await assert.rejects(h.client.submit('3', 1), /新建下一任务/);
  const originalId = h.calls[0].options.body.request_id;
  h.client.next(); assert.equal(h.calls.length, 1);
  const next = h.client.submit('3', 1);
  assert.notEqual(h.calls[1].options.body.request_id, originalId);
  h.calls[1].resolve(job('submitted')); await next;
});

for (const finalState of ['submitted', 'failed', 'unknown']) test('轮询到 ' + finalState + ' 停止，不另发打印任务', async () => {
  const h = harness(), first = h.client.submit('1-2', 1);
  h.calls[0].resolve(job('queued')); await first;
  h.fireTimer(); assert.equal(h.calls[1].url, '/api/production/print-jobs/job-one');
  assert.equal(h.calls[1].options, undefined);
  h.calls[1].resolve(job(finalState, { message: '合成结果' })); await flush();
  assert.equal([...h.timers.values()].filter(timer => !timer.canceled).length, 0);
  assert.equal(h.calls.filter(call => call.options?.method === 'POST').length, 1);
  if (finalState === 'unknown') assert.match(h.changes.at(-1).message, /结果暂不明确.*避免重复/);
});

test('查询失败后再次操作仅查询已有job，不重发POST', async () => {
  const h = harness(), first = h.client.submit('1-2', 1);
  h.calls[0].resolve(job('queued')); await first;
  const refresh = h.client.refresh(); h.calls[1].reject(new TypeError('offline')); await refresh;
  const retry = h.client.submit('1-2', 1);
  assert.equal(h.calls[2].url, '/api/production/print-jobs/job-one');
  h.calls[2].resolve(job('submitted')); await retry;
  assert.equal(h.calls.filter(call => call.options?.method === 'POST').length, 1);
});

test('切换会话或离开页面后停止轮询，迟到回执不更新当前状态', async () => {
  for (const action of ['switchSession', 'inactive']) {
    const h = harness(), first = h.client.submit('1-2', 1);
    h.calls[0].resolve(job('queued')); await first;
    const count = h.changes.length; h[action](); h.fireTimer();
    assert.equal(h.calls.length, 1); assert.equal(h.changes.length, count);
  }
  const h = harness(), first = h.client.submit('1', 1); h.switchSession();
  h.calls[0].resolve(job('submitted')); await first;
  assert.ok(!h.changes.some(change => change.task?.job?.state === 'submitted'));
});

test('收起设置、隐藏页面及pagehide不会继续后台查询', async () => {
  const h = harness(), ui = h.ui(), opened = ui.hostButton.trigger('click');
  h.calls[0].resolve({ enabled: true, available: true, printer_name: '柯尼卡' }); await opened;
  const first = byText(ui.element, '提交至本机打印队列').trigger('click');
  h.calls[1].resolve(job('queued')); await first;
  await byText(ui.element, '收起打印设置').trigger('click');
  assert.equal([...h.timers.values()].filter(timer => !timer.canceled).length, 0);
  h.visibility('hidden'); h.event('pagehide');
  assert.equal(h.calls.length, 2);
});

test('最新history已清理时移除陈旧预览并明确保留原业务资料', async () => {
  const h = harness();
  h.state.productionDocumentViews = { 'order-one': { preview: h.context.metadata, page: 1 } };
  const panel = vm.runInContext('productionDocumentPanel({id:"order-one"})', h.context); h.body.append(panel);
  assert.equal(h.calls[0].url, '/api/production/orders/order-one/documents');
  h.calls[0].resolve({ latest: null, documents: [], history: [{ id: 'output-one', status: 'cleaned' }] }); await flush();
  assert.equal(h.state.productionDocumentViews['order-one'].preview, null);
  assert.match(byClass(panel, 'document-cleaned-note').textContent, /临时资料已清理.*重新生成.*原单、模板和生产记录保留/);
  assert.match(byClass(panel, 'document-retention-note').textContent, /7 天未使用.*下载副本由自己管理/);
});

test('410 下载响应明确要求重新生成，并标记陈旧资料已清理', async () => {
  const h = harness();
  h.context.feedback = { textContent: '', className: '' };
  h.context.fetch = async () => ({ status: 410, ok: false, json: async () => ({ error: '临时文件已移除' }) });
  await vm.runInContext('downloadProductionDocument(metadata,"xlsx",feedback)', h.context);
  assert.equal(h.context.metadata.status, 'cleaned');
  assert.match(h.context.feedback.textContent, /临时资料已清理.*重新生成后下载/);
  assert.equal(h.links.length, 0);
});
