'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

// 直接运行发布文件的三个搜索函数，不复制实现，不接入真实生产资料。
const source = fs.readFileSync(path.join(__dirname, '..', 'web', 'production.js'), 'utf8');
const begin = source.indexOf('function normalizeProductionSearch(');
const end = source.indexOf('function renderProduction(', begin);
assert.ok(begin >= 0 && end > begin, '必须找到实际发布的生产单搜索函数');
const searchSource = source.slice(begin, end);

function freeze(value) {
  if (value && typeof value === 'object') {
    for (const child of Object.values(value)) freeze(child);
    Object.freeze(value);
  }
  return value;
}

function fixtures() {
  return [
    { id: 'ORDER-RUSSIA', order_no: 'DY-2026-09-WXK-001', title: '俄罗斯仿真类生产单',
      business_contact: 'Chloe 王', source_ref: '生产资料群|message/test.db:100',
      search_terms: ['ELS1', '张英', '俄罗斯仿真类-生产单DY-2026-09-WXK-001.xlsx'],
      pieces: [
        { id: 'R-1', product_name: '胖蘑菇 Mushrooms TYPE A', links: [{ item_type: 'after_sales', item_id: 'AS-PRIVATE' }], stages: [{ state: '返工', note: '非搜索字段售后暗号' }] },
        { id: 'R-2', product_name: '动态魔法树 Magic Tree', links: [] },
      ],
      after_sales: [{ title: '不应进入生产单搜索的售后专有词', observed: '售后专有观察' }],
    },
    { id: 'ORDER-MUSEUM', order_no: 'DYNM-2026-10-zx-002', title: '国内展馆恐龙蛋',
      business_contact: '曾霞', source_ref: '指定生产资料来源',
      search_terms: ['成都博物馆', '郭逸', '恐龙蛋生产单(3).xlsx'],
      pieces: [{ id: 'M-1', product_name: '恐龙蛋 Dinosaur Egg' }, { id: 'M-2', product_name: '霸王龙 T-Rex' }],
    },
    { id: 'ORDER-UK', order_no: 'JNWM-2026-10-MRY-001', title: '英国雾森项目(1)+灯组[A]',
      business_contact: '李婷', source_ref: '雾森项目资料群',
      search_terms: ['Ocean Park UK', '银小芹', 'magic.tree.xlsx'],
      pieces: [{ id: 'U-1', product_name: '雾森 2.5米' }, { id: 'U-2', product_name: '灯组[A]+(1)' }],
    },
  ];
}

function harness() {
  const orders = freeze(fixtures());
  const state = freeze({ snapshot: { after_sales: { cases: [{ id: 'AS-PRIVATE', title: '售后专有词独立台账', observed: '只存在于售后观察' }] } } });
  const context = vm.createContext({ state });
  vm.runInContext(searchSource, context);
  return { orders, state,
    normalize: value => context.normalizeProductionSearch(value),
    matches: (order, query) => context.matchesProductionOrder(order, query),
    filter: (selected = 'all', query = '') => context.filteredProductionOrders(orders, selected, query),
  };
}
const ids = values => Array.from(values, order => order.id);

for (const [label, query, expected] of [
  ['业务员', 'Chloe', 'ORDER-RUSSIA'],
  ['业务员中文', '曾霞', 'ORDER-MUSEUM'],
  ['生产单号', 'DYNM-2026-10-zx-002', 'ORDER-MUSEUM'],
  ['订单标题', '英国雾森', 'ORDER-UK'],
  ['客户', '成都博物馆', 'ORDER-MUSEUM'],
  ['跟单员', '银小芹', 'ORDER-UK'],
  ['原文件名', '恐龙蛋生产单(3).xlsx', 'ORDER-MUSEUM'],
  ['原消息来源', 'message/test.db:100', 'ORDER-RUSSIA'],
  ['产品中文', '动态魔法树', 'ORDER-RUSSIA'],
  ['产品英文', 'dinosaur egg', 'ORDER-MUSEUM'],
]) test('搜索能够命中' + label, () => {
  const h = harness();
  assert.deepEqual(ids(h.filter('all', query)), [expected]);
});

test('多个关键词取AND交集，可跨业务员、客户、产品与标题', () => {
  const h = harness();
  assert.deepEqual(ids(h.filter('all', 'CHLOE ELS1 魔法树')), ['ORDER-RUSSIA']);
  assert.deepEqual(ids(h.filter('all', '曾霞 成都 霸王龙')), ['ORDER-MUSEUM']);
  assert.deepEqual(ids(h.filter('all', 'Chloe 成都')), []);
  assert.deepEqual(ids(h.filter('all', '魔法树 不存在')), []);
});

