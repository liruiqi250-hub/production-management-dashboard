'use strict';

const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const test=require('node:test');
const vm=require('node:vm');
const source=fs.readFileSync(path.join(__dirname,'..','web','drafts.js'),'utf8');
const KEY='production-dashboard-followup-drafts-v1';
const plain=value=>value===null?null:JSON.parse(JSON.stringify(value));
function storage(initial={}){
 const data=new Map(Object.entries(initial));
 return {getItem:key=>data.has(key)?data.get(key):null,setItem:(key,value)=>data.set(key,String(value)),removeItem:key=>data.delete(key),data};
}
function create(options={}){
 const context={window:{}};vm.runInNewContext(source,context,{filename:'web/drafts.js',timeout:1000});
 return context.window.FollowupDrafts.create(options);
}
const baseline={status:'待处理',owner:'小李',closure_note:''};

test('输入只保存字段差异；同一账号刷新后恢复，不改当前服务端其他字段',()=>{
 const tab=storage(),engine=create({storage:tab});engine.setUser('user-a');
 engine.capture('item:1',baseline,{...baseline,closure_note:'已联系工厂，等待回件'});
 assert.deepEqual(plain(JSON.parse(tab.data.get(KEY)).entries['item:1'].changes),{closure_note:'已联系工厂，等待回件'});
 const refreshed=create({storage:tab});refreshed.setUser('user-a');
 assert.deepEqual(plain(refreshed.read('item:1',{...baseline,owner:'小王'})),{
  values:{status:'待处理',owner:'小王',closure_note:'已联系工厂，等待回件'},conflict:true,changed:['closure_note'],
 });
 assert.equal(refreshed.hasDrafts(),true);
});

test('新输入以当前基线比较，保留最初基线提醒跨刷新冲突',()=>{
 const tab=storage(),engine=create({storage:tab});engine.setUser('user-a');
 engine.capture('item:1',baseline,{...baseline,closure_note:'初稿'});
 const updated={...baseline,status:'处理中'};
 const restored=engine.read('item:1',updated);assert.equal(restored.conflict,true);
 engine.capture('item:1',updated,{...restored.values,closure_note:'补充说明'});
 assert.deepEqual(plain(JSON.parse(tab.data.get(KEY)).entries['item:1'].changes),{closure_note:'补充说明'});
 assert.equal(engine.read('item:1',updated).conflict,true);
 const refreshed=create({storage:tab});refreshed.setUser('user-a');
 assert.equal(refreshed.read('item:1',updated).conflict,true);
});

test('账号切换、退出登录、重新进入旧账号都不会串稿，其他存储不受影响',()=>{
 const tab=storage({other:'keep'}),engine=create({storage:tab});engine.setUser('user-a');
 engine.capture('item:1',baseline,{...baseline,closure_note:'甲的草稿'});
 engine.setUser('user-b');assert.equal(engine.hasDrafts(),false);assert.equal(engine.read('item:1',baseline),null);
 engine.setUser('user-a');assert.equal(engine.read('item:1',baseline),null);
 engine.capture('item:1',baseline,{...baseline,closure_note:'新草稿'});engine.setUser('');
 assert.equal(engine.hasDrafts(),false);assert.equal(tab.data.has(KEY),false);assert.equal(tab.data.get('other'),'keep');
});

test('首次加载发现存储属于别的账号时清除，不能恢复别人的字段',()=>{
 const tab=storage(),first=create({storage:tab});first.setUser('user-a');first.capture('item:1',baseline,{...baseline,closure_note:'仅供甲'});
 const next=create({storage:tab});next.setUser('user-b');assert.equal(next.hasDrafts(),false);assert.equal(tab.data.has(KEY),false);
});

test('改回当前服务端值会清除草稿；提交成功只清对应记录',()=>{
 const engine=create({storage:storage()});engine.setUser('user-a');
 engine.capture('item:1',baseline,{...baseline,closure_note:'事项一'});engine.capture('item:2',baseline,{...baseline,closure_note:'事项二'});
 engine.capture('item:1',baseline,baseline);assert.equal(engine.read('item:1',baseline),null);assert.equal(engine.hasDrafts(),true);
 engine.ack('item:2',{...baseline,closure_note:'事项二'});assert.equal(engine.hasDrafts(),false);
});

test('提交期间继续输入会保留，已提交字段不会重新成为草稿',()=>{
 const engine=create({storage:storage()});engine.setUser('user-a');
 const submitted={...baseline,status:'处理中',closure_note:'已电话联系'};
 engine.capture('item:1',baseline,submitted);
 engine.capture('item:1',baseline,{...submitted,closure_note:'已电话联系；继续等待配件'});
 const remaining=engine.ack('item:1',submitted,{closure_note:''});
 assert.deepEqual(plain(remaining),{values:{status:'处理中',owner:'小李',closure_note:'已电话联系；继续等待配件'},conflict:false,changed:['closure_note']});
 assert.equal(engine.read('item:1',{...baseline,status:'处理中'}).conflict,false);
});

