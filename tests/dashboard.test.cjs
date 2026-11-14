const {test}=require('node:test');
const assert=require('node:assert/strict');
const vm=require('node:vm');
const fs=require('node:fs');
const path=require('node:path');
let Card;
const context={HTMLElement:class {attachShadow(){return {}; }},customElements:{get(){},define(name,value){Card=value;}},window:{},setInterval,clearInterval,Date,URL};
vm.runInNewContext(fs.readFileSync(path.join(__dirname,'../custom_components/quota_guard/frontend/quota-guard-card.js'),'utf8'),context);
test('an empty successful response shows reported zero without quota percentage or balance',()=>{
 const card=new Card();const m={type:'metric',key:'assistant',config:{id:'assistant',name:'Assistant',unit:'בקשות ביום',max_age:600},verified_limit:500,status:'no_activity_reported',percent:null,reporting:{state:'no_activity_reported',reported_used:0,checked_at:new Date().toISOString(),period:'today'}};
 assert.equal(card.fresh(m),false);assert.equal(card.reportedEmpty(m),true);
 const html=card.tile(m);assert.match(html,/0<small>requests reported today/);assert.doesNotMatch(html,/Usage unknown|% used/);
 m.reporting.checked_at='2020-01-01T00:00:00Z';assert.equal(card.reportedEmpty(m),false);
});
test('reference policies are not represented as measured usage or graphed',()=>{
 const card=new Card();card.data={metrics:{},inventory:[{entry_id:'entry',domain:'telegram_bot',coverage:'needs_setup',policy:{title:'Telegram',headline:'1 message/s',scope:'per chat',summary:'reference',limits:[],notes:[],source:'https://core.telegram.org/bots/faq'}}]};
 const row=card.rows()[0];assert.equal(row.type,'reference');const html=card.tile(row);
 assert.match(html,/no usage counter/);assert.doesNotMatch(html,/% used/);assert.doesNotMatch(card.detail(row),/<svg/);
});

