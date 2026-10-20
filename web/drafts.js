'use strict';

// 仅保护当前标签页中已授权表单的未提交字段，不提交业务数据，也不保存登录信息。
(function(root){
 const STORAGE_KEY='production-dashboard-followup-drafts-v1';
 const MAX_AGE=24*60*60*1000,MAX_DRAFTS=100,MAX_STORAGE_CHARS=256*1024,MAX_TEXT=8192;
 const owns=(object,key)=>Object.prototype.hasOwnProperty.call(object,key);
 const record=value=>value!==null&&typeof value==='object'&&!Array.isArray(value)&&Object.prototype.toString.call(value)==='[object Object]';
 const validKey=key=>typeof key==='string'&&key.length>0&&key.length<=320&&!/[\u0000-\u001f\u007f]/.test(key)&&!['__proto__','constructor','prototype'].includes(key);
 const validField=key=>/^[A-Za-z][A-Za-z0-9_]{0,63}$/.test(key)&&!['constructor','prototype'].includes(key)&&!/(password|passwd|pwd|token|secret|authorization|cookie|session)/i.test(key);
 const validValue=value=>typeof value==='string'?value.length<=MAX_TEXT:value===null||typeof value==='boolean'||typeof value==='number'&&Number.isFinite(value);
 function fields(value,allowed){
  const result={};if(!record(value))return result;
  for(const key of Object.keys(value))if(validField(key)&&(!allowed||owns(allowed,key))&&validValue(value[key]))result[key]=value[key];
  return result;
 }
 const same=(left,right)=>Object.is(left,right);
 function create(options={}){
  const storage=options.storage,clock=typeof options.now==='function'?options.now:Date.now;
  let user='',entries=new Map(),recent=new Map(),pending=null,persistent=Boolean(storage&&typeof storage.getItem==='function'&&typeof storage.setItem==='function'&&typeof storage.removeItem==='function');
  let readable=persistent;
  const time=()=>{const value=Number(clock());return Number.isFinite(value)?value:Date.now();};
  function stale(entry){const age=time()-entry.updatedAt;return age>MAX_AGE||age< -5*60*1000;}
  function prune(){let removed=false;for(const [key,entry] of entries)if(stale(entry)){entries.delete(key);removed=true;}for(const [key,entry] of recent)if(stale(entry))recent.delete(key);return removed;}
  function serialize(){return JSON.stringify({version:1,user,entries:Object.fromEntries(entries)});}
  function persist(){
   prune();
   while(entries.size>MAX_DRAFTS)dropOldest();
   let payload=serialize();
   while(payload.length>MAX_STORAGE_CHARS&&entries.size){dropOldest();payload=serialize();}
   if(!storage)return persistent=false;
   try{if(user&&entries.size)storage.setItem(STORAGE_KEY,payload);else storage.removeItem(STORAGE_KEY);persistent=readable;}
   catch(_error){persistent=false;}
   return persistent;
  }
  function dropOldest(){let oldest;for(const pair of entries)if(!oldest||pair[1].updatedAt<oldest[1].updatedAt)oldest=pair;if(oldest)entries.delete(oldest[0]);}
  function validateEntry(value){
   if(!record(value)||!record(value.baseline)||!record(value.changes)||!Number.isFinite(value.updatedAt)||value.updatedAt<0)return null;
   const baseline=fields(value.baseline),changes=fields(value.changes,baseline);
   const entry={baseline,changes,updatedAt:value.updatedAt};
   return Object.keys(changes).length&&!stale(entry)?entry:null;
  }
  if(persistent){
   try{
    let payload;
    try{payload=storage.getItem(STORAGE_KEY);}catch(error){readable=false;throw error;}
    if(payload!==null){
     if(typeof payload!=='string'||payload.length>MAX_STORAGE_CHARS)throw new Error('invalid draft storage');
     const decoded=JSON.parse(payload);
     if(!record(decoded)||decoded.version!==1||typeof decoded.user!=='string'||!record(decoded.entries))throw new Error('invalid draft format');
     pending=decoded;
    }
   }catch(_error){persistent=false;try{storage.removeItem(STORAGE_KEY);}catch(_ignored){/* 失败时仍保留内存保护。 */}}
  }
  function setUser(value){
   const next=typeof value==='string'?value.trim():'';
   const safe=next.length<=120&&!/[\u0000-\u001f\u007f]/.test(next)?next:'';
   if(safe===user&&!pending)return user;
   entries.clear();recent.clear();user=safe;
   if(pending&&user&&pending.user===user){
    for(const key of Object.keys(pending.entries)){
     const entry=validKey(key)?validateEntry(pending.entries[key]):null;
     if(entry)entries.set(key,entry);
    }
   }
   pending=null;persist();return user;
  }
  function read(key,baseline){
   if(!user||!validKey(key))return null;
   if(prune())persist();
   const entry=entries.get(key);if(!entry)return null;
   const current=fields(baseline),changes=fields(entry.changes,current);
   for(const name of Object.keys(changes))if(same(changes[name],current[name]))delete changes[name];
   const changed=Object.keys(changes);
   if(!changed.length){entries.delete(key);persist();return null;}
   if(changed.length!==Object.keys(entry.changes).length){entry.changes=changes;persist();}
   // 更新未改字段的服务端值，避免成功提交时将后台新负责人等误恢复为旧值。
   const latest=recent.get(key);if(latest){latest.baseline=current;latest.values={...current,...changes};}
   const conflict=Object.keys(entry.baseline).some(name=>owns(current,name)&&!same(entry.baseline[name],current[name]));
   return {values:{...current,...changes},conflict,changed};
  }
  function capture(key,baseline,values){
   if(!user||!validKey(key))return null;
   const current=fields(baseline),provided=fields(values,current),changes={};
   recent.delete(key);recent.set(key,{baseline:current,values:{...current,...provided},updatedAt:time()});
   while(recent.size>MAX_DRAFTS)recent.delete(recent.keys().next().value);
   for(const name of Object.keys(provided))if(!same(provided[name],current[name]))changes[name]=provided[name];
   if(!Object.keys(changes).length){entries.delete(key);persist();return null;}
   const old=entries.get(key),original={...current};
   if(old&&!stale(old))for(const name of Object.keys(original))if(owns(old.baseline,name))original[name]=old.baseline[name];
   entries.set(key,{baseline:original,changes,updatedAt:time()});persist();
   return read(key,current);
  }
  function clear(key){if(validKey(key)){entries.delete(key);recent.delete(key);persist();}}
  function clearAll(){entries.clear();recent.clear();pending=null;persist();}
  function retain(keys){
   if(!keys||typeof keys[Symbol.iterator]!=='function')return;
   const allowed=new Set(Array.from(keys).filter(validKey));
   for(const key of entries.keys())if(!allowed.has(key))entries.delete(key);
   for(const key of recent.keys())if(!allowed.has(key))recent.delete(key);
   persist();
  }
  function ack(key,submittedValues,newBaseline){
   prune();
   const entry=entries.get(key),latest=recent.get(key);if((!entry&&!latest)||!user)return null;
   const previous=latest?latest.baseline:entry.baseline,submitted=fields(submittedValues,previous);
   if(!Object.keys(submitted).length)return read(key,previous);
   const baseline={...previous,...submitted,...fields(newBaseline,previous)},changes={};
   const local=latest?latest.values:{...baseline,...entry.changes};
   for(const name of Object.keys(local)){
    if(owns(submitted,name)&&same(local[name],submitted[name]))continue;
    if(owns(baseline,name)&&!same(local[name],baseline[name]))changes[name]=local[name];
   }
   recent.delete(key);
   if(!Object.keys(changes).length){clear(key);return null;}
   entries.set(key,{baseline,changes,updatedAt:time()});persist();return read(key,baseline);
  }
  return Object.freeze({setUser,capture,read,clear,clearAll,retain,ack,
   hasDrafts(){if(prune())persist();return entries.size>0;},
   get persistent(){return persistent;},
  });
 }
 root.FollowupDrafts=Object.freeze({create});
})(typeof window!=='undefined'?window:globalThis);