test('提交期间把字段改回提交前的原值仍会保留为新草稿',()=>{
 const engine=create({storage:storage()});engine.setUser('user-a');
 const submitted={...baseline,status:'处理中'};
 engine.capture('item:1',baseline,submitted);engine.capture('item:1',baseline,baseline);
 assert.equal(engine.hasDrafts(),false);
 const remaining=engine.ack('item:1',submitted);
 assert.deepEqual(plain(remaining),{values:baseline,conflict:false,changed:['status']});
 assert.equal(engine.read('item:1',submitted).values.status,'待处理');
});

test('显式放弃可清除内存中的回改快照，响应到达后不会复活',()=>{
 const engine=create({storage:storage()});engine.setUser('user-a');
 const submitted={...baseline,status:'处理中'};
 engine.capture('item:1',baseline,submitted);engine.capture('item:1',baseline,baseline);engine.clear('item:1');
 assert.equal(engine.ack('item:1',submitted),null);assert.equal(engine.hasDrafts(),false);
});

test('刷新恢复后直接提交，不会把未修改的服务端新值变回旧值',()=>{
 const tab=storage(),first=create({storage:tab});first.setUser('user-a');
 first.capture('item:1',baseline,{...baseline,closure_note:'本地说明'});
 const restored=create({storage:tab});restored.setUser('user-a');
 const visible=restored.read('item:1',{...baseline,owner:'小王'}).values;
 restored.ack('item:1',visible,{closure_note:''});
 assert.equal(restored.hasDrafts(),false);assert.equal(restored.read('item:1',{...baseline,owner:'小王'}),null);
});

test('后台重绘后未再输入就提交，也不会恢复未改字段的旧值',()=>{
 const engine=create({storage:storage()});engine.setUser('user-a');
 engine.capture('item:1',baseline,{...baseline,closure_note:'本地说明'});
 const visible=engine.read('item:1',{...baseline,owner:'小王'}).values;
 engine.ack('item:1',visible,{closure_note:''});assert.equal(engine.hasDrafts(),false);
});

test('恢复只应用当前表单仍存在的字段，不再有权限的记录可整体剔除',()=>{
 const engine=create({storage:storage()});engine.setUser('user-a');
 engine.capture('item:1',baseline,{...baseline,owner:'小王',closure_note:'保留说明'});
 engine.capture('item:2',baseline,{...baseline,closure_note:'已撤回权限'});
 assert.deepEqual(plain(engine.read('item:1',{status:'待处理',closure_note:''})),{values:{status:'待处理',closure_note:'保留说明'},conflict:false,changed:['closure_note']});
 engine.retain(new Set(['item:1']));assert.equal(engine.read('item:2',baseline),null);
 engine.retain([]);assert.equal(engine.hasDrafts(),false);
});

test('服务端已经包含全部草稿值时不再提示未保存',()=>{
 const engine=create({storage:storage()});engine.setUser('user-a');
 const saved={...baseline,closure_note:'相同说明'};engine.capture('item:1',baseline,saved);
 assert.equal(engine.read('item:1',saved),null);assert.equal(engine.hasDrafts(),false);
});

test('服务端更新后用户改回最初原值，刷新仍能恢复这份明确修改',()=>{
 const tab=storage(),engine=create({storage:tab});engine.setUser('user-a');
 engine.capture('item:1',baseline,{...baseline,status:'处理中'});
 const updated={...baseline,status:'已关闭'};
 engine.capture('item:1',updated,baseline);
 const restored=create({storage:tab});restored.setUser('user-a');
 assert.equal(restored.read('item:1',updated).values.status,'待处理');assert.equal(restored.read('item:1',updated).conflict,true);
});

test('禁止密码、令牌、原型污染及非白名单字段写入；只接纳安全标量',()=>{
 const tab=storage(),engine=create({storage:tab});engine.setUser('user-a');
 const malicious=JSON.parse('{"note":"","password":"","api_token":"","__proto__":"","constructor":"","nested":{},"bad-number":0}');
 const changed=JSON.parse('{"note":"安全说明","password":"秘密密码","api_token":"秘密令牌","__proto__":"污染","constructor":"污染","nested":{"note":"嵌套"},"bad-number":1,"extra":"未授权字段"}');
 engine.capture('item:1',malicious,changed);
 const saved=tab.data.get(KEY);assert.doesNotMatch(saved,/秘密|污染|nested|extra|password|api_token|constructor|__proto__/);
 assert.deepEqual(plain(engine.read('item:1',malicious).values),{note:'安全说明'});assert.equal({}.polluted,undefined);
 for(const key of ['__proto__','constructor','prototype','x\nsecret',''])assert.equal(engine.capture(key,baseline,{...baseline,closure_note:'禁止'}),null);
});

