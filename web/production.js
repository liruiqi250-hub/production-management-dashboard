'use strict';
// 生产进度是人工确认的主档；聊天证据通过明确关联进入，不按关键词自动改状态。
function productionSelect(name, choices, selected){
 const select=node('select',{name});
 for(const choice of choices){const value=Array.isArray(choice)?choice[0]:choice,label=Array.isArray(choice)?choice[1]:choice;select.append(node('option',{value},label));}
 if(selected!==undefined)select.value=selected;
 return select;
}
function productionField(label,control){const field=node('label',{},label);field.append(control);return field;}
function productionFeedback(){return node('p',{class:'small production-feedback',role:'status','aria-live':'polite'});}
function masterEditor(kind,record){
 const order=kind==='order',editor=node('details',{class:'production-master-editor'});
 editor.append(node('summary',{},order?'修改生产单资料':'修改单件资料'));
 const form=node('form',{class:'stage-form'});
 const specs=order?[['title','生产单名称',160],['order_no','正式生产单号',80],['business_contact','业务对接人',80],['received_on','实际收单日期',10],['source_ref','生产单来源',300]]
  :[['piece_label','单件编号',80],['product_name','产品名称',160],['source_ref','单件来源',300]];
 const fields={};
 for(const [name,label,limit] of specs){const input=node('input',{name,maxlength:String(limit),type:name==='received_on'?'date':'text'});input.value=record[name]||'';fields[name]=input;form.append(productionField(label,input));}
 const button=node('button',{class:'primary',type:'submit'},'保存资料'),feedback=productionFeedback();form.append(button,feedback);
 productionSave(form,button,feedback,order?'/api/production/orders/'+encodeURIComponent(record.id):'/api/production/pieces/'+encodeURIComponent(record.id),()=>Object.fromEntries(Object.entries(fields).map(([name,input])=>[name,input.value])),order?null:record.id);
 editor.append(form);return editor;
}
async function reloadProduction(openId){
 state.production=await api('/api/production');
 renderProduction(openId);
}
function productionSave(form,button,feedback,endpoint,body,openId){
 form.addEventListener('submit',async event=>{
  event.preventDefault();button.disabled=true;feedback.textContent='正在保存…';feedback.className='small production-feedback';
  try{await api(endpoint,{method:'PATCH',body:body()});await reloadProduction(openId);showError('');}
  catch(error){feedback.textContent=error.message;feedback.className='small error';}
  finally{button.disabled=false;}
 });
}
function stageEditor(piece,stage,editable){
 const row=node('div',{class:'stage-row'});
 const heading=node('div',{class:'stage-heading'});
 heading.append(node('strong',{},stage.stage),node('span',{class:'stage-state','data-state':stage.state},stage.state));
 const latest=stage.updated_at?`${textDate(stage.updated_at)} · ${stage.updated_by||'人工更新'}${stage.basis?' · '+stage.basis:''}`:'尚无人工更新';
 const content=node('div',{class:'stage-content'});
 content.append(node('p',{class:'small muted'},latest));
 if(stage.note)content.append(node('p',{class:'small'},stage.note));
 if(stage.source_ref)content.append(node('p',{class:'small muted'},'依据：'+stage.source_ref));
 row.append(heading,content);
 if(!editable)return row;
 const editor=node('details',{class:'stage-editor'});
 editor.append(node('summary',{},'修改此工序'));
 const form=node('form',{class:'stage-form'});
 const status=productionSelect('state',state.production.states,stage.state);
 const basis=productionSelect('basis',['人工核对','现场进展','业务口头放行','厂内检查'],stage.basis||'人工核对');
 const note=node('textarea',{name:'note',rows:'2',maxlength:'2000',placeholder:'写明哪一件、哪一步、实际结果；合格/返工/暂缓/不适用必填'});note.value=stage.note||'';
 const source=node('input',{name:'source_ref',maxlength:'300',placeholder:'群名＋消息时间或现场记录；业务口头放行必填'});source.value=stage.source_ref||'';
 const fields=node('div',{class:'stage-form-grid'});
 fields.append(productionField('进度',status),productionField('依据类型',basis));
 const button=node('button',{class:'primary',type:'submit'},'保存工序'),feedback=productionFeedback();
 form.append(fields,productionField('具体说明',note),productionField('来源定位',source),button,feedback);
 productionSave(form,button,feedback,'/api/production/pieces/'+encodeURIComponent(piece.id)+'/stage',()=>({stage:stage.stage,state:status.value,basis:basis.value,note:note.value,source_ref:source.value}),piece.id);
 editor.append(form);row.append(editor);return row;
}
function deliveryEditor(piece,editable){
 const block=node('section',{class:'delivery-block'});
 block.append(node('h4',{},'交付'),node('span',{class:'stage-state','data-state':piece.delivery_state},piece.delivery_state));
 if(!editable)return block;
 const editor=node('details',{class:'stage-editor'});editor.append(node('summary',{},'修改交付状态'));
 const form=node('form',{class:'stage-form'});
 const status=productionSelect('state',state.production.delivery_states,piece.delivery_state);
 const note=node('textarea',{name:'note',rows:'2',maxlength:'2000',placeholder:'填写实际交付凭据、部分交付范围或暂缓原因'});
 const source=node('input',{name:'source_ref',maxlength:'300',placeholder:'出货记录或对应群消息时间'});
 const button=node('button',{class:'primary',type:'submit'},'保存交付'),feedback=productionFeedback();
 form.append(productionField('交付状态',status),productionField('说明',note),productionField('来源定位',source),button,feedback);
 productionSave(form,button,feedback,'/api/production/pieces/'+encodeURIComponent(piece.id)+'/delivery',()=>({state:status.value,note:note.value,source_ref:source.value}),piece.id);
 editor.append(form);block.append(editor);return block;
}
function linkEditor(piece,editable){
 const block=node('section',{class:'production-links'});block.append(node('h4',{},'关联事项'));
 const productionItems=new Map((state.snapshot.items||[]).map(item=>[item.id,item]));
 const salesItems=new Map((state.snapshot.after_sales?.cases||[]).map(item=>[item.id,item]));
 for(const link of piece.links){const record=(link.item_type==='production'?productionItems:salesItems).get(link.item_id),row=node('div',{class:'production-link-row'});row.append(node('p',{class:'small'},`${link.item_type==='production'?'生产':'售后'} · ${record?.title||record?.observed||record?.product||link.item_id}`));if(editable){const button=node('button',{type:'button'},'解除关联'),feedback=productionFeedback();button.addEventListener('click',async()=>{if(!confirm('解除这条单件关联？原事项和工序记录仍保留。'))return;button.disabled=true;try{await api('/api/production/pieces/'+encodeURIComponent(piece.id)+'/unlink',{method:'PATCH',body:{item_type:link.item_type,item_id:link.item_id}});await reloadProduction(piece.id);}catch(error){feedback.textContent=error.message;feedback.className='small error';}finally{button.disabled=false;}});row.append(button,feedback);}block.append(row);}
 if(!piece.links.length)block.append(node('p',{class:'small muted'},'尚未关联生产或售后事项。'));
 if(!editable)return block;
 const used=new Set(state.production.orders.flatMap(order=>order.pieces.flatMap(p=>p.links.map(link=>link.item_type+':'+link.item_id))));
 const choices=[...productionItems.values()].filter(item=>!used.has('production:'+item.id)).map(item=>['production:'+item.id,'生产 · '+item.title]);
 for(const item of salesItems.values())if(!used.has('after_sales:'+item.id))choices.push(['after_sales:'+item.id,'售后 · '+(item.product||item.observed||item.id)]);
 if(!choices.length)return block;
 const form=node('form',{class:'link-form'}),select=productionSelect('linked_record',choices),button=node('button',{type:'submit'},'关联到此单件'),feedback=productionFeedback();
 form.append(productionField('选择已有事项',select),button,feedback);
 productionSave(form,button,feedback,'/api/production/pieces/'+encodeURIComponent(piece.id)+'/link',()=>{const [item_type,...parts]=select.value.split(':');return {item_type,item_id:parts.join(':')};},piece.id);
 block.append(form);return block;
}
function renderProduction(openId){
 const data=state.production;if(!data)return;
 const orders=data.orders||[];
 $('production-admin').hidden=state.user?.role!=='admin';
 $('production-count').textContent=`${orders.length} 张生产单 · ${orders.reduce((sum,order)=>sum+order.pieces.length,0)} 件产品`;
 const orderSelect=clear($('piece-order'));
 orderSelect.append(node('option',{value:''},orders.length?'选择生产单':'请先建立生产单'));
 for(const order of orders)orderSelect.append(node('option',{value:order.id},`${order.order_no||'未编号'} · ${order.title}`));
 $('piece-form').querySelector('button').disabled=!orders.length;
 const filter=$('production-filter'),previous=filter.value;
 clear(filter).append(node('option',{value:'all'},'全部生产单'));
 for(const order of orders)filter.append(node('option',{value:order.id},`${order.order_no||'未编号'} · ${order.title}`));
 filter.value=orders.some(order=>order.id===previous)?previous:'all';
 $('production-empty-note').textContent=orders.length?'工序状态由授权人员逐件保存，修改历史保留在单件详情。':'目前没有建立生产单。历史聊天事项尚未逐件归档，不应据此推定生产为零。';
 const list=clear($('production-orders'));
 for(const order of orders.filter(order=>filter.value==='all'||filter.value===order.id)){
  const wrapper=node('details',{class:'production-order','data-order-id':order.id});wrapper.open=filter.value===order.id||orders.length===1||order.pieces.some(piece=>piece.id===openId);
  const summary=node('summary',{class:'production-order-summary'});
  summary.append(node('strong',{},order.order_no||'单号待补'),node('span',{},order.title),node('span',{class:'small muted'},`${order.pieces.length} 件 · 收单 ${order.received_on||'待补日期'}`));
  wrapper.append(summary);
  const meta=node('p',{class:'small muted'},`业务对接：${order.business_contact||'待补'} · 建档 ${textDate(order.created_at)}${order.source_ref?' · 来源 '+order.source_ref:''}`);wrapper.append(meta);
  if(state.user?.role==='admin')wrapper.append(masterEditor('order',order));
  if(!order.pieces.length)wrapper.append(node('p',{class:'empty'},'这张生产单尚未登记单件产品。'));
  for(const piece of order.pieces){
   const editable=canEditPiece(piece);
   const detail=node('details',{class:'production-piece','data-piece-id':piece.id});detail.open=piece.id===openId||orders.length===1&&order.pieces.length===1;
   const complete=piece.stages.filter(stage=>stage.state==='合格').length;
   const blocked=piece.stages.filter(stage=>['返工','暂缓','待业务确认'].includes(stage.state)).map(stage=>stage.stage);
   const pieceSummary=node('summary',{class:'production-piece-summary'});
   pieceSummary.append(node('strong',{},piece.piece_label+' · '+piece.product_name),node('span',{class:'small'},`合格 ${complete}/6 · 交付 ${piece.delivery_state}`));
   if(blocked.length)pieceSummary.append(node('span',{class:'production-attention'},`${blocked.length} 道待处理`));
   detail.append(pieceSummary);
   if(piece.source_ref)detail.append(node('p',{class:'small muted production-source'},'单件来源：'+piece.source_ref));
   if(state.user?.role==='admin')detail.append(masterEditor('piece',piece));
   const stages=node('div',{class:'stage-list'});
   for(const stage of piece.stages)stages.append(stageEditor(piece,stage,editable));
   detail.append(stages,deliveryEditor(piece,editable),linkEditor(piece,Boolean(state.user.permissions?.manage_all)));
   if(piece.updates.length){const history=node('details',{class:'production-history'});history.append(node('summary',{},`变更记录 ${piece.updates.length} 条`));for(const update of piece.updates)history.append(node('p',{class:'small'},`${textDate(update.updated_at)} · ${update.updated_by} · ${update.subject} ${update.old_value} → ${update.new_value}；${update.note||'未补说明'}${update.source_ref?' · '+update.source_ref:''}`));detail.append(history);}
   wrapper.append(detail);
  }
  list.append(wrapper);
 }
}
$('production-filter').addEventListener('change',()=>renderProduction());
$('order-form').addEventListener('submit',async event=>{
 event.preventDefault();const button=event.target.querySelector('button');button.disabled=true;
 try{const data=Object.fromEntries(new FormData(event.target));await api('/api/production/orders',{method:'POST',body:data});event.target.reset();await reloadProduction();showError('');}
 catch(error){showError(error.message);}finally{button.disabled=false;}
});
$('piece-form').addEventListener('submit',async event=>{
 event.preventDefault();const button=event.target.querySelector('button');button.disabled=true;
 try{const data=Object.fromEntries(new FormData(event.target));const result=await api('/api/production/pieces',{method:'POST',body:data});event.target.reset();await reloadProduction(result.id);showError('');}
 catch(error){showError(error.message);}finally{button.disabled=false;}
});
