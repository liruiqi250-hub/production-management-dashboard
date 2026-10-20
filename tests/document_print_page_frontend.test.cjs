'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, '..', 'web', 'document-print.js'), 'utf8');
const flush = () => new Promise(resolve => setImmediate(resolve));
function deferred() {
  let resolve, reject;
  const result = { settled: false };
  result.promise = new Promise((yes, no) => {
    resolve = value => { result.settled = true; yes(value); };
    reject = error => { result.settled = true; no(error); };
  });
  return Object.assign(result, { resolve, reject });
}

function harness(options = {}) {
  const elements = new Map(), events = [], timers = new Map(), windowEvents = new Map(), printCalls = [];
  let timerId = 0, printError = null;
  function element(id) {
    if (!elements.has(id)) {
      const handlers = new Map(), classes = new Set();
      elements.set(id, { disabled: id === 'print-document', hidden: id === 'retry-pages', textContent: '',
        classList: { add: value => classes.add(value), remove: value => classes.delete(value), contains: value => classes.has(value) },
        addEventListener: (name, handler) => handlers.set(name, handler),
        emit: name => handlers.get(name)?.(),
      });
    }
    return elements.get(id);
  }
  const images = Array.from({ length: options.count ?? 3 }, (_, index) => {
    const image = { dataset: { printSrc: '/api/production/document-files/synthetic/pages/' + (index + 1) + '.png?quality=print' },
      complete: Boolean(options.cached), naturalWidth: options.cached ? 100 : 0, onload: null, onerror: null,
      requested: [], decodeCalls: [],
    };
    Object.defineProperty(image, 'src', { get: () => image.requested.at(-1), set: value => {
      image.complete = false; image.naturalWidth = 0; image.requested.push(value); events.push('load:' + index);
    } });
    if (options.decode !== false) image.decode = () => {
      events.push('decode:' + index);
      if (options.decodeThrows) throw new Error('Synthetic synchronous browser EncodingError');
      const pending = deferred(); image.decodeCalls.push(pending); return pending.promise;
    };
    return image;
  });
  const context = vm.createContext({
    document: { getElementById: element, querySelectorAll: selector => {
      assert.equal(selector, '[data-print-src]'); return images;
    } },
    window: {
      print: () => { printCalls.push(true); if (printError) throw printError; },
      addEventListener: (name, handler) => windowEvents.set(name, handler),
    },
    setTimeout: (callback, delay) => { const id = ++timerId; timers.set(id, { callback, delay, canceled: false, fired: false }); return id; },
    clearTimeout: id => { if (timers.has(id)) timers.get(id).canceled = true; },
  });
  vm.runInContext(source, context);
  const result = { images, events, timers, printCalls,
    button: element('print-document'), retryButton: element('retry-pages'), status: element('print-status'),
    load(index) {
      const image = images[index]; assert.equal(typeof image.onload, 'function', '必须由真实发布代码注册图片load');
      image.complete = true; image.naturalWidth = 100; image.onload();
    },
    fail(index) {
      const image = images[index]; assert.equal(typeof image.onerror, 'function');
      image.complete = true; image.naturalWidth = 0; image.onerror(new Error('Synthetic image failed'));
    },
    clickPrint() { element('print-document').emit('click'); },
    retry() { element('retry-pages').emit('click'); },
    afterPrint() { windowEvents.get('afterprint')?.(); },
    setPrintError(error) { printError = error; },
    activeTimers() { return [...timers.entries()].filter(([, timer]) => !timer.canceled && !timer.fired); },
    fire(id) { const timer = timers.get(id); assert.ok(timer && !timer.canceled && !timer.fired); timer.fired = true; timer.callback(); },
    state() { return vm.runInContext('({preparing,ready})', context); },
    prepare() { return vm.runInContext('preparePrintPages()', context); },
    async finish(index) {
      if (typeof images[index].onload === 'function') { result.load(index); await flush(); }
      const pending = images[index].decodeCalls.at(-1);
      if (pending && !pending.settled) pending.resolve();
      await flush();
    },
  };
  return result;
}

