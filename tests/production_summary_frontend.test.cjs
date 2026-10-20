'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

// Run the shipped renderer, with only its text-node builder replaced by a small DOM stub.
const source = fs.readFileSync(path.join(__dirname, '..', 'web', 'production.js'), 'utf8');
const start = source.indexOf('function productionPieceSummary(');
const end = source.indexOf('function renderProduction(', start);
assert.ok(start >= 0 && end > start, '必须找到实际单件摘要渲染函数');
const renderer = source.slice(start, end);
const stageNames = ['机械图', '钳工（机械、钢架）', '海绵（造型）', '裱糊（纹理、表皮）', '刷胶', '上色（喷色）'];

function node(tag, attributes = {}, text = '') {
  return {
    tag, attributes, childNodes: [], ownText: text,
    append(...children) { this.childNodes.push(...children); },
    get textContent() { return [this.ownText, ...this.childNodes.map(child => child.textContent)].join(' '); },
  };
}

function render(states, changes = {}) {
  const piece = {
    piece_label: '01', product_name: '测试产品', delivery_state: '未安排',
    stages: states.map((state, index) => Object.freeze({ stage: stageNames[index], state })),
    ...changes,
  };
  Object.freeze(piece.stages);
  Object.freeze(piece);
  return vm.runInNewContext(renderer + '\nproductionPieceSummary(piece);', { node, piece }, { timeout: 1000 });
}

function checkpoints(summary) {
  return summary.childNodes.find(child => child.attributes.class === 'production-checkpoints').childNodes;
}

test('折叠摘要保留单件、合格数、交付和待处理总数，并列明实际卡点', () => {
  const summary = render(['合格', '待业务确认', '返工', '暂缓', '未核对', '进行中']);
  assert.equal(summary.tag, 'summary');
  assert.match(summary.textContent, /01 · 测试产品/);
  assert.match(summary.textContent, /合格 1\/6 · 交付 未安排/);
  assert.match(summary.textContent, /3 道待处理/);
  const lines = checkpoints(summary).map(child => child.textContent);
  assert.deepEqual(lines, [
    '待业务确认 1 道：钳工（机械、钢架）',
    '返工 1 道：海绵（造型）',
    '暂缓 1 道：裱糊（纹理、表皮）',
    '未核对 1 道：刷胶',
  ]);
});

test('同一状态按工序顺序合并，数量与工序名相符', () => {
  const lines = checkpoints(render(['返工', '合格', '返工', '合格', '未核对', '未核对']));
  assert.equal(lines[0].attributes['data-state'], '返工');
  assert.equal(lines[0].textContent, '返工 2 道：机械图、海绵（造型）');
  assert.equal(lines[1].textContent, '未核对 2 道：刷胶、上色（喷色）');
});

test('全部未核对只表示尚无核对结论，不能改成未开始或卡点总数', () => {
  const summary = render(Array(6).fill('未核对'));
  assert.match(summary.textContent, /未核对 6 道：机械图、钳工（机械、钢架）/);
  assert.doesNotMatch(summary.textContent, /未开始|待处理|生产完成|%/);
  assert.equal(checkpoints(summary)[0].attributes.class, 'production-checkpoint muted');
});

test('全部合格明确是工序记录，不推断交付或生产完成', () => {
  const summary = render(Array(6).fill('合格'));
  assert.match(summary.textContent, /合格 6\/6 · 交付 未安排/);
  assert.match(summary.textContent, /6 道工序均已记录为合格/);
  assert.doesNotMatch(summary.textContent, /待处理|未核对|生产完成|已交付|%/);
});

test('已记录未开始与进行中保持原状态，不推断为待处理', () => {
  const summary = render(['未开始', '未开始', '进行中', '进行中', '不适用', '合格']);
  assert.match(summary.textContent, /进行中 2 道 · 未开始 2 道 · 不适用 1 道/);
  assert.doesNotMatch(summary.textContent, /待处理|未核对/);
});

test('不适用不增加合格数，也不误报全部完成', () => {
  const summary = render(Array(6).fill('不适用'));
  assert.match(summary.textContent, /合格 0\/6/);
  assert.match(summary.textContent, /不适用 6 道/);
  assert.doesNotMatch(summary.textContent, /均已记录为合格|生产完成|%/);
});

test('零阶段和缺少阶段时显示尚无工序记录', () => {
  for (const summary of [render([]), render([], { stages: undefined })]) {
    assert.match(summary.textContent, /尚无工序记录/);
    assert.doesNotMatch(summary.textContent, /均已记录为合格|待处理/);
  }
});

test('长中文名称原样保留为文本，状态分组不省略具体工序', () => {
  const name = '同一生产单下的长中文产品名称及补充说明'.repeat(10);
  const summary = render(Array(6).fill('待业务确认'), { product_name: name });
  assert.equal(summary.childNodes[0].ownText, '01 · ' + name);
  const line = checkpoints(summary)[0].textContent;
  for (const stage of stageNames) assert.ok(line.includes(stage));
  assert.match(line, /^待业务确认 6 道：/);
});

test('渲染遵守输入只读，不改变数据库状态口径', () => {
  assert.doesNotThrow(() => render(['待业务确认', '返工', '暂缓', '未核对', '进行中', '合格']));
  assert.match(source, /未核对表示尚无人工核对结论，不代表未开始/);
});
