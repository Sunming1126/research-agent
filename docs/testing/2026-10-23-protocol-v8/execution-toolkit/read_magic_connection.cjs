// Read exactly the already selected magic connection. No profile writes,
// plugin installation or credential changes. Default output is nonsecret.
const fs=require('node:fs');
const os=require('node:os');
const path=require('node:path');
const YAML=require('D:/deepseek/resources/app.asar/dsh/node_modules/yaml');

function connection() {
  const home=path.join(os.homedir(),'.dsh');
  const profile=YAML.parse(fs.readFileSync(path.join(home,'profiles/desktop/cordis.patch.yml'),'utf8'),{logLevel:'silent'});
  const selected=profile.find(x=>x.id==='agent-default-model')?.config;
  const route=profile.find(x=>x.id==='llm-pi-ai')?.config?.providers?.magic;
  if(selected?.provider!=='magic' || !route || route.api!=='openai-completions') throw new Error('MAGIC_CONFIGURATION_UNAVAILABLE');
  const url=new URL(route.baseURL);
  if(!['http:','https:'].includes(url.protocol)||url.username||url.password||url.search) throw new Error('UNSUPPORTED_MAGIC_URL');
  const ref=route.apiKeyEnv;
  if(typeof ref!=='string'||!/^[A-Za-z_][A-Za-z0-9_]*$/.test(ref)) throw new Error('UNSUPPORTED_MAGIC_CREDENTIAL_REFERENCE');
  const stored=YAML.parse(fs.readFileSync(path.join(home,'.credentials.yaml'),'utf8'),{logLevel:'silent'});
  if(stored.version!==1) throw new Error('UNSUPPORTED_CREDENTIAL_STORE');
  const key=stored.refs?.[ref]??process.env[ref];
  if(typeof key!=='string'||!key) throw new Error('MAGIC_CREDENTIAL_UNAVAILABLE');
  return {provider:'magic',model:selected.model,base_url:route.baseURL,api_key:key,headers:route.headers??{}};
}

try {
  const config=connection();
  if(process.env.ANIC_PRIVATE_CONFIG_PIPE==='parent-process-only' && process.argv.includes('--private-parent-pipe')) {
    // Caller MUST capture this anonymous stdout pipe in memory and never echo it.
    process.stdout.write(JSON.stringify(config));
  } else {
    process.stdout.write(JSON.stringify({provider:config.provider,model:config.model,
      hostname:new URL(config.base_url).hostname,credential_available:!!config.api_key,
      profile_modified:false,model_called:false}));
  }
} catch {
  process.stderr.write('Selected magic connection could not be resolved; no configuration or credentials displayed.');
  process.exitCode=1;
}