test('所有页面按顺序加载，每一页decode完成后才开始下一页，最后才启用打印', async () => {
  const h = harness();
  assert.deepEqual(h.events, ['load:0']);
  assert.equal(h.button.disabled, true);
  assert.equal(h.printCalls.length, 0);
  h.clickPrint(); assert.equal(h.printCalls.length, 0);
  for (let page = 0; page < 3; page++) {
    h.load(page); await flush();
    assert.equal(h.images[page].decodeCalls.length, 1);
    assert.equal(h.button.disabled, true, 'load完成尚未decode时仍不能打印');
    if (page < 2) assert.equal(h.images[page + 1].requested.length, 0);
    h.images[page].decodeCalls[0].resolve(); await flush();
    assert.equal(h.button.disabled, page !== 2);
  }
  assert.deepEqual(h.events, ['load:0', 'decode:0', 'load:1', 'decode:1', 'load:2', 'decode:2']);
  assert.match(h.status.textContent, /全部 3 页已就绪/);
  assert.equal(h.button.textContent, '打印 / 云打印');
  assert.equal(h.printCalls.length, 0, '准备页面不应自动打开打印对话框');
  assert.equal(h.activeTimers().length, 0);
  h.clickPrint();
  assert.equal(h.printCalls.length, 1);
  assert.match(h.status.textContent, /系统打印对话框.*打印机.*份数/);
});

test('缓存已完整载入的图片仍须decode，避免decode未完成就打印', async () => {
  const h = harness({ count: 1, cached: true }); await flush();
  assert.equal(h.images[0].requested.length, 0);
  assert.equal(h.images[0].decodeCalls.length, 1);
  assert.equal(h.button.disabled, true);
  h.images[0].decodeCalls[0].resolve(); await flush();
  assert.equal(h.button.disabled, false);
  assert.equal(h.printCalls.length, 0);
});

test('正在准备期间重复调用不创建重复图片请求或并行准备', async () => {
  const h = harness({ count: 1 });
  await h.prepare(); await h.prepare();
  assert.equal(h.images[0].requested.length, 1);
  assert.equal(h.state().preparing, true);
  await h.finish(0);
  assert.equal(h.state().preparing, false);
});

test('图片载入失败保持打印禁用，显示中文及重试，重试成功后才可打印', async () => {
  const h = harness({ count: 1 });
  h.fail(0); await flush();
  assert.equal(h.button.disabled, true);
  assert.equal(h.retryButton.hidden, false);
  assert.equal(h.status.classList.contains('error'), true);
  assert.match(h.status.textContent, /未能载入.*清理.*登录/);
  h.clickPrint(); assert.equal(h.printCalls.length, 0);
  assert.equal(h.images[0].onload, null);
  assert.equal(h.images[0].onerror, null);
  h.retry();
  assert.equal(h.images[0].requested.length, 2);
  assert.equal(h.retryButton.hidden, true);
  assert.equal(h.status.classList.contains('error'), false);
  await h.finish(0);
  assert.equal(h.button.disabled, false);
  h.clickPrint(); assert.equal(h.printCalls.length, 1);
});

test('后续页失败不会带着缺页打开打印，其后页也不继续请求', async () => {
  const h = harness();
  await h.finish(0);
  h.fail(1); await flush();
  assert.equal(h.images[2].requested.length, 0);
  assert.equal(h.button.disabled, true);
  assert.equal(h.state().ready, false);
  h.clickPrint(); assert.equal(h.printCalls.length, 0);
});

test('图片网络加载超时提供中文重试，清理事件监听而不打印', async () => {
  const h = harness({ count: 1 });
  const [[id, timer]] = h.activeTimers();
  assert.equal(timer.delay, 60000);
  h.fire(id); await flush();
  assert.match(h.status.textContent, /页面加载超时.*重新加载/);
  assert.equal(h.retryButton.hidden, false);
  assert.equal(h.button.disabled, true);
  assert.equal(h.images[0].onload, null);
  assert.equal(h.images[0].onerror, null);
  assert.equal(h.printCalls.length, 0);
});

