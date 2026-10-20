'use strict';
// 只绑定已授权的业务编辑表单；登录、密码、令牌表单不进入草稿系统。
const businessDrafts=FollowupDrafts.create({storage:(()=>{try{return window.sessionStorage;}catch(_error){return null;}})()});
const draftControllers=new WeakMap();
let businessDraftUser='',businessDraftEpoch=0;
function setBusinessDraftUser(username){if(businessDraftUser!==username){businessDraftEpoch++;businessDraftUser=username;businessDrafts.setUser(username);}}
function clearBusinessDraftUser(){businessDraftEpoch++;businessDraftUser='';businessDrafts.clearAll();}
const draftFieldNames=new Set(['owner','deadline','status','closure_note','closure_scope','progress_note','state','basis','note','source_ref','title','order_no','business_contact','received_on','piece_label','product_name']);
function draftFields(form){return [...form.querySelectorAll('input[name],select[name],textarea[name]')].filter(input=>!input.disabled&&draftFieldNames.has(input.name));}
function draftSnapshot(form){return Object.fromEntries(draftFields(form).map(input=>[input.name,input.value]));}
function trackDraft(form,key,feedback){
 let baseline=draftSnapshot(form);const epoch=businessDraftEpoch;form.dataset.draftKey=key;
 const notice=node('div',{class:'draft-notice',hidden:''}),message=node('p',{class:'small',role:'status','aria-live':'polite'}),actions=node('div',{class:'draft-actions'});
 const discard=node('button',{type:'button'},'放弃草稿'),acknowledge=node('button',{type:'button'},'已核对，保留草稿');
 actions.append(acknowledge,discard);notice.append(message,actions);form.prepend(notice);
 let checked=false,restored=false;
 const display=()=>{
  const draft=businessDrafts.read(key,baseline);notice.hidden=!draft;form.dataset.draftActive=String(Boolean(draft));
  if(!draft){if(feedback?.dataset.draftNotice==='true'){feedback.textContent='';delete feedback.dataset.draftNotice;}return;}
  acknowledge.hidden=!draft.conflict||checked;
  message.textContent=draft.conflict&&!checked?'这条记录已更新，已恢复你的未提交草稿。请对照上方最新记录，核对后再保存。':!businessDrafts.persistent?'草稿暂存在当前页面；浏览器暂存不可用，刷新前请先保存。尚未提交到台账。':restored?'已恢复本标签页草稿，尚未提交到台账。':'草稿已暂存在本标签页，尚未提交到台账。';
  if(feedback){feedback.dataset.draftNotice='true';feedback.textContent=draft.conflict&&!checked?'记录已更新，请先核对草稿。':!businessDrafts.persistent?'草稿仅在当前页面，刷新前请先保存。':restored?'已恢复草稿，尚未保存。':'草稿已暂存，尚未保存。';}
 };
 const restoredDraft=businessDrafts.read(key,baseline);
 if(restoredDraft){for(const input of draftFields(form))if(Object.hasOwn(restoredDraft.values,input.name))input.value=restoredDraft.values[input.name];restored=true;}
 const current=()=>epoch===businessDraftEpoch&&Boolean(businessDraftUser);
 const capture=()=>{if(!current())return;businessDrafts.capture(key,baseline,draftSnapshot(form));display();};
 form.addEventListener('input',capture);form.addEventListener('change',capture);
 form.addEventListener('submit',event=>{if(!current()){event.preventDefault();event.stopImmediatePropagation();return;}capture();const draft=businessDrafts.read(key,baseline);if(draft?.conflict&&!checked){event.preventDefault();event.stopImmediatePropagation();acknowledge.focus();}},true);
 acknowledge.addEventListener('click',()=>{checked=true;display();});
 discard.addEventListener('click',()=>{if(!confirm('放弃这份未提交草稿，恢复当前记录的值？'))return;businessDrafts.clear(key);for(const input of draftFields(form))input.value=baseline[input.name]??'';checked=false;restored=false;display();});
 const controller={capture,current,ack(submitted,reset){if(!current())return;businessDrafts.ack(key,submitted,reset);baseline={...submitted,...reset};for(const input of draftFields(form))if(input.value===submitted[input.name])input.value=baseline[input.name]??'';restored=false;checked=false;display();}};
 draftControllers.set(form,controller);display();return controller;
}
function draftRequestCurrent(form){return draftControllers.get(form)?.current()??true;}
function acknowledgeDraft(form,submitted,reset={}){draftControllers.get(form)?.ack(submitted,reset);}
function openDraftEditors(container){for(const form of container.querySelectorAll('form[data-draft-active="true"]')){for(let parent=form.parentElement;parent&&parent!==container;parent=parent.parentElement)if(parent.tagName==='DETAILS')parent.open=true;}}
function rememberDraftDetails(container){return new Set([...container.querySelectorAll('details[open]')].map(detail=>detail.dataset.draftView||detail.dataset.pieceId||detail.dataset.orderId||detail.dataset.caseId).filter(Boolean));}
function restoreDraftDetails(container,open){for(const detail of container.querySelectorAll('details'))if(open.has(detail.dataset.draftView||detail.dataset.pieceId||detail.dataset.orderId||detail.dataset.caseId))detail.open=true;openDraftEditors(container);}
function pruneBusinessDrafts(){
 if(!state.user||!state.snapshot||!state.production)return;
 const keys=[];
 for(const item of state.snapshot.items||[])if(canEditItem(item))keys.push('item:'+item.id);
 for(const item of state.snapshot.after_sales?.cases||[])if(canEditItem(item))keys.push('after-sales:'+item.id);
 for(const order of state.production.orders||[]){
  if(state.user.role==='admin')keys.push('order:'+order.id);
  for(const piece of order.pieces||[]){
   if(state.user.role==='admin')keys.push('piece:'+piece.id);
   if(canEditPiece(piece)){keys.push('delivery:'+piece.id);for(const stage of piece.stages||[])keys.push('stage:'+piece.id+':'+stage.stage);}
  }
 }
 businessDrafts.retain(keys);
}
window.addEventListener('beforeunload',event=>{if(businessDrafts.hasDrafts()){event.preventDefault();event.returnValue='';}});
