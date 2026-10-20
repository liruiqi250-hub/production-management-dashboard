'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const web = path.join(__dirname, '..', 'web');
const engine = fs.readFileSync(path.join(web, 'drafts.js'), 'utf8');
const helpers = fs.readFileSync(path.join(web, 'draft-forms.js'), 'utf8');

// Minimal form DOM with browser target-phase ordering: capture handlers run before bubble handlers.
class Element {
  constructor(tag, attributes = {}, text = '') {
    this.tagName = tag.toUpperCase(); this.attributes = attributes; this.textContent = text;
    this.children = []; this.dataset = {}; this.parentElement = null;
    this.name = attributes.name || ''; this.value = attributes.value || '';
    this.disabled = Boolean(attributes.disabled); this.hidden = 'hidden' in attributes;
    this.listeners = []; this.focused = false;
    for (const [key, value] of Object.entries(attributes)) {
      if (key.startsWith('data-')) this.dataset[key.slice(5).replace(/-([a-z])/g, (_, letter) => letter.toUpperCase())] = value;
    }
  }
  append(...children) { for (const child of children) { child.parentElement = this; this.children.push(child); } }
  prepend(child) { child.parentElement = this; this.children.unshift(child); }
  addEventListener(type, listener, options = false) { this.listeners.push({ type, listener, capture: options === true || Boolean(options.capture) }); }
  focus() { this.focused = true; }
  querySelectorAll(selector) {
    const found = [];
    for (const child of this.children) {
      if (selector === 'input[name],select[name],textarea[name]' && ['INPUT', 'SELECT', 'TEXTAREA'].includes(child.tagName) && child.name) found.push(child);
      if (selector === 'form[data-draft-active="true"]' && child.tagName === 'FORM' && child.dataset.draftActive === 'true') found.push(child);
      if (selector === 'details' && child.tagName === 'DETAILS') found.push(child);
      if (selector === 'details[open]' && child.tagName === 'DETAILS' && child.open) found.push(child);
      found.push(...child.querySelectorAll(selector));
    }
    return found;
  }
  dispatch(type) {
    const event = { type, target: this, defaultPrevented: false, stopped: false,
      preventDefault() { this.defaultPrevented = true; }, stopImmediatePropagation() { this.stopped = true; },
    };
    for (const capture of [true, false]) for (const entry of this.listeners) {
      if (!event.stopped && entry.type === type && entry.capture === capture) entry.listener(event);
    }
    return event;
  }
}

function harness() {
  const stored = new Map();
  const window = { sessionStorage: { getItem: key => stored.get(key) ?? null, setItem: (key, value) => stored.set(key, value), removeItem: key => stored.delete(key) }, addEventListener() {} };
  const context = vm.createContext({ window, node: (tag, attributes, text) => new Element(tag, attributes, text), confirm: () => true, state: { user: { username: 'test' } } });
  vm.runInContext(engine + '\nconst FollowupDrafts=window.FollowupDrafts;\n' + helpers + '\nsetBusinessDraftUser("test");', context);
  const run = source => vm.runInContext(source, context);
  const form = (values, disabled = []) => {
    const element = new Element('form');
    const inputs = Object.fromEntries(Object.entries(values).map(([name, value]) => [name, new Element('input', { name, value, ...(disabled.includes(name) ? { disabled: true } : {}) })]));
    element.append(...Object.values(inputs));
    return { element, inputs };
  };
  const track = (element, key) => { context.currentForm = element; context.currentKey = key; return run('trackDraft(currentForm,currentKey)'); };
  const draft = (key, baseline) => { context.key = key; context.baseline = baseline; return run('businessDrafts.read(key,baseline)'); };
  const seed = (key, baseline, values) => { Object.assign(context, { key, baseline, values }); run('businessDrafts.capture(key,baseline,values)'); };
  const snapshot = element => { context.currentForm = element; return run('draftSnapshot(currentForm)'); };
  const ack = (element, submitted, reset = {}) => { Object.assign(context, { currentForm: element, submitted, reset }); run('acknowledgeDraft(currentForm,submitted,reset)'); };
  return { context, run, form, track, draft, seed, snapshot, ack };
}