test('decode也有独立60秒超时，不能永久卡在正在准备', async () => {
  const h = harness({ count: 1 });
  h.load(0); await flush();
  const active = h.activeTimers();
  assert.equal(active.length, 1, '进入decode后必须保留独立超时控制');
  assert.equal(active[0][1].delay, 60000);
  h.fire(active[0][0]); await flush();
  assert.match(h.status.textContent, /图片解析超时.*重新加载/);
  assert.equal(h.retryButton.hidden, false);
  assert.equal(h.button.disabled, true);
  assert.equal(h.state().preparing, false);
  h.clickPrint(); assert.equal(h.printCalls.length, 0);
});

test('decode的浏览器英文拒绝转换为中文失败，不暴露底层错误', async () => {
  const h = harness({ count: 1 });
  h.load(0); await flush();
  const error = new Error('Synthetic browser EncodingError'); error.name = 'EncodingError';
  h.images[0].decodeCalls[0].reject(error); await flush();
  assert.match(h.status.textContent, /页面图片解析失败.*重新加载/);
  assert.doesNotMatch(h.status.textContent, /Synthetic|EncodingError/);
  assert.equal(h.retryButton.hidden, false);
  assert.equal(h.button.disabled, true);
  assert.equal(h.activeTimers().length, 0);
});

test('decode同步抛异常也有中文提示和可重试入口', async () => {
  const h = harness({ count: 1, decodeThrows: true });
  h.load(0); await flush();
  assert.match(h.status.textContent, /页面图片解析失败.*重新加载/);
  assert.equal(h.button.disabled, true);
  assert.equal(h.retryButton.hidden, false);
  assert.equal(h.state().preparing, false);
});

test('已超时的旧decode迟到完成不能提前启用正在重试的打印页', async () => {
  const h = harness({ count: 1 });
  h.load(0); await flush();
  const oldDecode = h.images[0].decodeCalls[0];
  h.fire(h.activeTimers()[0][0]); await flush();
  h.retry(); await flush();
  assert.equal(h.images[0].decodeCalls.length, 2);
  oldDecode.resolve(); await flush();
  assert.equal(h.button.disabled, true);
  assert.equal(h.state().ready, false);
  h.images[0].decodeCalls[1].resolve(); await flush();
  assert.equal(h.button.disabled, false);
  assert.equal(h.state().ready, true);
});

test('缺少decode的浏览器在所有load成功后仍可进入打印', async () => {
  const h = harness({ count: 2, decode: false });
  await h.finish(0);
  assert.equal(h.button.disabled, true);
  await h.finish(1);
  assert.equal(h.button.disabled, false);
  assert.deepEqual(h.events, ['load:0', 'load:1']);
});

test('afterprint只说明对话框结束，不冒称已出纸或再次发起打印', async () => {
  const h = harness({ count: 1 }); await h.finish(0);
  h.clickPrint(); h.afterPrint();
  assert.equal(h.printCalls.length, 1);
  assert.match(h.status.textContent, /打印对话框已结束/);
  assert.match(h.status.textContent, /是否已出纸.*实际结果为准/);
  assert.doesNotMatch(h.status.textContent, /打印成功|已打印完成|全部已出纸/);
});

test('window.print抛错给出友好中文替代方式，可再次尝试且不声称成功', async () => {
  const h = harness({ count: 1 }); await h.finish(0);
  h.setPrintError(new Error('Synthetic blocked print dialog'));
  assert.doesNotThrow(() => h.clickPrint());
  assert.match(h.status.textContent, /当前浏览器无法打开系统打印.*Edge.*Chrome/);
  assert.doesNotMatch(h.status.textContent, /Synthetic|打印成功|已出纸/);
  assert.equal(h.printCalls.length, 1);
  h.setPrintError(null); h.clickPrint();
  assert.equal(h.printCalls.length, 2);
  assert.match(h.status.textContent, /系统打印对话框/);
});
