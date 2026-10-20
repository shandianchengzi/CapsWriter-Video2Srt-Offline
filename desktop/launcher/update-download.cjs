const fs=require('node:fs'),path=require('node:path'),crypto=require('node:crypto');
const {pipeline}=require('node:stream/promises');
const wait=ms=>new Promise(r=>setTimeout(r,ms));
async function digest(file){const h=crypto.createHash('sha512');for await(const chunk of fs.createReadStream(file))h.update(chunk);return h.digest('base64');}
async function resumableDownload(url,destination,options,{request,cacheDir,fallback,say,delay=wait}){
 if(!options.sha512)throw Error('更新文件缺少 SHA-512 校验，停止下载');
 fs.mkdirSync(cacheDir,{recursive:true});const partial=path.join(cacheDir,crypto.createHash('sha256').update(options.sha512).digest('hex')+'.part');
 const sources=[...new Set([String(url),...(fallback?[fallback]:[])])];let last;
 for(let attempt=0;attempt<6;attempt++){
  if(options.cancellationToken?.cancelled)throw Error('更新下载已取消');
  try{
   if(fs.existsSync(partial)&&await digest(partial)===options.sha512){fs.copyFileSync(partial,destination);fs.unlinkSync(partial);return destination;}
   const offset=fs.existsSync(partial)?fs.statSync(partial).size:0;
   const response=await new Promise((resolve,reject)=>{const req=request(sources[attempt%sources.length],{...(options.headers||{}),...(offset?{Range:`bytes=${offset}-`}:{})});const timer=setTimeout(()=>{req.abort();reject(Error('更新连接超时'));},30000);req.once('error',e=>{clearTimeout(timer);reject(e);});req.once('response',res=>{clearTimeout(timer);resolve(res);});req.end();});
   const status=response.statusCode;
   if(status!==200&&status!==206){response.resume();if(status===416&&fs.existsSync(partial))fs.unlinkSync(partial);throw Error('更新下载 HTTP '+status);}
   const header=k=>String(response.headers[k]||'');let start=0,total=Number(header('content-length'));
   if(status===206){const match=/^bytes (\d+)-(\d+)\/(\d+)$/.exec(header('content-range'));if(!match||Number(match[1])!==offset){response.resume();fs.rmSync(partial,{force:true});throw Error('续传范围不匹配');}start=offset;total=Number(match[3]);}
   let received=start,lastData=Date.now();const timer=setInterval(()=>{if(Date.now()-lastData>30000||options.cancellationToken?.cancelled)response.destroy(Error('下载中断或已取消'));},1000);
   response.on('data',chunk=>{lastData=Date.now();received+=chunk.length;options.onProgress?.({total,transferred:received,percent:total?received/total*100:0,bytesPerSecond:0});});
   try{await pipeline(response,fs.createWriteStream(partial,{flags:start?'a':'w'}));}finally{clearInterval(timer);}
   if(await digest(partial)!==options.sha512){fs.rmSync(partial,{force:true});throw Error('更新文件 SHA-512 校验失败');}
   fs.copyFileSync(partial,destination);fs.unlinkSync(partial);return destination;
  }catch(e){last=e;if(options.cancellationToken?.cancelled)throw e;if(attempt<5){say(`更新连接中断（${e.message}），切换至 ${new URL(sources[(attempt+1)%sources.length]).hostname} 重试 ${attempt+1}/5；已下载部分保留`);await delay(Math.min(16000,1000*2**attempt));}}
 }
 throw last;
}
module.exports={resumableDownload,digest};