function buttons(form) {
  const actions = form.children[0].children[1];
  return { acknowledge: actions.children[0], discard: actions.children[1], notice: form.children[0] };
}

test('服务器已有更新时，capture阶段冲突检查阻止先注册的生产保存handler', () => {
  const h = harness();
  h.seed('stage:01:刷胶', { state: '未开始', note: '' }, { state: '未开始', note: '本地未提交说明' });
  const { element, inputs } = h.form({ state: '进行中', note: '' });
  let submissions = 0;
  element.addEventListener('submit', () => submissions++); // productionSave registers before trackDraft.
  h.track(element, 'stage:01:刷胶');
  assert.equal(inputs.note.value, '本地未提交说明');
  const event = element.dispatch('submit');
  assert.equal(event.defaultPrevented, true);
  assert.equal(event.stopped, true);
  assert.equal(submissions, 0);
  assert.equal(buttons(element).acknowledge.focused, true);
  buttons(element).acknowledge.dispatch('click');
  element.dispatch('submit');
  assert.equal(submissions, 1);
});

test('放弃冲突草稿恢复最新服务端基线，而非草稿创建时的旧值', () => {
  const h = harness();
  h.seed('item:01', { status: '待处理', closure_note: '' }, { status: '待处理', closure_note: '旧草稿' });
  const { element, inputs } = h.form({ status: '处理中', closure_note: '' });
  h.track(element, 'item:01');
  buttons(element).discard.dispatch('click');
  assert.equal(inputs.status.value, '处理中');
  assert.equal(inputs.closure_note.value, '');
  assert.equal(buttons(element).notice.hidden, true);
  assert.equal(h.draft('item:01', { status: '处理中', closure_note: '' }), null);
});

test('取消放弃时保留字段和草稿', () => {
  const h = harness();
  const { element, inputs } = h.form({ progress_note: '' });
  h.track(element, 'after-sales:01');
  inputs.progress_note.value = '待确认的售后结果'; element.dispatch('input');
  h.context.confirm = () => false;
  buttons(element).discard.dispatch('click');
  assert.equal(inputs.progress_note.value, '待确认的售后结果');
  assert.equal(buttons(element).notice.hidden, false);
});

test('已授权字段才进入快照，登录密码、令牌和禁用跟进人均不采集', () => {
  const h = harness();
  const { element, inputs } = h.form({ note: '', password: 'password-value', new_password: 'new-password', token: 'token-value', owner: '其他人' }, ['owner']);
  h.track(element, 'stage:01:刷胶');
  inputs.note.value = '业务说明'; element.dispatch('input');
  const draft = h.draft('stage:01:刷胶', { note: '' });
  assert.deepEqual(Object.keys(draft.values), ['note']);
  assert.equal(draft.values.note, '业务说明');
  buttons(element).discard.dispatch('click');
  assert.equal(inputs.password.value, 'password-value');
  assert.equal(inputs.new_password.value, 'new-password');
  assert.equal(inputs.token.value, 'token-value');
  assert.equal(inputs.owner.value, '其他人');
});