test('存储不可用时保留当前页面的内存草稿并明确 persistent=false',()=>{
 const unavailable={getItem(){throw Error('blocked');},setItem(){throw Error('blocked');},removeItem(){throw Error('blocked');}};
 const engine=create({storage:unavailable});engine.setUser('user-a');engine.capture('item:1',baseline,{...baseline,closure_note:'仍在内存'});
 assert.equal(engine.persistent,false);assert.equal(engine.hasDrafts(),true);assert.equal(engine.read('item:1',baseline).values.closure_note,'仍在内存');
});

test('允许写但不允许读取的存储仍提示不能跨刷新可靠恢复',()=>{
 const tab=storage();tab.getItem=()=>{throw Error('read blocked');};
 const engine=create({storage:tab});engine.setUser('user-a');engine.capture('item:1',baseline,{...baseline,closure_note:'仍可输入'});
 assert.equal(engine.persistent,false);assert.equal(engine.hasDrafts(),true);
});

test('写入额度不足时不丢内存草稿，恢复写入后可继续持久保护',()=>{
 const tab=storage();let blocked=true;const write=tab.setItem;tab.setItem=(...args)=>{if(blocked)throw Error('quota');return write(...args);};
 const engine=create({storage:tab});engine.setUser('user-a');engine.capture('item:1',baseline,{...baseline,closure_note:'第一稿'});
 assert.equal(engine.persistent,false);assert.equal(engine.hasDrafts(),true);
 blocked=false;engine.capture('item:1',baseline,{...baseline,closure_note:'第二稿'});assert.equal(engine.persistent,true);
 assert.equal(JSON.parse(tab.data.get(KEY)).entries['item:1'].changes.closure_note,'第二稿');
});

test('超过 24 小时的草稿在内存和刷新恢复时均过期',()=>{
 let clock=1000;const tab=storage(),engine=create({storage:tab,now:()=>clock});engine.setUser('user-a');engine.capture('item:1',baseline,{...baseline,closure_note:'旧稿'});
 const stored=tab.data.get(KEY);clock+=24*60*60*1000+1;
 assert.equal(engine.hasDrafts(),false);assert.equal(tab.data.has(KEY),false);
 const refreshed=create({storage:storage({[KEY]:stored}),now:()=>clock});refreshed.setUser('user-a');assert.equal(refreshed.hasDrafts(),false);
});

test('畸形、未来时间、超长内容、错误格式不会恢复或影响其他存储',()=>{
 for(const payload of ['not-json','[]','{"version":9,"user":"user-a","entries":{}}']){
  const tab=storage({[KEY]:payload,other:'keep'}),engine=create({storage:tab});assert.doesNotThrow(()=>engine.setUser('user-a'));assert.equal(engine.hasDrafts(),false);assert.equal(tab.data.get('other'),'keep');
 }
 const tab=storage(),engine=create({storage:tab,now:()=>1000});engine.setUser('user-a');
 engine.capture('item:1',{note:''},{note:'x'.repeat(8193)});assert.equal(engine.hasDrafts(),false);
 tab.data.set(KEY,JSON.stringify({version:1,user:'user-a',entries:{future:{baseline:{note:''},changes:{note:'未来'},updatedAt:1000+6*60*1000}}}));
 const refreshed=create({storage:tab,now:()=>1000});refreshed.setUser('user-a');assert.equal(refreshed.hasDrafts(),false);
});

test('数量和存储体积有上限，优先保留最近输入',()=>{
 let clock=0;const tab=storage(),engine=create({storage:tab,now:()=>++clock});engine.setUser('user-a');
 for(let i=0;i<110;i++)engine.capture('item:'+i,{note:''},{note:'草稿 '+i});
 const saved=JSON.parse(tab.data.get(KEY));assert.equal(Object.keys(saved.entries).length,100);
 assert.equal(engine.read('item:0',{note:''}),null);assert.equal(engine.read('item:109',{note:''}).values.note,'草稿 109');
 for(let i=0;i<100;i++)engine.capture('large:'+i,{note:''},{note:'中'.repeat(8192)});
 assert.ok(tab.data.get(KEY).length<=256*1024);assert.equal(engine.read('large:99',{note:''}).values.note.length,8192);
});

test('无账号时不保护表单，空值、布尔值与数字能够准确比较',()=>{
 const engine=create({storage:storage()});assert.equal(engine.capture('item:1',baseline,{...baseline,closure_note:'未登录'}),null);
 engine.setUser('user-a');engine.capture('item:1',{note:null,count:1,checked:false},{note:'',count:0,checked:true});
 assert.deepEqual(plain(engine.read('item:1',{note:null,count:1,checked:false}).values),{note:'',count:0,checked:true});engine.clearAll();assert.equal(engine.hasDrafts(),false);
});