test('English default, explicit Hebrew or auto, overrides and custom names',()=>{
 const card=new Card();assert.equal(card.language,'en');assert.equal(card.t('Daily limit'),'Daily limit');
 card._hass={locale:{language:'he'}};assert.equal(card.language,'en');card.setConfig({language:'auto'});assert.equal(card.language,'he');assert.equal(card.t('Daily limit'),'מגבלה יומית');
 card._hass={language:'iw-IL'};assert.equal(card.language,'he');
 card.setConfig({language:'en'});assert.equal(card.language,'en');
 card.setConfig({language:'auto'});card._hass={locale:{language:'fr'}};assert.equal(card.language,'en');
 assert.equal(card.name({config:{name:'השירות שלי'}}),'השירות שלי');
 const metric={config:{name:'Google Assistant — requests today',builtin_name:true}};
 assert.equal(card.name(metric),metric.config.name);card.setConfig({language:'he'});
 assert.equal(card.name(metric),'Google Assistant — בקשות היום');
 assert.throws(()=>card.setConfig({language:'de'}),/language/);
});
test('metric details and provider notes translate without changing measurement behavior',()=>{
 const card=new Card();const m={type:'metric',config:{id:'google_test',name:'Assistant',unit:'requests per day',kind:'rate_limit',enforce:true},verified_limit:500,status:'unknown',percent:null,provider_note:'Delayed provider measurement; not a billing cap or free character counter'};
 assert.match(card.detail(m),/API rate limit/);assert.doesNotMatch(card.detail(m),/[\u0590-\u05ff]/);
 card.setConfig({language:'he'});assert.match(card.detail(m),/מגבלת API/);assert.match(card.detail(m),/מדידה מהספק בהשהיה/);
});
test('render switches direction and refreshes an already loaded card on profile changes',()=>{
 const card=new Card();card.data={metrics:{},inventory:[],events:[]};card.loaded=true;
 card.shadowRoot={innerHTML:'',querySelector(){return {onclick:null,oninput:null,open:false};},querySelectorAll(){return [];}};
 card.render();assert.match(card.shadowRoot.innerHTML,/direction:ltr/);assert.match(card.shadowRoot.innerHTML,/Cloud quotas and services/);
 card.hass={locale:{language:'he'}};assert.match(card.shadowRoot.innerHTML,/direction:ltr/);card.setConfig({language:'auto'});card.hass={locale:{language:'he'}};assert.match(card.shadowRoot.innerHTML,/direction:rtl/);assert.match(card.shadowRoot.innerHTML,/מכסות ושירותי ענן/);
 card.setConfig({language:'en'});assert.match(card.shadowRoot.innerHTML,/direction:ltr/);
});
test('unavailable quota data is separate from the integration connection',()=>{
 const card=new Card();const row={type:'unknown',key:'translate',domain:'google_translate',title:'Google Translate text-to-speech',entry:{state:'loaded'}};
 const html=card.tile(row);assert.match(html,/Quota data unavailable/);assert.match(html,/Connected/);assert.match(html,/does not indicate a connection failure/);assert.doesNotMatch(html,/Source not connected/);
 assert.match(card.detail(row),/home-assistant.io\/integrations\/google_translate/);
 card.setConfig({language:'he'});assert.match(card.tile(row),/נתוני מכסה אינם זמינים/);assert.match(card.tile(row),/מחובר/);
});
test('usage without a limit has raw units and a graph, without percentage or cutoff',()=>{
 const card=new Card();const now=new Date().toISOString();const m={type:'metric',key:'tuya',status:'usage_only',percent:null,config:{id:'tuya',name:'Tuya',unit:'requests',entry_ids:['entry'],max_age:180,enforce:false},sample:{used:12,timestamp:now,period:'today'},points:[{at:now,used:12,period:'today'}]};
 assert.equal(card.usageFresh(m),true);assert.match(card.tile(m),/12<small>observed/);const html=card.detail(m);assert.match(html,/<svg/);assert.match(html,/12 requests/);assert.doesNotMatch(html,/90%|95%|12%|NaN/);
 m.sample.timestamp='2020-01-01T00:00:00Z';assert.match(card.tile(m),/stale/);assert.match(card.tile(m),/12<small>/);
});
test('details link mapped integrations and deduplicated automation dependencies',()=>{
 const card=new Card();card.data={inventory:[{entry_id:'one',domain:'github',title:'GitHub account'}],relationships:{available:true,entries:{one:[{entity_id:'automation.repo',name:'Repo alert',automation_id:'my id',entities:['sensor.repo'],devices:[]}]}}};
 const html=card.impact({config:{entry_ids:['one'],limit:5000,enforce:true}});
 assert.match(html,/\/config\/integrations\/integration\/github/);assert.match(html,/\/config\/automation\/edit\/my%20id/);assert.match(html,/Related automations remain enabled/);
});

test('loading an upgraded module replaces a legacy registered card without losing state',()=>{
 class LegacyCard {state(){return 'old';}}
 const instance=new LegacyCard();instance.config={language:'he'};instance.selected='saved-selection';
 let registered=LegacyCard;
 const scope={...context,customElements:{get(){return registered;},define(name,value){registered=value;}}};
 const code=fs.readFileSync(path.join(__dirname,'../custom_components/quota_guard/frontend/quota-guard-card.js'),'utf8');
 vm.runInNewContext('(function(){'+code+'})()',scope);
 assert.equal(registered,LegacyCard);assert.equal(instance.language,'he');assert.equal(instance.selected,'saved-selection');assert.equal(instance.t('Daily limit'),'מגבלה יומית');assert.equal(typeof instance.usageDetail,'function');
 const upgraded=registered.prototype.usageDetail;
 const older=code.replace(/const CARD_VERSION="[^"]+"/, 'const CARD_VERSION="0.4.0"');
 vm.runInNewContext('(function(){'+older+'})()',scope);
 assert.equal(registered.prototype.usageDetail,upgraded);
});
test('a provider repair shows the reset date and paused alerts without a numeric sample',()=>{
 const card=new Card();card.filter='attention';card.setConfig({language:'he'});
 const signal={entry_id:'imou',confirmed:true,expected_reset_date:'2026-12-01',source:'Home Assistant Repairs · IMOU',resources_url:'https://open.imoulife.com/consoleNew/resourceManage/myResource'};
 card.data={metrics:{},events:[],inventory:[{entry_id:'imou',title:'IMOU',domain:'imou_life',coverage:'needs_setup',provider_signal:signal}]};
 card.shadowRoot={innerHTML:'',querySelector(){return {open:false};},querySelectorAll(){return [];}};
 card.render();assert.match(card.shadowRoot.innerHTML,/2026-12-01/);assert.match(card.shadowRoot.innerHTML,/התראות נוספות מושהות/);
 const html=card.detail(card.rows()[0]);assert.match(html,/2026-12-01/);assert.doesNotMatch(html,/<svg|100%/);
});

