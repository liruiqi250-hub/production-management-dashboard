'use strict';

const VERSION = '0.3.0';
const STAGES = ['丝架', '装灯', '裱糊', '上色'];
const STATUSES = ['未开始', '进行中', '已完成'];
const MERCHANDISERS = ['黄钰萌', '田秀玲'];
const SHIPMENT_CHOICES = {
  order_status: ['未安排', '待生产', '生产中', '待装货', '已装货', '已出运', '已到港', '清关中', '派送中', '已完成'],
  payment: ['待确认', '未付款', '部分付款', '已付清'],
  trade_terms: ['EXW', 'FCA', 'FOB', 'CFR', 'CIF', 'DAP', 'DDP'],
  customs_delivery: ['待确认', '不包含', '包含'],
  product_category: ['彩灯', '恐龙', '其他'],
  container_type: ['20GP', '40GP', '40HQ', '其他'],
  merchandiser: MERCHANDISERS
};
const SHIPMENT_FIELDS = [
  ['country', '国家'], ['customer', '客户名称'], ['documents', '清关资料、电放提单提供情况'],
  ['order_status', '订单进度'], ['trade_terms', '贸易条款'], ['payment', '付款情况'],
  ['quantity', '数量'], ['loading_date', '装货时间', 'date'], ['route', '路线'],
  ['container_no', '柜号'], ['shipping_line', '船司'], ['container_type', '箱型'],
  ['forwarder', '货代公司'], ['etd', 'ETD / 预计离港', 'date'], ['eta', 'ETA / 预计到港', 'date'],
  ['salesperson', '业务员'], ['merchandiser', '跟单员'], ['document_owner', '资料制作员'],
  ['customs_delivery', '是否清关派送'], ['product_category', '产品类别'], ['notes', '备注', 'textarea']
];
const state = {
  user: null, csrf: '', lanUrl: '', data: {projects: [], shipments: [], setup: {}},
  page: 'projects', selected: null, loading: false, lastRefresh: null, epoch: 0,
  editor: null, dirty: false, saving: false, uploading: false, toastTimer: null,
  shipmentOpen: new Set()
};
const $ = id => document.getElementById(id);
function el(tag, attrs = {}, content) {
  const item = document.createElement(tag);
  for (const [name, value] of Object.entries(attrs)) {
    if (value !== undefined && value !== null) item.setAttribute(name, String(value));
  }
  if (content !== undefined && content !== null) item.textContent = String(content);
  return item;
}
function clear(item) { item.replaceChildren(); return item; }
function showError(message = '') { $('global-error').textContent = message; $('global-error').hidden = !message; }
function editorError(message = '') { $('editor-error').textContent = message; $('editor-error').hidden = !message; }
function canEdit() { return ['admin', 'editor'].includes(state.user?.role); }
function isAdmin() { return state.user?.role === 'admin'; }
function text(value, empty = '未填写') { return value === undefined || value === null || value === '' ? empty : String(value); }
function unique(values) { return [...new Set(values.filter(v => v !== undefined && v !== null && v !== '').map(String))].sort((a, b) => a.localeCompare(b, 'zh-CN')); }
function roleLabel(role) { return {admin: '管理员', editor: '编辑', viewer: '仅查看'}[role] || '已授权'; }
function toast(message) {
  clearTimeout(state.toastTimer);
  $('toast').textContent = message; $('toast').hidden = false;
  state.toastTimer = setTimeout(() => { $('toast').hidden = true; }, 4200);
}
async function api(url, options = {}) {
  const config = {credentials: 'same-origin', method: options.method || 'GET', headers: {Accept: 'application/json'}};
  if (options.body !== undefined) {
    config.headers['Content-Type'] = 'application/json';
    config.headers['X-CSRF-Token'] = state.csrf;
    config.body = JSON.stringify(options.body);
  }
  const response = await fetch(url, config);
  const result = await response.json().catch(() => ({}));
  if (!response.ok) {
    const message = typeof result.error === 'string' ? result.error : (typeof result.message === 'string' ? result.message : `请求失败（${response.status}）`);
    const error = new Error(message); error.status = response.status; error.code = result.code;
    if (response.status === 401 && url !== '/api/login' && state.user) showLogin('登录已过期，请重新登录。');
    throw error;
  }
  return result;
}
function showLogin(message = '') {
  state.epoch++; state.user = null; state.csrf = ''; state.data = {projects: [], shipments: [], setup: {}};
  state.editor = null; state.dirty = false;
  if ($('editor-dialog').open) $('editor-dialog').close();
  clear($('new-user-secret')); $('new-user-secret').hidden = true;
  state.shipmentOpen.clear();
  clear($('project-list')); clear($('project-detail')); clear($('shipment-list')); clear($('user-list'));
  $('application').hidden = true; $('password-screen').hidden = true; $('login-screen').hidden = false;
  $('login-error').textContent = message; $('first-password-error').textContent = '';
  $('login-form').reset(); showError();
}
function showSession(me) {
  state.user = me.user; state.csrf = me.csrf || state.csrf; state.lanUrl = me.lan_url || '';
  $('login-screen').hidden = true;
  if (state.user.must_change_password) {
    $('application').hidden = true; $('password-screen').hidden = false;
    $('first-password-user').textContent = `${text(state.user.display_name, state.user.username)}，请为你的独立彩灯账号设置密码。`;
    return false;
  }
  $('password-screen').hidden = true; $('application').hidden = false;
  $('user-name').textContent = `${text(state.user.display_name, state.user.username)} · ${roleLabel(state.user.role)}`;
  $('accounts-nav').hidden = !isAdmin();
  $('new-project').hidden = !canEdit(); $('new-shipment').hidden = !canEdit();
  if (!isAdmin() && state.page === 'accounts') state.page = 'projects';
  setPage(state.page);
  return true;
}
async function bootstrap() {
  try { const me = await api('/api/me'); if (showSession(me)) await loadData(); }
  catch (error) { if (error.status !== 401) $('login-error').textContent = error.message; }
}
function refreshLabel() {
  const stamp = state.lastRefresh ? state.lastRefresh.toLocaleTimeString('zh-CN', {hour: '2-digit', minute: '2-digit', second: '2-digit'}) : '';
  $('refresh-status').textContent = state.editor ? '编辑中 · 自动刷新已暂停' : (stamp ? `${stamp} 已刷新 · 每30秒` : '尚未刷新');
  $('refresh-button').disabled = state.loading || Boolean(state.editor);
}
async function loadData({quiet = false} = {}) {
  if (!state.user || state.user.must_change_password || state.loading) return;
  if (state.editor) { refreshLabel(); return; }
  const epoch = state.epoch;
  state.loading = true; refreshLabel();
  if (!quiet) showError();
  try {
    const result = await api('/api/lantern');
    if (epoch !== state.epoch) return;
    state.data = {projects: Array.isArray(result.projects) ? result.projects : [], shipments: Array.isArray(result.shipments) ? result.shipments : [], setup: result.setup || {}};
    state.lastRefresh = new Date(); render(); showError();
  } catch (error) { if (epoch === state.epoch && error.status !== 401) showError(`刷新失败：${error.message}。当前显示上次已读取的数据。`); }
  finally { state.loading = false; refreshLabel(); }
}
function setPage(page) {
  if (page === 'accounts' && !isAdmin()) return;
  if (state.page === 'accounts' && page !== 'accounts') { clear($('new-user-secret')); $('new-user-secret').hidden = true; }
  state.page = page;
  document.querySelectorAll('[data-view]').forEach(view => { view.hidden = view.dataset.view !== page; });
  document.querySelectorAll('[data-page]').forEach(button => {
    if (button.dataset.page === page) button.setAttribute('aria-current', 'page'); else button.removeAttribute('aria-current');
  });
  if (page === 'accounts') loadUsers();
}
function optionsFor(select, values, first) {
  const previous = select.value; clear(select); select.append(el('option', {value: ''}, first));
  for (const value of unique(values)) select.append(el('option', {value}, value));
  select.value = values.map(String).includes(previous) ? previous : '';
}
function productDone(product) { return STAGES.filter(stage => product.stages?.[stage]?.status === '已完成').length; }
function productUnstarted(product) { return STAGES.every(stage => !product.stages?.[stage]?.status || product.stages[stage].status === '未开始'); }
function deadline(date) {
  if (!date || !/^\d{4}-\d{2}-\d{2}$/.test(date)) return {value: '—', label: '完工日期未填写'};
  const [year, month, day] = date.split('-').map(Number);
  const now = new Date();
  const remaining = Math.round((Date.UTC(year, month - 1, day) - Date.UTC(now.getFullYear(), now.getMonth(), now.getDate())) / 86400000);
  return remaining < 0 ? {value: `超期 ${-remaining}`, label: `天 · 完工日期 ${date}`} : {value: remaining, label: remaining === 0 ? '今天到期' : `天 · 完工日期 ${date}`};
}
function metric(label, value, detail, tone = '') {
  const card = el('article', {class: 'metric-card', 'data-tone': tone});
  card.append(el('span', {}, label), el('strong', {}, value), el('small', {}, detail)); return card;
}
function filteredProjects() {
  const query = $('project-search').value.trim().toLocaleLowerCase();
  return state.data.projects.filter(project =>
    (!$('merchandiser-filter').value || project.merchandiser === $('merchandiser-filter').value) &&
    (!$('salesperson-filter').value || project.salesperson === $('salesperson-filter').value) &&
    (!$('country-filter').value || project.country === $('country-filter').value) &&
    (!query || [project.name, project.order_no, ...(project.products || []).map(product => `${product.name || ''} ${product.specification || ''}`)].join(' ').toLocaleLowerCase().includes(query))
  );
}
function emptyState(title, description, action) {
  const box = el('div', {class: 'empty'}); box.append(el('h3', {}, title), el('p', {}, description));
  if (action) { const actions = el('div', {class: 'actions'}); const button = el('button', {type: 'button', class: 'primary'}, action.label); button.addEventListener('click', action.run); actions.append(button); box.append(actions); }
  return box;
}
function renderProjects() {
  const projects = filteredProjects();
  const products = projects.flatMap(project => project.products || []);
  const done = products.filter(product => productDone(product) === 4).length;
  const metrics = clear($('overview-metrics'));
  metrics.append(metric('项目数量', projects.length, '当前筛选范围', 'indigo'), metric('产品种类 / 条目', products.length, '按各项目产品行统计'), metric('已完成产品', done, '四工序均已确认完成', 'green'), metric('未完成产品', products.length - done, '待完成全部四工序', 'gold'));
  $('overview-scope').textContent = `${isAdmin() ? '管理员汇总 · 查看全部已登记项目' : '查看当前账号可访问的项目'} · 丝架 / 装灯 / 裱糊 / 上色`;
  $('project-count').textContent = `${projects.length} / ${state.data.projects.length}`;
  const list = clear($('project-list'));
  if (!projects.some(project => String(project.id) === String(state.selected))) state.selected = projects[0]?.id ?? null;
  for (const project of projects) {
    const productList = project.products || [];
    const complete = productList.filter(product => productDone(product) === 4).length;
    const button = el('button', {type: 'button', class: 'project-card', 'aria-pressed': String(project.id) === String(state.selected)});
    button.append(el('strong', {}, text(project.name, '未命名项目')), el('p', {class: 'project-card-meta'}, `${text(project.country, '国家未填写')} · ${text(project.order_no, '订单号未填写')}`), el('p', {class: 'project-card-meta'}, `跟单 ${text(project.merchandiser, '待确认')} · 业务 ${text(project.salesperson, '待确认')}`));
    const bottom = el('div', {class: 'project-card-bottom'}); bottom.append(el('span', {}, `已完成 ${complete} / ${productList.length} 项`), el('span', {}, project.completion_date || '完工日期未填写')); button.append(bottom);
    button.addEventListener('click', () => { state.selected = project.id; renderProjects(); }); list.append(button);
  }
  const detail = clear($('project-detail'));
  if (!projects.length) {
    const hasProjects = state.data.projects.length > 0;
    list.append(el('p', {class: 'small muted'}, hasProjects ? '没有符合筛选条件的项目。' : '暂无项目。'));
    detail.append(emptyState(hasProjects ? '没有匹配的项目' : '暂无项目', hasProjects ? '调整跟单、业务员、国家或搜索条件后查看。' : '生产单及项目资料确认后，在这里登记项目、产品与实际工序进展。', !hasProjects && canEdit() ? {label: '＋ 新增第一个项目', run: () => openProject()} : null));
    return;
  }
  renderProjectDetail(projects.find(project => String(project.id) === String(state.selected)), detail);
}
function renderProjectDetail(project, target) {
  const products = project.products || [], done = products.filter(product => productDone(product) === 4).length;
  const hero = el('article', {class: 'project-hero'}), top = el('div', {class: 'project-hero-top'}), title = el('div');
  title.append(el('p', {class: 'eyebrow'}, 'PROJECT PROGRESS'), el('h2', {}, text(project.name)), el('p', {class: 'project-subline'}, `订单号 ${text(project.order_no)} · 最近更新 ${text(project.updated_at)}`)); top.append(title);
  if (canEdit()) { const edit = el('button', {type: 'button'}, '编辑项目'); edit.addEventListener('click', () => openProject(project)); top.append(edit); }
  hero.append(top);
  const fields = el('dl', {class: 'project-fields'});
  for (const [label, value] of [['国家', project.country], ['电压', project.voltage], ['完工日期', project.completion_date], ['跟单 / 业务员', `${text(project.merchandiser)} / ${text(project.salesperson)}`]]) {
    const field = el('div'); field.append(el('dt', {}, label), el('dd', {}, text(value))); fields.append(field);
  }
  hero.append(fields);
  if (project.source_attachment) {
    const attachment = typeof project.source_attachment === 'object' ? project.source_attachment.id : project.source_attachment;
    const p = el('p', {class: 'project-subline'}); p.append(el('a', {href: '/api/lantern/orders/' + encodeURIComponent(attachment), download: ''}, '下载已留存生产单')); hero.append(p);
  }
  if (project.notes) hero.append(el('p', {class: 'project-notes'}, project.notes));
  target.append(hero);
  const days = deadline(project.completion_date), metrics = el('div', {class: 'metrics detail-metrics'});
  metrics.append(metric('总产品种类 / 条目', products.length, '本项目登记产品行'), metric('已完成', done, '四工序全部完成', 'green'), metric('未完成', products.length - done, '包含未开始和进行中', 'gold'), metric('剩余天数', days.value, days.label, 'indigo')); target.append(metrics);
  const panel = el('div', {class: 'panel'}), heading = el('div', {class: 'section-heading'});
  heading.append(el('h3', {}, '产品与四工序进度'), el('span', {class: 'small muted'}, '按完成工序个数 / 4')); panel.append(heading);
  if (!products.length) { panel.append(emptyState('暂无产品', '编辑项目后添加产品名称、规格与数量。')); target.append(panel); return; }
  const wrap = el('div', {class: 'table-wrap', tabindex: '0', 'aria-label': '产品四工序进度，可横向滚动'}), table = el('table', {class: 'product-table'}), head = el('thead'), header = el('tr');
  for (const label of ['产品名称', '规格', '数量', ...STAGES, '完成进度']) header.append(el('th', {scope: 'col'}, label)); head.append(header); table.append(head);
  const body = el('tbody');
  for (const product of products) {
    const row = el('tr', {class: productUnstarted(product) ? 'not-started' : ''});
    row.append(el('td', {class: 'product-name'}, text(product.name)), el('td', {}, text(product.specification)), el('td', {}, text(product.quantity)));
    for (const stageName of STAGES) {
      const stage = product.stages?.[stageName] || {}, status = STATUSES.includes(stage.status) ? stage.status : '未开始';
      const cell = el('td'); cell.append(el('span', {class: 'stage-pill', 'data-status': status}, status));
      if (stage.note) cell.append(el('p', {class: 'stage-note'}, stage.note)); row.append(cell);
    }
    const count = productDone(product), progress = el('td'); progress.append(el('span', {class: 'progress-label'}, `${count} / 4`));
    const track = el('div', {class: 'progress-track', 'aria-hidden': 'true'}); for (let i = 0; i < 4; i++) track.append(el('i', {class: i < count ? 'complete' : ''})); progress.append(track); row.append(progress); body.append(row);
  }
  table.append(body); wrap.append(table); panel.append(wrap, el('p', {class: 'table-legend'}, '暖金底色表示四工序均未开始。标记已完成须填写实际完成依据。')); target.append(panel);
}
function renderShipments() {
  const query = $('shipment-search').value.trim().toLocaleLowerCase(), selectedStatus = $('shipment-status').value;
  const shipments = state.data.shipments.filter(shipment => (!selectedStatus || shipment.order_status === selectedStatus) && (!query || [shipment.order_no, ...SHIPMENT_FIELDS.map(([key]) => shipment[key])].join(' ').toLocaleLowerCase().includes(query)));
  $('shipment-count').textContent = `显示 ${shipments.length} / ${state.data.shipments.length} 条 · 点击记录展开21项字段`;
  const list = $('shipment-list');
  for (const item of list.querySelectorAll('details[data-shipment-id]')) {
    if (item.open) state.shipmentOpen.add(item.dataset.shipmentId); else state.shipmentOpen.delete(item.dataset.shipmentId);
  }
  clear(list);
  for (const shipment of shipments) {
    const id = String(shipment.id), record = el('details', {class: 'shipment-record', 'data-shipment-id': id});
    record.open = state.shipmentOpen.has(id);
    const summary = el('summary', {class: 'shipment-summary'}), identity = el('div', {class: 'shipment-identity'});
    identity.append(el('strong', {}, text(shipment.customer, '客户未填写')), el('span', {}, `订单号 ${text(shipment.order_no, '未填写')}`));
    const overview = el('div', {class: 'shipment-overview'});
    overview.append(el('span', {class: 'shipment-country'}, text(shipment.country, '国家未填写')), el('span', {class: 'shipment-status-label'}, text(shipment.order_status, '进度未填写')));
    const dates = el('div', {class: 'shipment-dates'}); dates.append(el('span', {}, `ETD ${text(shipment.etd, '—')}`), el('span', {}, `ETA ${text(shipment.eta, '—')}`));
    summary.append(identity, overview, dates, el('span', {class: 'shipment-chevron', 'aria-hidden': 'true'}, '⌄')); record.append(summary);
    const content = el('div', {class: 'shipment-content'}), fields = el('dl', {class: 'shipment-fields'});
    const linked = state.data.projects.find(project => String(project.id) === String(shipment.project_id));
    for (const [label, value] of [['关联订单号', shipment.order_no], ['关联项目', shipment.project_id ? text(linked?.name, '关联项目不可见') : ''], ...SHIPMENT_FIELDS.map(([key, label]) => [label, shipment[key]])]) {
      const field = el('div', {class: label === '备注' || label === '清关资料、电放提单提供情况' ? 'wide' : ''});
      field.append(el('dt', {}, label), el('dd', {}, text(value, '—'))); fields.append(field);
    }
    const footer = el('div', {class: 'shipment-footer'}), button = el('button', {type: 'button'}, canEdit() ? '编辑出货记录' : '查看详情');
    button.addEventListener('click', () => openShipment(shipment));
    footer.append(el('span', {class: 'small muted'}, '订单进度按人工登记显示；“已完成”不代表已确认实际收货。'), button); content.append(fields, footer); record.append(content);
    record.addEventListener('toggle', () => { if (!record.isConnected) return; if (record.open) state.shipmentOpen.add(id); else state.shipmentOpen.delete(id); });
    list.append(record);
  }
  $('shipment-empty').hidden = Boolean(shipments.length);
  $('shipment-empty').textContent = state.data.shipments.length ? '没有符合当前筛选条件的出货记录。' : '暂无出货记录。确认客户、单证、船期等资料后再登记。';
}
function renderSetup() {
  const cards = clear($('setup-cards'));
  for (const [key, icon, title, description] of [
    ['wechat', '微', '微信生产群', '指定群与采集授权待确认。当前项目及工序由授权人员登记，不会自动把群消息当成完工。'],
    ['email', '邮', '阿里企业邮箱', '邮箱授权及归属规则待提供。当前没有邮件自动抓取或自动生成订单功能。'],
    ['templates', '单', '生产单与出货模板', '请提供真实样本及字段口径。生产单上传仅用于留存，自动解析待样本接入后验收。']
  ]) {
    const source = state.data.setup[key], card = el('article', {class: 'panel setup-card'});
    const status = typeof source === 'object' && source ? text(source.status, '待提供 / 待接入') : text(source, '待提供 / 待接入');
    const detail = typeof source === 'object' && source ? (source.description || source.message || source.note) : '';
    card.append(el('div', {class: 'setup-icon', 'aria-hidden': 'true'}, icon), el('h3', {}, title), el('p', {}, detail || description), el('span', {class: 'setup-state'}, status)); cards.append(card);
  }
  $('lan-address').textContent = state.lanUrl ? `本系统内网地址：${state.lanUrl}` : '内网访问地址以本机启动信息为准。';
}
function render() {
  optionsFor($('merchandiser-filter'), state.data.projects.map(p => p.merchandiser), '全部跟单');
  optionsFor($('salesperson-filter'), state.data.projects.map(p => p.salesperson), '全部业务员');
  optionsFor($('country-filter'), state.data.projects.map(p => p.country), '全部国家');
  optionsFor($('shipment-status'), state.data.shipments.map(s => s.order_status), '全部进度');
  renderProjects(); renderShipments(); renderSetup();
}
function field(label, name, value = '', {type = 'text', required = false, max = 200, help = '', wide = false} = {}) {
  const wrapper = el('label', {class: wide ? 'wide' : ''}, label);
  const input = el(type === 'textarea' ? 'textarea' : 'input', {name, maxlength: max, ...(type !== 'textarea' ? {type} : {rows: '3'})});
  input.value = value ?? ''; if (required) input.required = true;
  if (help) input.placeholder = help;
  wrapper.append(input); return {wrapper, input};
}
function choiceField(label, name, value = '', choices = []) {
  const wrapper = el('label', {}, label), input = el('select', {name});
  input.append(el('option', {value: ''}, '未知 / 留空'));
  for (const option of [...new Set([...choices, ...state.data.shipments.map(item => item[name]), ...(name === 'merchandiser' ? state.data.projects.map(item => item.merchandiser) : []), value].filter(item => item !== undefined && item !== null && item !== '').map(String))]) input.append(el('option', {value: option}, option));
  input.value = value ?? ''; wrapper.append(input); return {wrapper, input};
}
function openEditor(kind, title, kicker, original = null, readOnly = false) {
  state.editor = {kind, original, readOnly}; state.dirty = false; state.saving = false; state.uploading = false;
  $('editor-title').textContent = title; $('editor-kicker').textContent = kicker; clear($('editor-body')); editorError();
  $('save-editor').hidden = readOnly; $('save-editor').disabled = false; $('save-editor').textContent = '保存';
  $('cancel-editor').textContent = readOnly ? '关闭' : '取消'; $('editor-hint').textContent = '表单打开期间暂停自动刷新。';
  $('editor-dialog').showModal(); refreshLabel();
}
function closeEditor(force = false) {
  if (!force && (state.saving || state.uploading)) { editorError('正在保存或上传，请等待完成。'); return; }
  if (!force && state.dirty && !confirm('当前输入尚未保存，确定关闭编辑窗口？')) return;
  state.editor = null; state.dirty = false; state.uploading = false; $('editor-dialog').close(); clear($('editor-body')); editorError(); refreshLabel();
}
function addProduct(container, product = {}) {
  const block = el('section', {class: 'product-edit'}); block._productId = product.id;
  const header = el('div', {class: 'product-edit-header'}), title = el('strong', {}, `产品 ${container.children.length + 1}`), remove = el('button', {type: 'button', class: 'danger'}, '移除产品');
  remove.addEventListener('click', () => { if (!confirm('从当前项目表单移除此产品？保存项目后才会生效。')) return; block.remove(); [...container.children].forEach((child, index) => { child.querySelector('.product-edit-header strong').textContent = `产品 ${index + 1}`; }); state.dirty = true; });
  header.append(title, remove); block.append(header);
  const basics = el('div', {class: 'product-basics'});
  for (const [label, name, value, config] of [['产品名称', 'product_name', product.name, {required: true, max: 160}], ['规格', 'product_specification', product.specification, {max: 300, help: '未知可留空'}], ['数量', 'product_quantity', product.quantity ?? '', {type: 'number', help: '未知留空'}]]) {
    const item = field(label, name, value, config); if (name === 'product_quantity') { item.input.min = '0'; item.input.max = '1000000'; item.input.step = 'any'; } basics.append(item.wrapper);
  }
  block.append(basics);
  const stages = el('div', {class: 'stage-edit-grid'});
  for (const stageName of STAGES) {
    const stage = product.stages?.[stageName] || {}, box = el('div', {class: 'stage-edit', 'data-stage': stageName}); box.append(el('strong', {}, stageName));
    const statusLabel = el('label', {}, '工序状态'), select = el('select', {name: 'stage_status', 'aria-label': `${stageName}状态`});
    for (const status of STATUSES) select.append(el('option', {value: status}, status)); select.value = STATUSES.includes(stage.status) ? stage.status : '未开始'; statusLabel.append(select);
    const note = field('完成依据 / 跟进备注', 'stage_note', stage.note, {type: 'textarea', max: 2000, help: '已完成时须填写实际结果或确认记录'});
    function validateNote() { note.input.required = select.value === '已完成'; note.wrapper.firstChild.textContent = select.value === '已完成' ? '完成依据（必填）' : '完成依据 / 跟进备注'; }
    select.addEventListener('change', validateNote); validateNote(); box.append(statusLabel, note.wrapper); stages.append(box);
  }
  block.append(stages); container.append(block);
}
function openProject(project = null) {
  if (!canEdit()) return;
  openEditor('project', project ? '编辑项目' : '新增项目', 'PROJECT DETAILS', project);
  const body = $('editor-body'), grid = el('div', {class: 'form-grid'});
  for (const [label, key, config] of [
    ['项目名称', 'name', {required: true, max: 160, wide: true}], ['订单号', 'order_no', {max: 120}],
    ['国家', 'country', {max: 80, help: '以实际订单为准'}], ['电压', 'voltage', {max: 80, help: '例如已确认的电压规格'}],
    ['完工日期', 'completion_date', {type: 'date'}], ['业务员', 'salesperson', {max: 80}], ['跟单', 'merchandiser', {max: 80}],
    ['项目备注', 'notes', {type: 'textarea', max: 4000, wide: true}]
  ]) grid.append((key === 'merchandiser' ? choiceField(label, key, project?.[key], MERCHANDISERS) : field(label, key, project?.[key], config)).wrapper);
  body.append(grid);
  const source = el('section', {class: 'form-section'}); source.append(el('h3', {}, '生产单留存'));
  const area = el('div', {class: 'upload-area'}), label = el('label', {}, '上传生产单（单个文件最大10MB）'), input = el('input', {type: 'file', accept: '.pdf,.doc,.docx,.xls,.xlsx,.csv,.png,.jpg,.jpeg', 'aria-label': '上传生产单'}), status = el('p', {class: 'upload-status', 'aria-live': 'polite'});
  state.editor.attachment = typeof project?.source_attachment === 'object' ? project.source_attachment.id : (project?.source_attachment || '');
  label.append(input); area.append(label, el('p', {class: 'field-help'}, '上传仅留存原件，字段仍需人工录入。自动识别待真实样本接入。'), status); source.append(area); body.append(source);
  function showAttachment(id, filename) { clear(status); status.append(document.createTextNode(`${filename || '生产单'} · 已留存 `), el('a', {href: '/api/lantern/orders/' + encodeURIComponent(id), download: ''}, '下载原件')); }
  if (state.editor.attachment) showAttachment(state.editor.attachment);
  input.addEventListener('change', async () => {
    const file = input.files[0]; if (!file) return;
    if (file.size > 10 * 1024 * 1024) { status.textContent = '文件超过10MB，请选择较小的文件。'; input.value = ''; return; }
    if (!/\.(pdf|docx?|xlsx?|csv|png|jpe?g)$/i.test(file.name)) { status.textContent = '文件格式不支持，请选择PDF、Office文件、CSV或图片。'; input.value = ''; return; }
    const currentEditor = state.editor; state.uploading = true; $('save-editor').disabled = true; input.disabled = true; status.textContent = '正在上传并留存，请稍候…';
    try {
      const encoded = await new Promise((resolve, reject) => { const reader = new FileReader(); reader.onload = () => resolve(String(reader.result).split(',')[1]); reader.onerror = () => reject(new Error('文件读取失败')); reader.readAsDataURL(file); });
      const result = await api('/api/lantern/orders/upload', {method: 'POST', body: {filename: file.name, content_base64: encoded}});
      if (state.editor !== currentEditor) return;
      if (!result.attachment?.id) throw new Error('上传结果缺少附件编号，请重新上传。');
      currentEditor.attachment = result.attachment.id; state.dirty = true; showAttachment(result.attachment.id, result.attachment.filename || file.name);
    } catch (error) { if (state.editor === currentEditor) status.textContent = `上传失败：${error.message}`; }
    finally { if (state.editor === currentEditor) { state.uploading = false; $('save-editor').disabled = false; input.disabled = false; } }
  });
  const section = el('section', {class: 'form-section'}), heading = el('div', {class: 'section-heading'}), add = el('button', {type: 'button'}, '＋ 添加产品'), products = el('div', {id: 'product-editor-list'});
  heading.append(el('h3', {}, '产品与四工序'), add); section.append(heading, el('p', {class: 'field-help'}, '标记工序已完成时，填写实际完成结果、复验或确认记录。日期及责任人不明确时留空。'), products); body.append(section);
  for (const product of project?.products || [{}]) addProduct(products, product);
  add.addEventListener('click', () => { addProduct(products); state.dirty = true; products.lastElementChild.querySelector('input').focus(); });
}
function readProjectForm() {
  const result = {};
  for (const key of ['name', 'order_no', 'country', 'voltage', 'completion_date', 'salesperson', 'merchandiser', 'notes']) result[key] = $('editor-form').elements.namedItem(key).value.trim();
  result.source_attachment = state.editor.attachment || '';
  result.products = [...$('product-editor-list').children].map((block, index) => {
    const product = {name: block.querySelector('[name=product_name]').value.trim(), specification: block.querySelector('[name=product_specification]').value.trim(), quantity: block.querySelector('[name=product_quantity]').value.trim(), stages: {}};
    if (!product.name) throw new Error(`请填写产品 ${index + 1} 的名称。`);
    if (product.quantity && (!/^\d+(?:\.\d+)?$/.test(product.quantity) || Number(product.quantity) > 1000000)) throw new Error(`产品 ${index + 1} 的数量须为不大于1000000的非负数，未知时留空。`);
    if (block._productId !== undefined) product.id = block._productId;
    for (const stage of block.querySelectorAll('[data-stage]')) {
      const status = stage.querySelector('select').value, note = stage.querySelector('textarea').value.trim();
      if (status === '已完成' && !note) throw new Error(`产品 ${index + 1} 的「${stage.dataset.stage}」已完成，请填写完成依据。`);
      product.stages[stage.dataset.stage] = {status, note};
    }
    return product;
  });
  if (!result.name) throw new Error('请填写项目名称。');
  return result;
}
function openShipment(shipment = null) {
  if (!shipment && !canEdit()) return;
  const readOnly = !canEdit(); openEditor('shipment', readOnly ? '出货记录详情' : (shipment ? '编辑出货记录' : '新增出货记录'), 'SHIPPING DETAILS', shipment, readOnly);
  const body = $('editor-body'), grid = el('div', {class: 'form-grid'});
  grid.append(field('关联订单号（可选）', 'order_no', shipment?.order_no, {max: 120}).wrapper);
  const projectLabel = el('label', {}, '关联项目（手动选择）'), select = el('select', {name: 'project_id'}); select.append(el('option', {value: ''}, '不关联项目'));
  for (const project of state.data.projects) select.append(el('option', {value: project.id}, `${text(project.name)}${project.order_no ? ' · ' + project.order_no : ''}`));
  if (shipment?.project_id && !state.data.projects.some(project => String(project.id) === String(shipment.project_id))) select.append(el('option', {value: shipment.project_id}, '原关联项目（当前不可见）'));
  select.value = shipment?.project_id || ''; projectLabel.append(select); grid.append(projectLabel, el('p', {class: 'field-help wide'}, '关联仅按实际订单选择，不会依据客户、国家或名称自动推测。21项原始台账字段完整保留。'));
  for (const [key, label, type = 'text'] of SHIPMENT_FIELDS) {
    const item = SHIPMENT_CHOICES[key] ? choiceField(label, key, shipment?.[key], SHIPMENT_CHOICES[key]) : field(label, key, shipment?.[key], {type, max: ['notes', 'documents'].includes(key) ? 4000 : 300, wide: type === 'textarea'});
    if (['customer', 'country', 'shipping_line', 'forwarder', 'route', 'salesperson', 'document_owner'].includes(key)) {
      const listId = `shipment-options-${key}`, suggestions = el('datalist', {id: listId});
      for (const value of unique(state.data.shipments.map(record => record[key]))) suggestions.append(el('option', {value}));
      item.input.setAttribute('list', listId); item.wrapper.append(suggestions);
    }
    grid.append(item.wrapper);
  }
  body.append(grid);
  if (readOnly) body.querySelectorAll('input,select,textarea').forEach(input => { input.disabled = true; });
}
function openPassword() {
  openEditor('password', '修改个人密码', 'ACCOUNT SECURITY');
  const grid = el('div', {class: 'form-grid'});
  for (const [label, name] of [['当前密码', 'old_password'], ['新密码（至少10个字符）', 'new_password'], ['再次输入新密码', 'confirm_password']]) {
    const item = field(label, name, '', {type: 'password', required: true, max: 120, wide: true}); item.input.autocomplete = name === 'old_password' ? 'current-password' : 'new-password'; if (name !== 'old_password') item.input.minLength = 10; grid.append(item.wrapper);
  }
  $('editor-body').append(grid); $('editor-hint').textContent = '修改成功后需要使用新密码重新登录。';
}
async function submitEditor(event) {
  event.preventDefault(); if (!state.editor || state.editor.readOnly || state.saving || state.uploading) return;
  editorError(); const current = state.editor; state.saving = true; $('save-editor').disabled = true; $('save-editor').textContent = '正在保存…';
  try {
    let result, message;
    if (current.kind === 'password') {
      const values = Object.fromEntries(new FormData($('editor-form')));
      if (values.new_password !== values.confirm_password) throw new Error('两次输入的新密码不一致。');
      await api('/api/password', {method: 'POST', body: {old_password: values.old_password, new_password: values.new_password}});
      closeEditor(true); showLogin('密码已修改，请使用新密码重新登录。'); return;
    }
    if (current.kind === 'project') {
      const payload = readProjectForm(); if (current.original) payload.revision = current.original.revision;
      result = await api('/api/lantern/projects' + (current.original ? '/' + encodeURIComponent(current.original.id) : ''), {method: current.original ? 'PATCH' : 'POST', body: payload});
      if (result.project?.id) state.selected = result.project.id; message = '项目已保存。';
    } else {
      const payload = {};
      for (const key of ['order_no', 'project_id', ...SHIPMENT_FIELDS.map(([key]) => key)]) payload[key] = $('editor-form').elements.namedItem(key).value.trim();
      if (current.original) payload.revision = current.original.revision;
      await api('/api/lantern/shipments' + (current.original ? '/' + encodeURIComponent(current.original.id) : ''), {method: current.original ? 'PATCH' : 'POST', body: payload}); message = '出货记录已保存。';
    }
    if (state.editor !== current) return;
    closeEditor(true); toast(message); await loadData();
  } catch (error) {
    if (state.editor !== current) return;
    editorError(error.status === 409 ? '其他同事已更新这条记录，本次没有覆盖对方修改。你的输入仍保留在本窗口；请先复制需要保留的内容，再关闭、刷新并重新编辑。' : error.message);
  } finally {
    state.saving = false; $('save-editor').disabled = false; $('save-editor').textContent = '保存';
  }
}
async function downloadCsv(kind, format = 'csv') {
  const button = $(`export-${kind}${format === 'xlsx' ? '-xlsx' : ''}`), original = button.textContent, epoch = state.epoch;
  button.disabled = true; button.textContent = '正在导出…';
  try {
    const response = await fetch(`/api/lantern/${kind}.${format}`, {credentials: 'same-origin'});
    if (epoch !== state.epoch) return;
    if (response.status === 401) { showLogin('登录已过期，请重新登录后导出。'); return; }
    if (!response.ok) { const error = await response.json().catch(() => ({})); throw new Error(typeof error.error === 'string' ? error.error : '导出失败，请稍后重试。'); }
    const expected = format === 'xlsx' ? 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet' : 'text/csv';
    if (!String(response.headers.get('Content-Type')).startsWith(expected)) throw new Error('导出格式异常，请刷新后重试。');
    const blob = await response.blob(); if (!blob.size) throw new Error('导出文件为空。'); if (epoch !== state.epoch) return;
    let filename = `彩灯_${kind === 'projects' ? '项目' : '出货台账'}_V${VERSION}.${format}`;
    const match = /filename\*=UTF-8''([^;]+)/i.exec(response.headers.get('Content-Disposition') || '');
    if (match) { try { filename = decodeURIComponent(match[1]).replace(/[\\/]/g, '_'); } catch (_) { /* 使用默认文件名 */ } }
    const url = URL.createObjectURL(blob), link = el('a', {href: url, download: filename}); document.body.append(link); link.click(); link.remove(); setTimeout(() => URL.revokeObjectURL(url), 1000); toast(`${format === 'xlsx' ? 'Excel' : 'CSV'}已导出，包含当前账号可访问的全部记录。`);
  } catch (error) { if (epoch === state.epoch) showError(error.message); }
  finally { button.disabled = false; button.textContent = original; }
}
async function loadUsers() {
  if (!isAdmin()) return; const epoch = state.epoch;
  try {
    const result = await api('/api/users'); if (epoch !== state.epoch || !isAdmin()) return;
    const list = clear($('user-list'));
    for (const user of result.users || []) {
      const row = el('div', {class: 'user-row'}), identity = el('div');
      const active = user.active !== false && user.active !== 0;
      identity.append(el('strong', {}, text(user.display_name, user.username)), el('p', {}, `${text(user.username)} · ${roleLabel(user.role)} · ${active ? '已启用' : '已停用'}${user.must_change_password ? ' · 首次改密待完成' : ''}`)); row.append(identity);
      if (user.role !== 'admin') {
        const actions = el('div', {class: 'user-actions'}), role = el('select', {'aria-label': `${text(user.display_name, user.username)}的权限`});
        role.append(el('option', {value: 'viewer'}, '仅查看'), el('option', {value: 'editor'}, '编辑项目和出货')); role.value = user.role;
        const id = encodeURIComponent(user.id ?? user.username);
        role.addEventListener('change', async () => { role.disabled = true; try { await api('/api/users/' + id, {method: 'PATCH', body: {role: role.value}}); toast('账号权限已更新。'); await loadUsers(); } catch (error) { role.value = user.role; showError(error.message); } finally { role.disabled = false; } });
        const toggle = el('button', {type: 'button'}, active ? '停用' : '启用');
        toggle.addEventListener('click', async () => { if (!confirm(`${active ? '停用' : '启用'} ${text(user.display_name, user.username)} 的账号？`)) return; toggle.disabled = true; try { await api('/api/users/' + id, {method: 'PATCH', body: {active: !active}}); await loadUsers(); } catch (error) { showError(error.message); } finally { toggle.disabled = false; } });
        const reset = el('button', {type: 'button'}, '重置密码');
        reset.addEventListener('click', async () => { if (!confirm(`重置 ${text(user.display_name, user.username)} 的密码？其原登录会失效，需使用新初始密码重新登录。`)) return; reset.disabled = true; try { const result = await api('/api/users/' + id + '/reset-password', {method: 'POST', body: {}}); showSecret({...user, ...result}); await loadUsers(); } catch (error) { showError(error.message); } finally { reset.disabled = false; } });
        actions.append(role, toggle, reset); row.append(actions);
      } else row.append(el('span', {class: 'small muted'}, '管理员账号'));
      list.append(row);
    }
    if (!list.children.length) list.append(emptyState('暂无账号记录', '当前没有可显示的账号。'));
  } catch (error) { if (epoch === state.epoch && error.status !== 401) showError(error.message); }
}
function showSecret(result) {
  const box = clear($('new-user-secret')); box.hidden = false;
  box.append(el('strong', {}, '初始密码仅本次显示，请单独交给对应人员。'), el('p', {}, `${text(result.display_name, result.user?.display_name || '')} · 账号 ${text(result.username, result.user?.username || '')}`));
  const password = el('input', {readonly: '', 'aria-label': '本次生成的初始密码', autocomplete: 'off'}); password.value = result.initial_password || ''; password.addEventListener('focus', () => password.select());
  const dismiss = el('button', {type: 'button'}, '已记录，隐藏密码'); dismiss.addEventListener('click', () => { clear(box); box.hidden = true; });
  box.append(password, el('span', {class: 'small muted'}, '首次登录必须修改密码。离开此页后隐藏；遗失时请重新生成。'), dismiss); box.scrollIntoView({block: 'nearest'});
}
async function logout() {
  try { await api('/api/logout', {method: 'POST', body: {}}); showLogin(); }
  catch (error) { if (error.status !== 401) { if (state.user?.must_change_password) $('first-password-error').textContent = error.message; else showError(error.message); } }
}
$('login-form').addEventListener('submit', async event => {
  event.preventDefault(); const form = event.currentTarget, button = form.querySelector('button[type=submit]'); button.disabled = true; $('login-error').textContent = '';
  try { const result = await api('/api/login', {method: 'POST', body: Object.fromEntries(new FormData(form))}); state.csrf = result.csrf || ''; form.elements.password.value = ''; await bootstrap(); }
  catch (error) { $('login-error').textContent = error.message; } finally { button.disabled = false; }
});
$('first-password-form').addEventListener('submit', async event => {
  event.preventDefault(); const form = event.currentTarget, values = Object.fromEntries(new FormData(form)), button = form.querySelector('button[type=submit]'); $('first-password-error').textContent = '';
  if (values.new_password !== values.confirm_password) { $('first-password-error').textContent = '两次输入的新密码不一致。'; return; }
  button.disabled = true;
  try { await api('/api/password', {method: 'POST', body: {old_password: values.old_password, new_password: values.new_password}}); form.reset(); showLogin('个人密码已设置，请使用新密码登录。'); }
  catch (error) { $('first-password-error').textContent = error.message; } finally { button.disabled = false; }
});
$('account-form').addEventListener('submit', async event => {
  event.preventDefault(); const form = event.currentTarget, button = form.querySelector('button[type=submit]'); $('account-error').textContent = ''; button.disabled = true;
  const values = Object.fromEntries(new FormData(form)); values.username = values.username.trim(); values.display_name = values.display_name.trim();
  try { if (!values.display_name) throw new Error('请填写姓名。'); const result = await api('/api/users', {method: 'POST', body: values}); showSecret({...values, ...result}); form.reset(); await loadUsers(); }
  catch (error) { $('account-error').textContent = error.message; } finally { button.disabled = false; }
});
$('logout-button').addEventListener('click', logout); $('first-password-logout').addEventListener('click', logout);
$('password-button').addEventListener('click', openPassword); $('refresh-button').addEventListener('click', () => loadData());
$('new-project').addEventListener('click', () => openProject()); $('new-shipment').addEventListener('click', () => openShipment());
$('export-projects').addEventListener('click', () => downloadCsv('projects')); $('export-shipments').addEventListener('click', () => downloadCsv('shipments'));
$('export-projects-xlsx').addEventListener('click', () => downloadCsv('projects', 'xlsx')); $('export-shipments-xlsx').addEventListener('click', () => downloadCsv('shipments', 'xlsx'));
$('reload-users').addEventListener('click', loadUsers);
document.querySelectorAll('[data-page]').forEach(button => button.addEventListener('click', () => setPage(button.dataset.page)));
for (const id of ['merchandiser-filter', 'salesperson-filter', 'country-filter']) $(id).addEventListener('change', renderProjects);
$('project-search').addEventListener('input', renderProjects);
$('clear-project-filters').addEventListener('click', () => { for (const id of ['project-search', 'merchandiser-filter', 'salesperson-filter', 'country-filter']) $(id).value = ''; renderProjects(); });
$('shipment-search').addEventListener('input', renderShipments); $('shipment-status').addEventListener('change', renderShipments);
$('close-editor').addEventListener('click', () => closeEditor()); $('cancel-editor').addEventListener('click', () => closeEditor());
$('editor-dialog').addEventListener('cancel', event => { event.preventDefault(); closeEditor(); });
$('editor-form').addEventListener('input', () => { state.dirty = true; }); $('editor-form').addEventListener('change', () => { state.dirty = true; });
$('editor-form').addEventListener('submit', submitEditor);
window.addEventListener('beforeunload', event => { if (state.editor && state.dirty) { event.preventDefault(); event.returnValue = ''; } });
setInterval(() => { if (!document.hidden && state.user && !state.user.must_change_password && !state.editor) loadData({quiet: true}); }, 30000);
document.addEventListener('visibilitychange', () => { if (!document.hidden && state.user && !state.editor && (!state.lastRefresh || Date.now() - state.lastRefresh.getTime() > 30000)) loadData({quiet: true}); });
bootstrap();
