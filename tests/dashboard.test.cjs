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
 const html=card.tile(m);assert.match(html,/0<small>בקשות שדווחו היום/);assert.doesNotMatch(html,/הניצול אינו ידוע|% ניצול/);
 m.reporting.checked_at='2020-01-01T00:00:00Z';assert.equal(card.reportedEmpty(m),false);
});
test('reference policies are not represented as measured usage or graphed',()=>{
 const card=new Card();card.data={metrics:{},inventory:[{entry_id:'entry',domain:'telegram_bot',coverage:'needs_setup',policy:{title:'Telegram',headline:'1 message/s',scope:'per chat',summary:'reference',limits:[],notes:[],source:'https://core.telegram.org/bots/faq'}}]};
 const row=card.rows()[0];assert.equal(row.type,'reference');const html=card.tile(row);
 assert.match(html,/ללא מונה שימוש/);assert.doesNotMatch(html,/% ניצול/);assert.doesNotMatch(card.detail(row),/<svg/);
});