function manualSourceCard(){const card=new Card();card._hass={states:{'sensor.usage':{state:'39.914',attributes:{checked_at:new Date().toISOString()}}}};card.editorDraft={id:'scanner',name:'Oracle safety estimate',unit:'%',kind:'free_allowance',usage_entity:'sensor.usage',usage_url:'/security-scanner/usage',limit:'100',timestamp_attribute:'checked_at',period_mode:'monthly',period_timezone:'UTC',max_age:'900',enforce:false,entry_ids:[]};return card;}
test('the source form builds a sensor monitor without a cutoff target',()=>{
 const card=manualSourceCard(),cfg=card.sourceConfig(card.editorDraft);
 assert.equal(cfg.enforce,false);assert.equal(cfg.entry_ids.length,0);assert.equal(cfg.limit,100);assert.equal(cfg.period_mode,'monthly');assert.equal(cfg.usage_url,'/security-scanner/usage');
 assert.match(card.sourceEditor(),/name="usage_entity"/);assert.match(card.previewSource(),/39.914/);assert.match(card.previewSource(),/\d{4}-\d{2}/);
});
test('source validation rejects unsafe links, absent sensors and unmapped cutoff',()=>{
 const card=manualSourceCard();
 for(const change of [{usage_url:'javascript:alert(1)'},{usage_url:'https://user:pass@example.com/'},{enforce:true},{usage_entity:'sensor.absent'},{limit:'NaN'},{max_age:5}])assert.throws(()=>card.sourceConfig({...card.editorDraft,...change}));
 assert.equal(card.safeUsageUrl('//evil.example'), '');assert.equal(card.safeUsageUrl('/\\evil.example'), '');
});
test('source link and editor escape user labels and retain missing data',()=>{
 const card=manualSourceCard();card.editorDraft.name='<img src=x onerror=alert(1)>';
 assert.doesNotMatch(card.sourceEditor(),/<img src=x/);
 delete card._hass.states['sensor.usage'];assert.match(card.previewSource(),/Missing/);
 assert.equal(card.sourceLink({config:{usage_url:'javascript:alert(1)'}}),'');
 assert.match(card.sourceLink({config:{usage_url:'/security-scanner/usage'}}),/rel="noopener noreferrer"/);
});

test('a selected limit sensor takes precedence and cannot fall back to an old fixed allowance',()=>{
 const card=new Card(),metric={config:{limit:100,limit_entity:'sensor.limit'},sample:{limit:'200'}};
 assert.equal(card.limit(metric),200);metric.sample.limit='unavailable';assert.equal(card.limit(metric),null);metric.sample.limit=null;assert.equal(card.limit(metric),null);
});
test('refresh keeps a source draft and its focused controls intact',async()=>{
 const card=new Card();card.editorOpen=true;card.editorDraft={name:'Unsaved source'};let renders=0;card.render=()=>renders++;card._hass={callWS:async()=>({metrics:{},inventory:[]})};
 await card.refresh();assert.equal(card.editorDraft.name,'Unsaved source');assert.equal(renders,0);assert.equal(card.loading,false);
});