test('订单下拉筛选与搜索取交集，不切换到其它命中的订单', () => {
  const h = harness();
  assert.deepEqual(ids(h.filter('ORDER-MUSEUM', '恐龙蛋')), ['ORDER-MUSEUM']);
  assert.deepEqual(ids(h.filter('ORDER-RUSSIA', '恐龙蛋')), []);
  assert.deepEqual(ids(h.filter('ORDER-MUSEUM', '')), ['ORDER-MUSEUM']);
  assert.deepEqual(ids(h.filter('ORDER-MISSING', '')), []);
});

test('匹配产品时返回完整生产单及所有产品，不截断关联产品清单', () => {
  const h = harness();
  const result = h.filter('all', '胖蘑菇');
  assert.equal(result.length, 1);
  assert.equal(result[0], h.orders[0]);
  assert.equal(result[0].pieces, h.orders[0].pieces);
  assert.deepEqual(Array.from(result[0].pieces, piece => piece.id), ['R-1', 'R-2']);
});

test('没有关键词或仅空白时显示全部订单，保留原顺序', () => {
  const h = harness(), expected = ids(h.orders);
  for (const query of ['', '   ', '\t\n\r', '\u3000', null, undefined]) {
    assert.deepEqual(ids(h.filter('all', query)), expected);
  }
});

test('无匹配与空订单输入均返回空数组，不抛异常', () => {
  const h = harness();
  assert.deepEqual(ids(h.filter('all', '这里没有对应的生产单')), []);
  const context = vm.createContext({});
  vm.runInContext(searchSource, context);
  assert.deepEqual(Array.from(context.filteredProductionOrders([], 'all', '俄罗斯')), []);
  assert.equal(h.matches({ pieces: [] }, '不存在'), false);
});

test('NFKC兼容全角字母数字标点，英文大小写与普通/全角空格', () => {
  const h = harness();
  assert.equal(h.normalize(' Ｃｈｌｏｅ　王 '), 'chloe王');
  assert.deepEqual(ids(h.filter('all', 'ＤＹ－２０２６－０９－ＷＸＫ－００１')), ['ORDER-RUSSIA']);
  assert.deepEqual(ids(h.filter('all', 'ｃｈｌｏｅ　ＥＬＳ１')), ['ORDER-RUSSIA']);
  assert.deepEqual(ids(h.filter('all', 'Ｔ－ＲＥＸ')), ['ORDER-MUSEUM']);
  assert.deepEqual(ids(h.filter('all', 'OceanParkUK')), ['ORDER-UK']);
  assert.deepEqual(ids(h.filter('all', 'Ocean  Park\tUK')), ['ORDER-UK']);
});

test('中文与英文关键词可组合，产品内部空格不妨碍匹配', () => {
  const h = harness();
  assert.deepEqual(ids(h.filter('all', '胖蘑菇 TYPEA')), ['ORDER-RUSSIA']);
  assert.deepEqual(ids(h.filter('all', '魔法 树 MagicTree')), ['ORDER-RUSSIA']);
  assert.deepEqual(ids(h.filter('all', '英国 Ocean 李婷')), ['ORDER-UK']);
});

test('售后独立台账、单件售后关联及工序备注不自动进入搜索索引', () => {
  const h = harness();
  for (const query of ['售后专有词', '售后专有观察', '只存在于售后观察', 'AS-PRIVATE', '非搜索字段售后暗号']) {
    assert.deepEqual(ids(h.filter('all', query)), []);
  }
});

test('搜索内容按纯文本匹配，不执行正则或通配符', () => {
  const h = harness();
  for (const query of ['.*', '^俄罗斯', '魔法.*树', '(a+)+$', '[', '\\d+', '俄罗斯|英国']) {
    assert.doesNotThrow(() => h.filter('all', query));
    assert.deepEqual(ids(h.filter('all', query)), query === '[' ? ['ORDER-UK'] : []);
  }
  assert.deepEqual(ids(h.filter('all', '[A]+(1)')), ['ORDER-UK']);
  assert.deepEqual(ids(h.filter('all', 'magic.tree.xlsx')), ['ORDER-UK']);
});

test('输入数组及嵌套订单保持只读，查询不修改主档、工序或关联', () => {
  const h = harness(), before = JSON.stringify(h.orders), stateBefore = JSON.stringify(h.state);
  for (const query of ['Chloe ELS1', '霸王龙', '', '[A]+(1)', '不存在']) {
    assert.doesNotThrow(() => h.filter('all', query));
  }
  assert.equal(JSON.stringify(h.orders), before);
  assert.equal(JSON.stringify(h.state), stateBefore);
  assert.equal(Object.isFrozen(h.orders), true);
  assert.equal(Object.isFrozen(h.orders[0].pieces[0].stages), true);
});
