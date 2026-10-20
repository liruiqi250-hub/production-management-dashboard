'use strict';
// 订单资料只读展示；生成文件不改变工序、交付或检验结论。
const productionDocumentKinds={labels:'标签',fitter:'钳工质检表',product:'产品质检表'};
function documentViews(){if(!state.productionDocumentViews)state.productionDocumentViews={};return state.productionDocumentViews;}
function documentValue(value){return value===null||value===undefined?'':String(value);}
async function downloadProductionDocument(metadata,format,feedback){
 const session=state.csrf,controller=new AbortController(),timer=setTimeout(()=>controller.abort(),60000);
 const current=()=>state.csrf===session;
 feedback.className='small muted document-download-status';feedback.textContent='正在接收'+(format==='pdf'?' PDF':' Excel')+'文件…';
 try{
  const response=await fetch('/api/production/document-files/'+encodeURIComponent(metadata.id)+'?format='+format,{credentials:'same-origin',signal:controller.signal});
  if(!current())return;
  if(response.status===401){showLogin();$('login-error').textContent='登录已过期，请重新登录后下载生产资料。';return;}
  if(!response.ok){const result=await response.json().catch(()=>({}));if(!current())return;if(response.status===410){metadata.status='cleaned';throw new Error('临时资料已清理，请重新生成后下载。');}if(result.code==='password_change_required'){await load();if(current())$('first-password-error').textContent='请先修改初始密码，再下载生产资料。';return;}throw new Error(result.error||'文件下载失败，请稍后重试。');}
  const expected={xlsx:'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',pdf:'application/pdf'}[format];
  if(!expected||String(response.headers.get('Content-Type')||'').split(';')[0].trim().toLowerCase()!==expected)throw new Error('返回的文件格式不正确，请刷新后重试。');
  const file=await response.blob();if(!current())return;
  if(!file.size)throw new Error('下载文件为空，请重新生成后重试。');
  const length=response.headers.get('Content-Length'),encoding=response.headers.get('Content-Encoding');
  if(length&&/^\d+$/.test(length)&&(!encoding||encoding==='identity')&&Number(length)!==file.size)throw new Error('文件未接收完整，请检查连接后重试。');
  const signature=new Uint8Array(await file.slice(0,5).arrayBuffer());if(!current())return;
  const valid=format==='pdf'?signature[0]===37&&signature[1]===80&&signature[2]===68&&signature[3]===70&&signature[4]===45:signature[0]===80&&signature[1]===75&&signature[2]===3&&signature[3]===4;
  if(!valid)throw new Error('文件内容异常，请重新生成后重试。');
  let filename=(metadata.filename||'生产资料.xlsx').replace(/\.[^.]*$/,'.'+format);
  const disposition=response.headers.get('Content-Disposition')||'',encoded=/filename\*=UTF-8''([^;]+)/i.exec(disposition),plain=/filename="([^"]+)"/i.exec(disposition);
  if(encoded){try{filename=decodeURIComponent(encoded[1]);}catch(_){}}else if(plain)filename=plain[1];
  filename=filename.replace(/[\\/:*?"<>|\u0000-\u001f\u007f]/g,'_').replace(/[. ]+$/,'');
  if(!filename.toLowerCase().endsWith('.'+format))filename+='.'+format;
  const url=URL.createObjectURL(file),link=node('a',{href:url,download:filename,hidden:''});
  try{document.body.append(link);link.click();}finally{link.remove();setTimeout(()=>URL.revokeObjectURL(url),60000);}
  feedback.textContent='文件已接收完整，已交给浏览器保存：'+filename;
 }catch(error){if(current()){feedback.className='small error document-download-status';feedback.textContent=error.name==='AbortError'?'接收文件超时，请检查连接后重试。':error.name==='TypeError'?'下载连接中断，请检查内网连接后重试。':error.message;}}
 finally{clearTimeout(timer);}
}
function documentFieldList(fields){
 const list=node('dl',{class:'document-fields'});
 for(const [label,value] of fields){const pair=node('div');pair.append(node('dt',{},label),node('dd',{},documentValue(value)||'\u00a0'));list.append(pair);}
 return list;
}
function parseDocumentPrintPages(value,totalPages){
 if(typeof value!=='string'||!Number.isInteger(totalPages)||totalPages<1)throw new Error('打印页码无效，请重新打开预览。');
 const text=value.trim().replace(/[，、]/g,',');
 if(!text||text.length>1000)throw new Error('请填写页码，例如 1-3,5。');
 const selected=new Set();
 for(const part of text.split(',')){
  const match=/^\s*([1-9]\d*)\s*(?:-\s*([1-9]\d*)\s*)?$/.exec(part);
  if(!match)throw new Error('页码格式不正确，请使用 1-3,5，不留空项。');
  const first=Number(match[1]),last=match[2]?Number(match[2]):first;
  if(!Number.isSafeInteger(first)||!Number.isSafeInteger(last)||last<first||last>totalPages)throw new Error('页码超出范围，请填写 1 至 '+totalPages+' 页，起始页不能大于结束页。');
  for(let page=first;page<=last;page++)selected.add(page);
 }
 const pages=[...selected].sort((a,b)=>a-b),ranges=[];
 for(let index=0;index<pages.length;index++){const first=pages[index];let last=first;while(index+1<pages.length&&pages[index+1]===last+1)last=pages[++index];ranges.push(first===last?String(first):first+'-'+last);}
 return {pages,canonical:ranges.join(','),count:pages.length};
}
function documentPrintRequestId(){
 if(typeof crypto.randomUUID==='function')return crypto.randomUUID();
 const bytes=new Uint8Array(16);crypto.getRandomValues(bytes);bytes[6]=(bytes[6]&15)|64;bytes[8]=(bytes[8]&63)|128;
 const hex=[...bytes].map(value=>value.toString(16).padStart(2,'0')).join('');
 return hex.slice(0,8)+'-'+hex.slice(8,12)+'-'+hex.slice(12,16)+'-'+hex.slice(16,20)+'-'+hex.slice(20);
}
function documentPrintJobClient(metadata,options={}){
 const session=state.csrf,terminal=new Set(['submitted','failed','unknown']);let task=options.task||null,timer=null,stopped=false,busy=false;
 const current=()=>!stopped&&session===state.csrf&&(!options.isActive||options.isActive());
 function clearPoll(){if(timer!==null){clearTimeout(timer);timer=null;}}
 function message(){
  if(task?.error)return task.error;
  if(task?.job?.state==='submitted')return '已交给 Windows 打印队列。请在打印机处确认实际出纸。';
  if(task?.job?.state==='unknown')return '打印提交结果暂不明确，请先检查 Windows 队列，避免重复打印。';
  if(task?.job?.state==='failed')return task.job.message||'本次打印未能提交，请查看原因后新建任务。';
  return task?.job?'打印任务处理中，正在查询 Windows 队列提交状态…':busy?'正在提交打印任务…':'';
 }
 function publish(){if(options.onTask&&session===state.csrf)options.onTask(task);if(current()&&options.onChange)options.onChange({task,busy,message:message()});}
 function schedule(){clearPoll();if(current()&&task?.job&&!terminal.has(task.job.state))timer=setTimeout(()=>{timer=null;if(current())refresh();},2000);}
 function accept(result){const job=result?.job||result;if(!job||typeof job.id!=='string'||typeof job.state!=='string')throw new Error('未取得有效打印任务回执，请重试同一任务。');task.job=job;task.error='';publish();schedule();return job;}
 async function refresh(){
  if(!current()||busy||!task?.job?.id)return;
  if(terminal.has(task.job.state)){publish();return task.job;}
  clearPoll();busy=true;publish();
  try{const result=await api('/api/production/print-jobs/'+encodeURIComponent(task.job.id));if(!current())return;return accept(result);}
  catch(error){if(current()){task.error=error.name==='TypeError'?'查询连接中断，可继续查询同一任务。':error.message;publish();}}
  finally{busy=false;publish();}
 }
 async function submit(pages,copies){
  if(!current()||busy)return;
  const selection=parseDocumentPrintPages(pages,Number(metadata.pages));
  if(!Number.isInteger(copies)||copies<1||copies>5)throw new Error('打印份数须为 1 至 5 的整数。');
  if(selection.count*copies>250)throw new Error('本次共 '+selection.count*copies+' 个纸面，主机打印每批最多 250 个，请减少页数或份数。');
  if(task&&(task.pages!==selection.canonical||task.copies!==copies))throw new Error('本次任务的页码与份数已固定，请先新建下一任务。');
  if(task?.job)return refresh();
  if(!task)task={output_id:metadata.id,pages:selection.canonical,copies,request_id:documentPrintRequestId(),job:null,error:''};
  busy=true;task.error='';publish();
  try{const result=await api('/api/production/print-jobs',{method:'POST',body:{output_id:task.output_id,pages:task.pages,copies:task.copies,request_id:task.request_id}});if(!current())return;return accept(result);}
  catch(error){if(current()){task.error=error.name==='TypeError'?'提交连接中断，请重试同一任务；会沿用任务编号，避免重复提交。':error.message;publish();}}
  finally{busy=false;publish();}
 }
 return {submit,refresh,getTask:()=>task,isBusy:()=>busy,isTerminal:()=>Boolean(task?.job&&terminal.has(task.job.state)),
  next(){if(busy)throw new Error('正在提交或查询，请等待本次请求返回。');clearPoll();task=null;publish();},
  stop(){stopped=true;clearPoll();},pause:clearPoll};
}
function productionDocumentPrinting(metadata,orderId,currentPage){
 const panel=node('section',{class:'document-print-panel','aria-label':'打印设置',hidden:''}),heading=node('div',{class:'document-print-heading'});
 const browserButton=node('button',{class:'document-download',type:'button','aria-expanded':'false'},'打印 / 云打印'),hostButton=node('button',{class:'document-download',type:'button','aria-expanded':'false'},'由本机打印');
 const title=node('h4',{},'打印设置'),close=node('button',{type:'button',class:'document-print-close'},'收起打印设置');heading.append(title,close);panel.append(heading);
 const target=node('p',{class:'small document-print-target'}),fields=node('div',{class:'document-print-fields'});
 const rangeLabel=node('label'),range=node('select',{'aria-label':'打印范围'});for(const [value,label] of [['all','全部页面'],['current','当前预览页'],['range','指定页码']])range.append(node('option',{value},label));range.value='all';rangeLabel.append(node('span',{},'打印范围'),range);
 const pagesLabel=node('label',{hidden:''}),pagesInput=node('input',{type:'text',placeholder:'例如 1-3,5','aria-label':'指定打印页码',inputmode:'text',maxlength:'1000'});pagesLabel.append(node('span',{},'页码'),pagesInput);
 const copiesLabel=node('label'),copies=node('input',{type:'number',min:'1',max:'5',step:'1',value:'1','aria-label':'本机打印份数'});copies.value='1';copiesLabel.append(node('span',{},'份数'),copies);fields.append(rangeLabel,pagesLabel,copiesLabel);
 const summary=node('p',{class:'document-print-summary',role:'status','aria-live':'polite'}),hint=node('p',{class:'small muted document-print-hint'}),status=node('p',{class:'small document-print-status',role:'status','aria-live':'polite'});
 const actions=node('div',{class:'document-print-actions'}),browserLink=node('a',{class:'document-print-link primary',href:'#'},'打开打印页面'),submit=node('button',{type:'button',class:'primary',hidden:''},'提交至本机打印队列'),nextTask=node('button',{type:'button',hidden:''},'新建下一任务');actions.append(browserLink,submit,nextTask);panel.append(target,fields,summary,hint,actions,status);
 const view=documentViews()[orderId]||(documentViews()[orderId]={}),session=state.csrf;
 let mode='browser',config=null,loadingConfig=false,configError='';
 const active=()=>session===state.csrf&&panel.isConnected&&!panel.hidden&&document.visibilityState!=='hidden'&&!(typeof panel.closest==='function'&&panel.closest('[hidden]'));
 const client=documentPrintJobClient(metadata,{task:view.printRequest?.output_id===metadata.id?view.printRequest:null,isActive:active,onTask:task=>{view.printRequest=task;},onChange:result=>{
  if(mode!=='host')return;status.textContent=result.message;status.className='small document-print-status'+(result.task?.error||['failed','unknown'].includes(result.task?.job?.state)?' error':'');update();
 }});
 function selection(){return parseDocumentPrintPages(range.value==='all'?'1-'+Number(metadata.pages):range.value==='current'?String(currentPage()):pagesInput.value,Number(metadata.pages));}
 function update(){
  const task=client.getTask(),locked=mode==='host'&&Boolean(task),done=client.isTerminal();
  range.disabled=locked;pagesInput.disabled=locked;copies.disabled=locked;copiesLabel.hidden=mode!=='host';pagesLabel.hidden=range.value!=='range';nextTask.hidden=mode!=='host'||!task;nextTask.disabled=client.isBusy();browserLink.hidden=mode!=='browser';submit.hidden=mode!=='host';
  browserButton.setAttribute('aria-expanded',String(!panel.hidden&&mode==='browser'));hostButton.setAttribute('aria-expanded',String(!panel.hidden&&mode==='host'));
  if(mode==='browser'){
   title.textContent='打印 / 云打印';target.textContent='目标：在系统打印对话框中选择当前设备的打印机或已安装的云打印服务。';hint.textContent='先打开打印页面，核对后点击“打印 / 云打印”。份数在系统对话框中选择；每批最多 80 页。';
  }else{
   title.textContent='由本机打印';target.textContent='固定目标：'+(config?.printer_name||'正在读取看板主机的打印机…');hint.textContent='由运行看板的主机发送至此固定队列；每批最多 250 个纸面，份数为 1 至 5。';
   submit.textContent=done?'本次任务已结束':task?.job?'查询任务状态':task?'重试同一任务':'提交至本机打印队列';
  }
  let valid=true;
  try{const selected=selection(),number=mode==='host'?Number(copies.value):1;if(!Number.isInteger(number)||number<1||number>5)throw new Error('份数须为 1 至 5 的整数。');
   summary.textContent=mode==='host'?'选中 '+selected.count+' 页 × '+number+' 份，共 '+selected.count*number+' 个纸面。':'选中 '+selected.count+' 页，每份 '+selected.count+' 个纸面；份数在系统对话框中确认。';
   if(mode==='browser'&&selected.count>80)throw new Error('本次选中 '+selected.count+' 页，每批最多 80 页，请指定页码分批打印。');
   if(mode==='host'&&selected.count*number>250)throw new Error('本次超过 250 个纸面，请减少页数或份数。');summary.className='document-print-summary';browserLink.setAttribute('href','/api/production/document-files/'+encodeURIComponent(metadata.id)+'/print?pages='+encodeURIComponent(selected.canonical));
  }catch(error){valid=false;summary.textContent=error.message;summary.className='document-print-summary error';}
  if(mode==='host'&&(loadingConfig||configError||!config?.enabled||!config?.available)){
   valid=false;if(!task)status.textContent=loadingConfig?'正在读取主机打印状态…':configError||config?.reason||'主机打印尚未启用，请由维护人员检查固定队列。';
  }else if(mode==='host'&&!task)status.textContent='';
  if(!valid)browserLink.removeAttribute('href');browserLink.setAttribute('aria-disabled',String(!valid));browserLink.tabIndex=valid?0:-1;
  submit.disabled=!valid||client.isBusy()||(mode==='host'&&done);
 }
 async function show(value){
  mode=value;panel.hidden=false;status.textContent='';
  if(mode==='host'){
   const task=client.getTask();if(task){range.value='range';pagesInput.value=task.pages;copies.value=String(task.copies);}
   if(!config&&!loadingConfig){loadingConfig=true;configError='';update();try{const result=await api('/api/production/printing');if(!active())return;config=result;}catch(error){if(active())configError=error.name==='TypeError'?'读取主机状态失败，请检查连接后重新打开。':error.message;}finally{loadingConfig=false;if(active())update();}}
   if(active()&&client.getTask()?.job)client.refresh();
  }else client.pause();
  update();
 }
 browserButton.addEventListener('click',()=>show('browser'));hostButton.addEventListener('click',()=>show('host'));
 close.addEventListener('click',()=>{panel.hidden=true;client.pause();update();});
 for(const input of [range,pagesInput,copies]){input.addEventListener('input',update);input.addEventListener('change',update);}
 browserLink.addEventListener('click',event=>{update();if(mode!=='browser'||!active()||browserLink.getAttribute('aria-disabled')==='true'){event.preventDefault();return;}status.textContent='请在打印页面核对后选择“打印 / 云打印”。';});
 submit.addEventListener('click',async()=>{
  if(submit.disabled||!active())return;
  try{const selected=selection();if(mode!=='host')return;if(!config?.enabled||!config?.available)throw new Error(config?.reason||'主机打印暂不可用。');await client.submit(selected.canonical,Number(copies.value));
  }catch(error){if(active()){status.textContent=error.message;status.className='small error document-print-status';}}
 });
 nextTask.addEventListener('click',()=>{if(!active())return;try{client.next();status.textContent='新任务尚未提交。请确认页码与份数后再提交。';update();}catch(error){status.textContent=error.message;}});
 window.addEventListener('pagehide',()=>client.stop(),{once:true});document.addEventListener('visibilitychange',()=>{if(document.visibilityState==='hidden')client.pause();});
 browserButton.disabled=hostButton.disabled=!metadata.pdf_ready||!Number(metadata.pages);update();
 return {element:panel,browserButton,hostButton};
}
function documentSourceDetail(result){
 const content=node('div',{class:'document-source-detail'}),latest=result.latest;
 if(!latest){content.append(node('p',{class:'empty'},'暂未关联生产单原件。每日整理会从生产资料群的新生产单自动建档，已有工序记录保留。'));return content;}
 const fields=latest.fields||{};
 content.append(node('p',{class:'small muted document-source-name'},latest.filename||''));
 const totals=Object.entries(latest.totals_by_unit||{});if(totals.length)content.append(node('p',{class:'small'},'原单数量：'+totals.map(([unit,count])=>count+unit).join('＋')));
 content.append(documentFieldList([['买方 / 客户',fields.buyer],['原单编号',fields.order_no],['业务员',fields.business_contact],['跟单员',fields.followup],['下单日期',fields.order_date],['出货日期',fields.delivery_date],['电压',fields.voltage],['插头 / 电气',fields.plug],['启动方式',fields.startup],['其他要求',fields.requirements]]));
 const checks=latest.checks||[];
 if(checks.length){const notes=node('details',{class:'document-notes'});notes.append(node('summary',{},'资料说明 · '+checks.length+'项'));const list=node('ul');for(const check of checks)list.append(node('li',{},(check.message||check.detail||'')+(check.source_ref?'（'+check.source_ref+'）':'')));notes.append(list);content.append(notes);}
 content.append(node('h4',{class:'document-products-heading'},'产品明细'));
 for(const product of latest.products||[]){
  const detail=node('details',{class:'document-product'}),heading=node('summary');
  heading.append(node('strong',{},(product.sequence?'序号 '+product.sequence+' · ':'')+(product.name||'')),node('span',{class:'small muted'},documentValue(product.quantity)+(product.unit||'')));
  detail.append(heading,documentFieldList([['原单规格',product.specification],['动作 / 功能',product.motions],['工艺',product.process],['材质',product.material],['来源',product.source_ref]]));content.append(detail);
 }
 const versions=result.documents||[];
 if(versions.length>1){const history=node('details',{class:'document-notes'});history.append(node('summary',{},'原件版本 · '+versions.length+'份'));for(const doc of versions)history.append(node('p',{class:'small muted'},(doc.filename||'')+' · '+textDate(doc.created_at)));content.append(history);}
 return content;
}
function documentPreview(metadata,orderId){
 const section=node('section',{class:'document-preview','aria-label':(productionDocumentKinds[metadata.kind]||'生产资料')+'预览'});
 if(metadata.status==='cleaned'){section.append(node('p',{class:'small document-cleaned-note',role:'status'},'临时资料已清理，请重新生成后预览、下载或打印。'));return section;}
 const heading=node('div',{class:'document-preview-heading'}),downloads=node('div',{class:'document-downloads'});
 heading.append(node('h4',{},(productionDocumentKinds[metadata.kind]||'生产资料')+'预览'));
 const base='/api/production/document-files/'+encodeURIComponent(metadata.id);
 const downloadStatus=node('p',{class:'small muted document-download-status',role:'status','aria-live':'polite'}),downloadButtons=[];
 for(const format of metadata.pdf_ready?['xlsx','pdf']:['xlsx']){
  const button=node('button',{class:'document-download',type:'button'},format==='pdf'?'下载 PDF':'下载 Excel');downloadButtons.push(button);downloads.append(button);
  button.addEventListener('click',async()=>{if(downloadButtons.some(item=>item.disabled))return;const original=button.textContent;downloadButtons.forEach(item=>item.disabled=true);button.textContent='正在接收…';try{await downloadProductionDocument(metadata,format,downloadStatus);}finally{downloadButtons.forEach(item=>item.disabled=false);button.textContent=original;}});
 }
 const printing=productionDocumentPrinting(metadata,orderId,()=>documentViews()[orderId]?.page||1);downloads.append(printing.browserButton,printing.hostButton);
 heading.append(downloads);section.append(heading);
 section.append(downloadStatus);
 section.append(node('p',{class:'small muted document-source-name'},(metadata.filename||'')+' · '+textDate(metadata.created_at)));
 section.append(printing.element);
 const warnings=metadata.warnings||[];
 if(warnings.length){const note=node('details',{class:'document-notes'});note.append(node('summary',{},'本次生成说明 · '+warnings.length+'项'));const list=node('ul');for(const warning of warnings)list.append(node('li',{},typeof warning==='string'?warning:warning.message||''));note.append(list);section.append(note);}
 if(!metadata.pdf_ready||!metadata.pages||metadata.preview_ready===false){section.append(node('p',{class:'small error',role:'status'},metadata.pdf_error||metadata.preview_error||'Excel 已生成，打印预览暂未就绪。可重试生成，或先下载 Excel。'));return section;}
 const previous=node('button',{type:'button'},'上一页'),next=node('button',{type:'button'},'下一页'),label=node('span',{class:'small document-page-label',role:'status','aria-live':'polite'});
 const fullSize=node('a',{class:'document-download',target:'_blank',rel:'noopener'},'放大本页');
 const pager=node('div',{class:'document-pager'});pager.append(previous,label,next,fullSize);
 const image=node('img',{class:'document-page-image',alt:'',decoding:'async'}),imageStatus=node('p',{class:'small muted',role:'status'});
 const views=documentViews(),view=views[orderId]||(views[orderId]={}),pages=Number(metadata.pages);
 let page=Math.max(1,Math.min(pages,view.page||1));
 function showPage(){view.page=page;previous.disabled=page===1;next.disabled=page===pages;label.textContent='第 '+page+' / '+pages+' 页';imageStatus.textContent='正在载入打印页面…';image.alt=(productionDocumentKinds[metadata.kind]||'生产资料')+' · 第'+page+'页';image.src=base+'/pages/'+page+'.png';fullSize.href=image.src;}
 previous.addEventListener('click',()=>{if(page>1){page--;showPage();}});next.addEventListener('click',()=>{if(page<pages){page++;showPage();}});
 image.addEventListener('load',()=>{imageStatus.textContent='';});image.addEventListener('error',()=>{imageStatus.textContent='预览页面无法载入，临时资料可能已清理。请重新生成后再预览。';imageStatus.className='small error';});
 section.append(pager,imageStatus,image);showPage();return section;
}
function productionDocumentPanel(order){
 const section=node('section',{class:'production-documents','aria-label':'订单生产资料'}),toolbar=node('div',{class:'document-toolbar'});
 const view=documentViews()[order.id]||(documentViews()[order.id]={});
 const details=node('details',{class:'production-document-detail','data-draft-view':'document:'+order.id}),summary=node('summary',{},'生产单详情');details.open=Boolean(view.open);details.append(summary);
 const source=node('div',{class:'document-source-slot'});details.append(source);
 const feedback=node('p',{class:'small muted document-generation-status',role:'status','aria-live':'polite'}),preview=node('div');
 const buttons=[];let loading=false;
 toolbar.hidden=!state.user?.permissions?.can_write;
 async function loadDetail(){
  if(loading)return;loading=true;const session=state.csrf;source.replaceChildren(node('p',{class:'small muted'},'正在读取生产单详情…'));
  try{const result=await api('/api/production/orders/'+encodeURIComponent(order.id)+'/documents');if(session!==state.csrf)return;source.replaceChildren(documentSourceDetail(result));const history=result.history||[],current=history.find(item=>item.id===view.preview?.id);if(history[0]?.status==='cleaned'||current?.status==='cleaned'){view.preview=null;preview.replaceChildren(node('p',{class:'small document-cleaned-note',role:'status'},'临时资料已清理，可重新生成。原单、模板和生产记录保留。'));}else if(!view.preview){const ready=history.find(item=>item.status==='ready');if(ready){view.preview=ready;preview.replaceChildren(documentPreview(ready,order.id));}}}
  catch(error){if(session===state.csrf)source.replaceChildren(node('p',{class:'small error'},error.message));}
  finally{loading=false;}
 }
 details.addEventListener('toggle',()=>{view.open=details.open;if(details.open)loadDetail();});
 for(const [kind,label] of Object.entries(productionDocumentKinds)){
  const button=node('button',{type:'button'},'生成'+label);buttons.push(button);toolbar.append(button);
  button.addEventListener('click',async()=>{
   const session=state.csrf;buttons.forEach(item=>item.disabled=true);feedback.className='small muted document-generation-status';feedback.textContent='正在生成'+label+'并准备打印预览…';
   try{const metadata=await api('/api/production/orders/'+encodeURIComponent(order.id)+'/documents/generate',{method:'POST',body:{kind}});if(session!==state.csrf)return;view.preview=metadata;view.page=1;preview.replaceChildren(documentPreview(metadata,order.id));feedback.textContent='已生成'+label+'，共 '+metadata.count+' 份。';}
   catch(error){if(session===state.csrf){feedback.className='small error document-generation-status';feedback.textContent=error.message;}}
   finally{buttons.forEach(item=>item.disabled=false);}
  });
 }
 section.append(details,toolbar,node('p',{class:'small muted document-help'},'按原单填写，缺少的信息、检验结果和签字留空。生成后可预览、下载或打印。'),node('p',{class:'small muted document-retention-note'},'生成文件连续 7 天未使用会自动清理；原单、模板和生产记录保留，下载副本由自己管理。'),feedback,preview);
 if(view.preview)preview.append(documentPreview(view.preview,order.id));
 if(details.open||view.preview)loadDetail();
 return section;
}