test('retired sources stay visible with a remedy, never as current usage',()=>{
 const card=new Card();card.setConfig({language:'he'});
 card.data={metrics:{},inventory:[],retired_metrics:{legacy:{config:{id:'legacy',name:'Google'},sample:{used:99,limit:100},status:'ok',percent:99,diagnostics:[{severity:'warning',message:'This legacy source stopped after the credential isolation update.',action:'Connect GitHub or Google in Quota Guard settings using credentials supplied directly to this plugin.'}]}}};
 const row=card.rows()[0];assert.equal(row.type,'retired');assert.equal(card.needsAttention(row),true);
 assert.match(card.tile(row),/המקור הישן הופסק/);assert.doesNotMatch(card.tile(row),/99%|99 %/);
 assert.match(card.detail(row),/סימון המקור הישן כטופל/);assert.match(card.detail(row),/\/config\/integrations\/integration\/quota_guard/);
});

test('configuration problems remain visible even when a different tile filter is selected',()=>{
 const card=new Card();card.filter='reference';card.setConfig({language:'he'});
 card.data={events:[],inventory:[],metrics:{bad:{config:{id:'bad',name:'Bad sensor',entry_ids:[]},diagnostics:[{severity:'error',message:'A configured sensor does not exist.',action:'Select an existing sensor or restore the missing entity.',field:'usage_entity',details:{entity_id:'sensor.missing'}}]}}};
 card.shadowRoot={innerHTML:'',querySelector(){return {open:false};},querySelectorAll(){return [];}};
 card.render();assert.match(card.shadowRoot.innerHTML,/Bad sensor/);assert.match(card.shadowRoot.innerHTML,/חיישן שנבחר בהגדרות אינו קיים/);
 const detail=card.detail(card.rows()[0]);assert.match(detail,/sensor\.missing/);assert.match(detail,/חיישן שימוש/);
});

test('ordinary quota coverage gaps are not presented as configuration errors',()=>{
 const card=new Card();card.data={metrics:{},inventory:[{domain:'service',title:'Service',coverage:'needs_setup',state:'loaded'}]};
 const rows=card.rows();assert.equal(card.needsAttention(rows[0]),false);assert.match(card.diagnosticsPanel(rows),/no detected problems/);assert.doesNotMatch(card.diagnosticsPanel(rows),/Show details and fix/);
});

test('diagnostic details escape entity names and distinguish future from stale measurements',()=>{
 const card=new Card();const m={diagnostics:[{severity:'error',message:'The measurement timestamp is in the future.',action:'Correct the source clock or timestamp mapping; do not substitute the current time.',field:'timestamp_attribute',details:{attribute:'<script>alert(1)</script>'}}],sample:{timestamp:'2099-01-01T00:00:00Z'}};
 assert.match(card.reason(m),/in the future/);const detail=card.diagnosticsDetail(m);assert.doesNotMatch(detail,/<script>/);assert.match(detail,/&lt;script&gt;/);
});

test('failed local integrations appear in diagnostics independently of cloud coverage',()=>{
 const card=new Card();card.data={metrics:{},inventory:[{domain:'local_test',title:'Local',coverage:'local',state:'setup_retry',diagnostics:[{severity:'error',message:'Home Assistant reports a problem loading this integration.',action:'Open the integration.'}]}]};
 assert.equal(card.rows().length,1);assert.equal(card.needsAttention(card.rows()[0]),true);assert.match(card.diagnosticsPanel(card.rows()),/problem loading/);
});

test('a not-loaded backend gives an actionable message instead of a raw websocket error',async()=>{
 const card=new Card();card.render=()=>{};card._hass={callWS:async()=>{throw {code:'unknown_command',message:'Unknown command'};}};
 await card.refresh();assert.match(card.error,/Quota Guard is not loaded/);
});
