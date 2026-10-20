'use strict';
const printButton=document.getElementById('print-document'),retryButton=document.getElementById('retry-pages'),printStatus=document.getElementById('print-status');
const printImages=[...document.querySelectorAll('[data-print-src]')];
let preparing=false,ready=false;
async function preparePrintPages(){
 if(preparing)return;preparing=true;ready=false;printButton.disabled=true;retryButton.hidden=true;
 try{
  for(let index=0;index<printImages.length;index++){
   const image=printImages[index];printStatus.textContent='正在准备打印页面 '+(index+1)+' / '+printImages.length+'…';
   if(!(image.complete&&image.naturalWidth)){
    await new Promise((resolve,reject)=>{
     const timeout=setTimeout(()=>finish(new Error('页面加载超时，请检查连接后重新加载。')),60000);
     const finish=error=>{clearTimeout(timeout);image.onload=null;image.onerror=null;error?reject(error):resolve();};
     image.onload=()=>finish();image.onerror=()=>finish(new Error('有页面未能载入。资料可能已清理或登录已过期，请返回看板重新生成或登录。'));
     image.src=image.dataset.printSrc;
    });
   }
   if(image.decode)await new Promise((resolve,reject)=>{
    const timeout=setTimeout(()=>reject(new Error('页面图片解析超时，请重新加载。')),60000);
    Promise.resolve().then(()=>image.decode()).then(()=>{clearTimeout(timeout);resolve();},()=>{clearTimeout(timeout);reject(new Error('页面图片解析失败，请重新加载。'));});
   });
  }
  ready=true;printButton.disabled=false;printButton.textContent='打印 / 云打印';printStatus.textContent='全部 '+printImages.length+' 页已就绪，可以选择打印机。';
 }catch(error){printStatus.textContent=error.message||'页面准备失败，请重新加载。';printStatus.classList.add('error');retryButton.hidden=false;printButton.textContent='页面尚未就绪';}
 finally{preparing=false;}
}
retryButton.addEventListener('click',()=>{printStatus.classList.remove('error');preparePrintPages();});
printButton.addEventListener('click',()=>{if(!ready)return;printStatus.textContent='请在系统打印对话框选择打印机、页码和份数。';try{window.print();}catch(_){printStatus.textContent='当前浏览器无法打开系统打印，请用电脑上的 Edge 或 Chrome 打开看板后打印。';}});
window.addEventListener('afterprint',()=>{printStatus.textContent='打印对话框已结束。是否已出纸，请以打印机实际结果为准。';});
preparePrintPages();
