const fs=require('node:fs'),path=require('node:path'),os=require('node:os'),http=require('node:http'),assert=require('node:assert/strict'),{gunzipSync}=require('node:zlib');
const {HttpExecutor,CancellationToken}=require('builder-util-runtime');
const {GenericDifferentialDownloader}=require('electron-updater/out/differentialDownloader/GenericDifferentialDownloader');
const {digest}=require('./update-download.cjs');
class Executor extends HttpExecutor{createRequest(options,callback){return http.request(options,callback);}}
(async()=>{
 const [oldFile,oldMap,newFile,newMap,output='release-dist/update-performance.json']=process.argv.slice(2);
 if(!oldFile||!oldMap||!newFile||!newMap)throw Error('Provide old installer / blockmap and new installer / blockmap');
 const root=fs.mkdtempSync(path.join(os.tmpdir(),'capswriter-real-diff-'));let downloadedBytes=0,requests=0;const stat=fs.statSync(newFile),expected=await digest(newFile);
 const server=http.createServer((req,res)=>{const range=/^bytes=(\d+)-(\d+)$/.exec(req.headers.range||'');if(!range){res.writeHead(400).end();return;}const start=Number(range[1]),end=Number(range[2]);if(start>end||end>=stat.size){res.writeHead(416).end();return;}requests++;res.writeHead(206,{'content-length':end-start+1,'content-range':`bytes ${start}-${end}/${stat.size}`});const stream=fs.createReadStream(newFile,{start,end});stream.on('data',chunk=>downloadedBytes+=chunk.length);stream.once('error',()=>res.destroy());res.once('close',()=>stream.destroy());stream.pipe(res);});
 await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
 try{const target=path.join(root,'rebuilt.exe'),messages=[];
  await new GenericDifferentialDownloader({size:stat.size,sha512:expected},new Executor(),{oldFile,newFile:target,newUrl:new URL('http://127.0.0.1:'+server.address().port+'/setup.exe'),logger:{info:m=>messages.push(m),warn:()=>{},error:console.error},isUseMultipleRangeRequest:false,cancellationToken:new CancellationToken(),onProgress:()=>{}}).download(JSON.parse(gunzipSync(fs.readFileSync(oldMap))),JSON.parse(gunzipSync(fs.readFileSync(newMap))));
  assert.equal(await digest(target),expected);
  const result={fullBytes:stat.size,downloadedBytes,downloadPercent:Number((downloadedBytes/stat.size*100).toFixed(2)),requests,sha512Verified:true,plan:messages.find(m=>m.startsWith('Full:'))};
  fs.mkdirSync(path.dirname(output),{recursive:true});fs.writeFileSync(output,JSON.stringify(result,null,2)+'\n');console.log(JSON.stringify(result));
 }finally{server.closeAllConnections();await new Promise(resolve=>server.close(resolve));fs.rmSync(root,{recursive:true,force:true});}
})().catch(error=>{console.error(error);process.exitCode=1;});
