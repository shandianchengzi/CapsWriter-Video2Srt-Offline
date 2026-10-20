'use strict';
// Headless Electron update manager; the Python tray remains the only visible tray icon.
// NSIS / electron-updater match the Cut Player packaging and update approach.
const {app, dialog, Notification, net, session, shell}=require('electron');
const {spawn}=require('node:child_process');
const fs=require('node:fs');
const path=require('node:path');

const OWNER='shandianchengzi';
const REPO='CapsWriter-Video2Srt-Offline';
let voice=null, updater=null, checking=false, downloaded=false, installing=false;
let manualCheck=false, updateInstallPrompt=false;
const instance=app.requestSingleInstanceLock();

function log(line) {
  try {
    const file=path.join(app.getPath('userData'),'updates.log');
    fs.mkdirSync(path.dirname(file),{recursive:true});
    if(fs.existsSync(file) && fs.statSync(file).size>1048576)
      fs.renameSync(file,file+'.previous');
    fs.appendFileSync(file,new Date().toISOString()+' '+String(line)+'\n','utf8');
  } catch {}
}
function inform(title,body) {
  log(title+': '+body);
  try {if(Notification.isSupported()) new Notification({title,body}).show();}catch{}
}
function voiceExe() {
  return path.join(process.resourcesPath,'voice','CapsWriterLite','CapsWriterLite.exe');
}
function startVoice() {
  const exe=voiceExe();
  if(!fs.existsSync(exe)){
    dialog.showErrorBox('CapsWriter Lite 安装不完整','找不到已打包的语音识别程序：\n'+exe);
    app.quit();
    return;
  }
  const started=Date.now();
  const child=spawn(exe,[],{
    cwd:path.dirname(exe),windowsHide:true,stdio:'ignore',
    env:{...process.env,CAPSWRITER_LAUNCHER_EXE:process.execPath}
  });
  voice=child;
  log('started resident Python engine pid='+child.pid);
  child.on('error',error=>{
    log('voice launch failed: '+error.message);
    dialog.showErrorBox('语音识别进程启动失败',error.message);
    app.quit();
  });
  child.on('exit',(code,signal)=>{
    log('voice exited: '+code+' / '+signal);
    voice=null;
    if(installing)return;
    if(Date.now()-started<5000 && code!==0)
      dialog.showErrorBox('语音输入进程异常退出',
        '请查看 %LOCALAPPDATA%\\CapsWriterLite\\capswriter.log，或从应用设置中选择正确的大模型目录。');
    // User chose "完全退出" in the Python tray: exit the updater too.
    app.quit();
  });
}
function stopVoice() {
  if(!voice)return;
  installing=true;
  const child=voice;voice=null;
  try {child.kill();}catch(e){log('voice close failed: '+e.message);}
}
function installerReady() {
  if(updateInstallPrompt)return;
  updateInstallPrompt=true;
  downloaded=true;
  dialog.showMessageBox({
    type:'info',title:'CapsWriter Lite 更新已下载',
    message:'新版本安装包已经下载完毕',
    detail:'现在安装将短暂关闭语音输入，升级后自动重新打开。\n原版模型、个人热词和 Quicker 设置都会保留。\n\n选择“下次退出时安装”可继续使用当前版本。',
    buttons:['立即安装并重启','下次退出时安装'],
    defaultId:1,cancelId:1,noLink:true
  }).then(choice=>{
    if(choice.response===0)installUpdate();
    else{updater.autoInstallOnAppQuit=true;inform('CapsWriter Lite','更新已准备好，将在退出程序时安装');}
  }).catch(e=>log(e.message));
}
function installUpdate() {
  if(!updater || !downloaded || installing)return;
  installing=true;
  stopVoice();
  log('installing downloaded NSIS update');
  try {updater.quitAndInstall(false,true);}catch(e){
    installing=false;log('install failed: '+e.stack);
    dialog.showErrorBox('更新安装失败',e.message);
  }
}
function createUpdater() {
  if(updater)return updater;
  const {autoUpdater}=require('electron-updater');
  updater=autoUpdater;
  updater.autoDownload=false;
  updater.autoInstallOnAppQuit=false;
  updater.disableDifferentialDownload=false;
  updater.disableWebInstaller=true;
  const {resumableDownload}=require('./update-download.cjs');
  const cacheDir=path.join(app.getPath('userData'),'update-downloads');
  updater.httpExecutor.cachedSession=session.defaultSession;
  // Same full-download fallback as CutPlayer: resume partial HTTP Range requests,
  // retry transient failures and verify SHA-512 before handing bytes to NSIS.
  updater.httpExecutor.download=(url,destination,options)=>resumableDownload(
    url,destination,options,{cacheDir,log,say:log,request:(uri,headers)=>{
      const req=net.request({url:String(uri),session:session.defaultSession});
      for(const [name,value] of Object.entries(headers||{}))req.setHeader(name,value);
      return req;
    }});
  updater.logger={
    info:m=>{log(m);const match=/^Full: (.*), To download: (.*)$/.exec(String(m));
      if(match)inform('CapsWriter Lite 差分更新','完整包 '+match[1]+'，预计仅下载 '+match[2]);},
    warn:m=>log('WARN '+m),
    error:m=>{log('ERROR '+m);if(String(m).startsWith('Cannot download differentially'))
      inform('CapsWriter Lite','差分更新不可用，自动回退完整安装包下载（支持断点续传）');},
    debug:m=>log('DEBUG '+m),
  };
  updater.on('error',e=>{
    log('updater error: '+e.stack);
    checking=false;
    if(manualCheck)dialog.showMessageBox({type:'error',
      message:'检查／下载更新失败',
      detail:e.message+'\n可从 GitHub Releases 手动下载安装包覆盖安装，保留模型与设置。',
      buttons:['关闭','打开下载页']}).then(x=>{
        if(x.response===1)shell.openExternal('https://github.com/'+OWNER+'/'+REPO+'/releases/latest');
      });
    manualCheck=false;
  });
  updater.on('update-not-available',()=>{
    checking=false;
    if(manualCheck)dialog.showMessageBox({type:'info',
      message:'当前已是最新安装版',detail:'版本：'+app.getVersion()});
    manualCheck=false;
  });
  updater.on('update-available',e=>inform('CapsWriter Lite','发现新版本 '+e.version+'，正在差分下载更新…'));
  updater.on('download-progress',p=>{
    const ratio=Math.round(p.percent);
    if(ratio%20===0)log('update download '+ratio+'%');
  });
  updater.on('update-downloaded',()=>{checking=false;manualCheck=false;installerReady();});
  updater.setFeedURL({provider:'github',owner:OWNER,repo:REPO});
  return updater;
}
async function checkUpdates(manual=false) {
  if(manual)manualCheck=true;
  if(!app.isPackaged){
    if(manual)dialog.showMessageBox({type:'info',message:'源码模式不支持在线更新'});
    return;
  }
  if(checking)return;
  if(downloaded){
    if(manual)dialog.showMessageBox({type:'info',message:'更新已经下载完毕',
      buttons:['现在安装并重启','稍后'],defaultId:1}).then(x=>{if(x.response===0)installUpdate();});
    return;
  }
  checking=true;
  try{
    createUpdater();
    const result=await updater.checkForUpdates();
    if(result && result.updateInfo &&
       require('semver').gt(result.updateInfo.version,app.getVersion())){
      // The updater chooses .blockmap chunks and falls back to full installer.
      await updater.downloadUpdate();
    }
  }catch(e){
    checking=false;
    log('checkUpdates error: '+(e.stack||e));
    if(manual)dialog.showMessageBox({type:'error',
       message:'无法检查或下载更新',detail:String(e.message)+'\n\n可从仓库 Release 下载完整 Setup.exe 安装包。'});
    manualCheck=false;
  }
}
if(!instance) {
  app.quit();
}else{
  app.on('second-instance',(_event,args)=>{
    if(args.includes('--check-for-updates'))checkUpdates(true);
  });
  app.on('before-quit',()=>{if(!installing)stopVoice();});
  app.whenReady().then(()=>{
    startVoice();
    if(process.argv.includes('--check-for-updates'))checkUpdates(true);
    // Auto-check silently after startup and at most every six hours.
    setTimeout(()=>checkUpdates(false),45000).unref?.();
    setInterval(()=>checkUpdates(false),6*60*60*1000).unref?.();
  }).catch(e=>{dialog.showErrorBox('CapsWriter Lite 启动失败',e.message);app.quit();});
}
