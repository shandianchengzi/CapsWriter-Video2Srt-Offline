const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
test('NSIS installed update settings remain versioned',()=>{
 const pkg=require('../../package.json');
 assert.equal(pkg.build.win.target[0],'nsis');
 assert.equal(pkg.build.nsis.oneClick,false);
 assert.equal(pkg.build.nsis.deleteAppDataOnUninstall,false);
 assert.match(pkg.build.nsis.artifactName,/Setup/);
 assert.equal(pkg.build.publish.provider,'github');
});
test('Python original-model directory remains outside installation',()=>{
 const src=fs.readFileSync(path.join(__dirname,'..','model_store.py'),'utf8');
 assert.match(src,/LOCALAPPDATA/);
 assert.match(src,/models\.zip/);
 const pkg=require('../../package.json');
 assert(!JSON.stringify(pkg.build.extraResources).includes('models.zip'));
});
