// 固定HTTPS入口，仅连接已绑定的VPC Service，不接受客户端指定内网目标。
export default {
 async fetch(request,env) {
  const incoming=new URL(request.url);
  if(!env.PUBLIC_ORIGIN||!env.GATEWAY_SECRET||incoming.origin!==env.PUBLIC_ORIGIN)
   return new Response('访问入口不匹配',{status:421});
  if(!['GET','HEAD','POST','PATCH','DELETE'].includes(request.method))
   return new Response('请求方法不支持',{status:405});
  const headers=new Headers(request.headers);
  for(const name of ['host','forwarded','x-forwarded-host','x-forwarded-proto','x-dino-gateway-key','x-dino-public-origin'])headers.delete(name);
  headers.set('X-Dino-Gateway-Key',env.GATEWAY_SECRET);
  headers.set('X-Dino-Public-Origin',env.PUBLIC_ORIGIN);
  try {
   const target='http://127.0.0.1:8769'+incoming.pathname+incoming.search;
   const options={method:request.method,headers,redirect:'manual'};
   if(!['GET','HEAD'].includes(request.method))options.body=request.body;
   const response=await env.DASHBOARD.fetch(new Request(target,options));
   const outgoing=new Headers(response.headers);
   outgoing.set('Cache-Control','no-store');
   outgoing.set('Strict-Transport-Security','max-age=15552000');
   return new Response(response.body,{status:response.status,headers:outgoing});
  }catch(_error){
   return new Response('办公室看板暂时不可用，请确认服务器已开机联网。',{status:503,headers:{'Content-Type':'text/plain; charset=utf-8','Cache-Control':'no-store'}});
  }
 }
};