test('登录、改密和令牌表单没有绑定草稿入口', () => {
  const app = fs.readFileSync(path.join(web, 'app.js'), 'utf8');
  assert.doesNotMatch(app, /trackDraft\([^\n;]*(?:password-form|login-form|token-form|user-form)/);
});

test('恢复草稿自动展开工序及所属单件、生产单', () => {
  const h = harness();
  h.seed('stage:01:刷胶', { note: '' }, { note: '未提交' });
  const { element } = h.form({ note: '' }); h.track(element, 'stage:01:刷胶');
  const container = new Element('section'), order = new Element('details'), piece = new Element('details'), editor = new Element('details');
  container.append(order); order.append(piece); piece.append(editor); editor.append(element);
  h.context.container = container; h.run('openDraftEditors(container)');
  assert.equal(order.open, true); assert.equal(piece.open, true); assert.equal(editor.open, true);
});

test('当前无冲突时仅暂存，未触发业务提交', () => {
  const h = harness();
  const { element, inputs } = h.form({ status: '待处理', closure_note: '' });
  let submissions = 0; element.addEventListener('submit', () => submissions++);
  h.track(element, 'item:01');
  inputs.closure_note.value = '进行中的说明'; element.dispatch('input');
  assert.equal(submissions, 0);
  assert.equal(buttons(element).notice.hidden, false);
  const event = element.dispatch('submit');
  assert.equal(event.stopped, false); assert.equal(submissions, 1);
});

test('保存成功后即使刷新失败，也清除已提交说明并重建当前基线', () => {
  const h = harness();
  const { element, inputs } = h.form({ status: '待处理', closure_note: '' });
  h.track(element, 'item:01');
  inputs.status.value = '处理中'; inputs.closure_note.value = '已提交的说明'; element.dispatch('input');
  const submitted = h.snapshot(element);
  h.ack(element, submitted, { closure_note: '' });
  assert.equal(inputs.closure_note.value, '');
  assert.equal(h.draft('item:01', { status: '处理中', closure_note: '' }), null);
  inputs.closure_note.value = '保存后的新草稿'; element.dispatch('input');
  buttons(element).discard.dispatch('click');
  assert.equal(inputs.status.value, '处理中');
  assert.equal(inputs.closure_note.value, '');
});

test('请求进行中续写的说明在成功确认后仍保留为未提交草稿', () => {
  const h = harness();
  const { element, inputs } = h.form({ status: '待处理', closure_note: '' });
  h.track(element, 'item:01');
  inputs.closure_note.value = '第一次提交'; element.dispatch('input');
  const submitted = h.snapshot(element);
  inputs.closure_note.value = '提交中继续补充'; element.dispatch('input');
  h.ack(element, submitted, { closure_note: '' });
  assert.equal(inputs.closure_note.value, '提交中继续补充');
  const remaining = h.draft('item:01', { status: '待处理', closure_note: '' });
  assert.equal(remaining.values.closure_note, '提交中继续补充');
  assert.equal(remaining.conflict, false);
});

test('换账号后旧表单的迟到确认不能清除新账号的同键草稿', () => {
  const h = harness();
  const old = h.form({ note: '' }); h.track(old.element, 'stage:01:刷胶');
  old.inputs.note.value = '旧账号提交'; old.element.dispatch('input');
  const submitted = h.snapshot(old.element);
  h.run('clearBusinessDraftUser();setBusinessDraftUser("new-user")');
  const next = h.form({ note: '' }); h.track(next.element, 'stage:01:刷胶');
  next.inputs.note.value = '新账号自己的草稿'; next.element.dispatch('input');
  h.ack(old.element, submitted);
  assert.equal(h.draft('stage:01:刷胶', { note: '' }).values.note, '新账号自己的草稿');
  h.context.currentForm = old.element;
  assert.equal(h.run('draftRequestCurrent(currentForm)'), false);
  const event = old.element.dispatch('submit');
  assert.equal(event.stopped, true); assert.equal(event.defaultPrevented, true);
});

test('退出后同一账号重新登录也拒绝旧会话表单的迟到确认', () => {
  const h = harness();
  const old = h.form({ note: '' }); h.track(old.element, 'stage:01:刷胶');
  old.inputs.note.value = '旧会话'; old.element.dispatch('input');
  const submitted = h.snapshot(old.element);
  h.run('clearBusinessDraftUser();setBusinessDraftUser("test")');
  const next = h.form({ note: '' }); h.track(next.element, 'stage:01:刷胶');
  next.inputs.note.value = '新会话'; next.element.dispatch('input');
  h.ack(old.element, submitted);
  assert.equal(h.draft('stage:01:刷胶', { note: '' }).values.note, '新会话');
});
