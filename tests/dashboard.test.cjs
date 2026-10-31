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

test('English fallback, Hebrew auto detection, overrides and custom names',()=>{
 const card=new Card();assert.equal(card.language,'en');assert.equal(card.t('Daily limit'),'Daily limit');
 card._hass={locale:{language:'he'}};assert.equal(card.language,'he');assert.equal(card.t('Daily limit'),'מגבלה יומית');
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
 card.hass={locale:{language:'he'}};assert.match(card.shadowRoot.innerHTML,/direction:rtl/);assert.match(card.shadowRoot.innerHTML,/מכסות ושירותי ענן/);
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
