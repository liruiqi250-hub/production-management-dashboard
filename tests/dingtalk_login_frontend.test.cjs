'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

// Execute the shipped handler itself; do not duplicate its URL validation rules.
const appSource = fs.readFileSync(path.join(__dirname, '..', 'web', 'app.js'), 'utf8');
const handlerStart = appSource.indexOf('function startDingTalk(');
const handlerEnd = appSource.indexOf("$('dingtalk-login').addEventListener", handlerStart);
assert.ok(handlerStart >= 0 && handlerEnd > handlerStart, '必须找到实际钉钉登录处理函数及按钮绑定');
const handlerSource = appSource.slice(handlerStart, handlerEnd);

function invokeHandler(info, origin = 'https://production-dashboard.liruiqi250.workers.dev') {
  const navigations = [];
  const messages = [];
  const elements = new Map();
  const element = id => {
    if (!elements.has(id)) elements.set(id, { textContent: '' });
    return elements.get(id);
  };
  const context = {
    dingtalkInfo: info,
    URL,
    location: { origin, href: origin + '/', assign: target => navigations.push(target) },
    $: element,
    showError: message => messages.push(String(message)),
  };
  vm.runInNewContext(handlerSource + '\nstartDingTalk();', context, {
    timeout: 1000,
    filename: 'web/app.js:startDingTalk',
  });
  return {
    navigations,
    message: [...messages, ...[...elements.values()].map(value => value.textContent)].join(' '),
  };
}

function expectRejected(info, origin) {
  let result;
  assert.doesNotThrow(() => { result = invokeHandler(info, origin); }, '无效入口应显示提示，不能抛出异常');
  assert.deepEqual(result.navigations, [], '无效入口不能导航');
  assert.match(result.message, /[\u3400-\u9fff]/, '无效入口必须给出中文提示');
}

test('同源 HTTPS 钉钉入口可以跳转', () => {
  const target = 'https://production-dashboard.liruiqi250.workers.dev/auth/dingtalk/start';
  assert.deepEqual(invokeHandler({ enabled: true, login_url: target }).navigations, [target]);
});

test('相对钉钉入口按当前来源解析后可以跳转', () => {
  assert.deepEqual(
    invokeHandler({ enabled: true, login_url: '/auth/dingtalk/start' }).navigations,
    ['https://production-dashboard.liruiqi250.workers.dev/auth/dingtalk/start'],
  );
});

test('原内网 HTTP 钉钉入口仍可跳转', () => {
  const target = 'http://192.168.1.20:8765/auth/dingtalk/start';
  assert.deepEqual(invokeHandler({ enabled: true, login_url: target }, 'http://192.168.1.20:8765').navigations, [target]);
});

test('保留原有 HTTP 入口兼容性', () => {
  const target = 'http://192.168.1.20:8765/auth/dingtalk/start';
  assert.deepEqual(invokeHandler({ enabled: true, login_url: target }).navigations, [target]);
});

// 仅用于拒绝凭据URL的合成样本；分字段构造，避免被发布凭据扫描当作真实认证地址。
const credentialFixture = new URL('http://192.168.1.20:8765/auth/dingtalk/start');
credentialFixture.username = 'test-user';
credentialFixture.password = 'test-password';
const rejectedUrls = [
  ['跨来源 HTTPS', 'https://example.com/auth/dingtalk/start'],
  ['不同端口 HTTPS', 'https://production-dashboard.liruiqi250.workers.dev:444/auth/dingtalk/start'],
  ['JavaScript 协议', 'javascript:alert(1)'],
  ['文件协议', 'file:///auth/dingtalk/start'],
  ['FTP 协议', 'ftp://production-dashboard.liruiqi250.workers.dev/auth/dingtalk/start'],
  ['错误路径', 'https://production-dashboard.liruiqi250.workers.dev/auth/dingtalk/callback'],
  ['路径后多余斜杠', 'https://production-dashboard.liruiqi250.workers.dev/auth/dingtalk/start/'],
  ['HTTP 错误路径', 'http://192.168.1.20:8765/auth/dingtalk/callback'],
  ['畸形 URL', 'http://[invalid'],
  ['带用户名', 'https://user@production-dashboard.liruiqi250.workers.dev/auth/dingtalk/start'],
  ['HTTP 带用户名密码', credentialFixture.href],
  ['带查询参数', 'https://production-dashboard.liruiqi250.workers.dev/auth/dingtalk/start?next=https://example.com'],
  ['HTTP 带查询参数', 'http://192.168.1.20:8765/auth/dingtalk/start?next=example'],
  ['带片段', 'https://production-dashboard.liruiqi250.workers.dev/auth/dingtalk/start#other'],
  ['HTTP 带片段', 'http://192.168.1.20:8765/auth/dingtalk/start#other'],
];

for (const [name, login_url] of rejectedUrls) {
  test(name + '入口被拒绝并显示中文提示', () => {
    expectRejected({ enabled: true, login_url });
  });
}

test('钉钉登录未启用时显示中文提示', () => {
  expectRejected({ enabled: false, login_url: '/auth/dingtalk/start' });
});

test('钉钉登录缺少入口时显示中文提示', () => {
  expectRejected({ enabled: true });
});

test('钉钉登录空入口时显示中文提示', () => {
  expectRejected({ enabled: true, login_url: '' });
});
