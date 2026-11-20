"""Judgment AI mode controls (#1050) and technical evidence in Settings.

Evidence class: DOM behaviour of the shipped renderer in a Node VM with
synthetic settings read models and a recording API stub.  No server, model,
CLI or credential is involved.
"""
from pathlib import Path
import shutil
import subprocess

import pytest

ROOT = Path(__file__).parents[1] / "src" / "personal_agent" / "web"
HTML = (ROOT / "index.html").read_text(encoding="utf-8")
APP = (ROOT / "app.js").read_text(encoding="utf-8")

DOM_SETUP = r"""
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const app=fs.readFileSync(process.argv[1],'utf8'),ids=new Map();
function matches(node,selector){
 if(selector.includes(','))return selector.split(',').some(part=>matches(node,part));
 if(selector.includes(':not(:disabled)')){if(node.disabled)return false;selector=selector.replace(':not(:disabled)','');}
 if(selector.endsWith(':checked')){if(!node.checked)return false;selector=selector.slice(0,-8);}
 const tag=selector.match(/^[a-z]+/);if(tag&&node.tag!==tag[0])return false;
 for(const match of selector.matchAll(/\.([\w-]+)/g))if(!node.className.split(' ').includes(match[1]))return false;
 for(const match of selector.matchAll(/\[([\w-]+)(?:="([^"]*)")?\]/g)){
  const key=match[1],value=key.startsWith('data-')?node.dataset[key.slice(5).replace(/-([a-z])/g,(_,letter)=>letter.toUpperCase())]:node[key]??node.attrs[key];
  if(match[2]===undefined?!value:String(value)!==match[2])return false;
 }return true;
}
class Element {
 constructor(tag){this.tag=tag;this.children=[];this.dataset={};this.attrs={};this.className='';this.hidden=false;this._text='';this.value='';this.disabled=false;this.parentNode=null;this._root=false;}
 set id(value){this._id=value;ids.set(value,this);} get id(){return this._id;}
 set textContent(value){this.replaceChildren();this._text=String(value);} get textContent(){return this._text+this.children.map(node=>typeof node==='string'?node:node.textContent).join('');}
 append(...nodes){for(const node of nodes){if(typeof node!=='string'){if(node.parentNode)node.parentNode.children=node.parentNode.children.filter(item=>item!==node);node.parentNode=this;}this.children.push(node);}}
 replaceChildren(...nodes){for(const child of this.children)if(typeof child!=='string')child.parentNode=null;this._text='';this.children=[];this.append(...nodes);}
 setAttribute(key,value){this.attrs[key]=String(value);} getAttribute(key){return this.attrs[key];} focus(){document.activeElement=this;} get isConnected(){return this.parentNode?this.parentNode.isConnected:this._root;}
 remove(){if(this.parentNode)this.parentNode.children=this.parentNode.children.filter(child=>child!==this);this.parentNode=null;}
 replaceWith(node){if(this.parentNode)this.parentNode.replaceChild(node,this);}
 replaceChild(node,old){const index=this.children.indexOf(old);if(index<0)return;if(node.parentNode)node.parentNode.children=node.parentNode.children.filter(child=>child!==node);this.children[index]=node;node.parentNode=this;old.parentNode=null;}
 scrollIntoView(options){this.lastScroll=options;}
 contains(node){return this===node||descendants(this).includes(node);}
 showModal(){this.open=true;} close(){this.open=false;this.onclose?.();}
 setSelectionRange(start,end){this.selectionStart=start;this.selectionEnd=end;}
 get classList(){const node=this;return {toggle(name,on){const parts=new Set(node.className.split(' ').filter(Boolean));if(on===undefined)on=!parts.has(name);if(on)parts.add(name);else parts.delete(name);node.className=[...parts].join(' ');},add(name){this.toggle(name,true);},remove(name){this.toggle(name,false);},contains(name){return node.className.split(' ').includes(name);}};}
 querySelectorAll(selector){return descendants(this).filter(node=>matches(node,selector));}
 querySelector(selector){return this.querySelectorAll(selector)[0]||null;}
}
function descendants(node){return node.children.flatMap(child=>typeof child==='string'?[]:[child,...descendants(child)]);}
const $=id=>ids.get(id),document={activeElement:null,getElementById:$,createElement:tag=>new Element(tag)};
const dialog=new Element('dialog');dialog.id='judgment-chooser';dialog._root=true;
for(const [tag,id] of [['div','judgment-follow'],['p','judgment-feedback'],['section','decision-route'],['button','judgment-chooser-close']]){
 const node=new Element(tag);node.id=id;dialog.append(node);
}
const opener=new Element('button');opener._root=true;
const part=(start,end)=>app.slice(app.indexOf(start),app.indexOf(end));
const source=part('const LANGUAGES=','function normalizeEndpoint(')+part('const DECISION_ROLE=','function traceWindow(')+
 part('function element(','function setError(')+part('function openJudgmentChooser(','const ENGINE_LOGIN_TEXT=')+
 part('const DECISION_TRANSPORT_LABEL=','function openMobileDetail(');
const calls=[],modelReplies={};let responder=null,refreshes=0;
const deferred=()=>{let resolve;const promise=new Promise(yes=>{resolve=yes;});return {promise,resolve};};
const ctx={document,$,console,safeTime:()=>'T',
 api:async(path,body)=>{
  calls.push({path,body});
  if(responder)return responder(path,body);
  if(path.endsWith('/models'))return {route:body.route,source:'fixture',models:modelReplies[body.route]||[]};
  return {};
 },
 refresh:async()=>{refreshes++;},busy:async(button,fn)=>fn(),
 setError:(id,error)=>{$(id).textContent=error?.message||String(error||'');},setFeedback:(id,text)=>{$(id).textContent=text||'';}};
vm.createContext(ctx);vm.runInContext(source,ctx);vm.runInContext("setLanguage('ko')",ctx);
const box=$('decision-route');
const fixtures=(overrides={})=>({
 mode:'follow_main',main:'openai',active:{source:'follow',transport:'direct_api',available:true,requested_model:'light'},
 follow:{available:true,model:'light',candidates:['light','backup'],destination:'api.example.invalid'},
 direct_api:{configured:false},
 apis:[
  {id:'openai',name:'OpenAI API',configured:true,ranked_models:['light','backup'],model_efforts:{light:['low','medium']}},
  {id:'anthropic',name:'Anthropic API',configured:false,ranked_models:['haiku']},
  {id:'openrouter',name:'OpenRouter',configured:true,ranked_models:['openai/light']}
 ],
 jev:{configured:false,model:'jev-latest'},
 subscription_cli:[
  {id:'codex',name:'Codex',installed:true,login:'signed-in',model_selection:'unchecked',ranked_models:['small'],model_efforts:{small:['low']}},
  {id:'claude-code',name:'Claude Code',installed:false,login:'unchecked',model_selection:'unchecked'}
 ],...overrides
});
const render=(decision_route,reset=true)=>ctx.renderDecisionRoute({decision_route},reset);
const form=()=>box.querySelector('form');
const find=(selector)=>box.querySelector(selector);
const provider=()=>find('select[aria-label="판단 AI"]');
const model=()=>find('select[aria-label="직접 선택할 모델 이름"]');
const effort=()=>find('select[aria-label="추론 강도"]');
const submitButton=()=>form().querySelector('button[type="submit"]');
const submit=()=>form().onsubmit({preventDefault(){}});
const button=(label,root=box)=>descendants(root).find(node=>node.tag==='button'&&node.textContent===label);
const click=(label,root=box)=>{const target=button(label,root);assert(target,label);return target.onclick({currentTarget:target});};
const select=(control,value)=>{control.value=value;return control.onchange();};
const choosePolicy=value=>{
 const radios=form().querySelectorAll('input[type="radio"]'),chosen=radios.find(node=>node.value===value);
 assert(chosen,value);for(const radio of radios)radio.checked=radio===chosen;
 return chosen.onchange();
};
const lastCall=(path,body)=>assert.deepEqual(JSON.parse(JSON.stringify(calls.at(-1))),{path:'/api/decision-route/'+path,body});
const options=select=>select.children.map(option=>option.value);
const listed=[
 {id:'full',rank:null,efforts:[],recommended:false},
 {id:'other-light',rank:1,efforts:['low'],recommended:true},
 {id:'light',rank:0,efforts:['low','medium'],recommended:true}
];
"""


def run_dom(script):
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node is required for DOM checks")
    result = subprocess.run(
        [node, "-e", DOM_SETUP + "\n(async()=>{\n" + script + "\n})().catch(error=>{console.error(error);process.exit(1);});",
         str(ROOT / "app.js")],
        capture_output=True, text=True, timeout=20,
    )
    assert result.returncode == 0, result.stderr + result.stdout


def test_decision_route_section_is_a_separate_role_in_the_ai_settings():
    assert '<section id="decision-route"' in HTML
    ai = HTML[HTML.index('id="settings-ai"'):HTML.index('id="settings-files"')]
    assert 'id="decision-route"' in ai
    assert "renderDecisionRoute(settings)" in APP
    block = APP[APP.index("const DECISION_TRANSPORT_LABEL="):APP.index("function openMobileDetail(")]
    assert "/api/ai-route" not in block and "/api/subscription-engines/connect" not in block
    assert "/api/decision-route/activate" in block and "/api/decision-route/credential" in block


def test_provider_select_tracks_saved_mode_and_api_key_state():
    run_dom(r"""
render(fixtures());ctx.openJudgmentChooser(opener);
assert.equal(document.activeElement,provider());
assert.deepEqual(options(provider()),['follow_main','openai','anthropic','openrouter','jev','codex','claude-code','off']);
assert.equal(provider().value,'follow_main');
assert.equal(provider().children[2].textContent,'Anthropic API · 키 필요');
assert.equal(provider().children[2].disabled,false);
assert.equal(provider().children[6].textContent,'Claude Code · 설치 안 됨');
assert.equal(provider().children[6].disabled,true);
assert.equal(box.querySelectorAll('select').length,1);
assert(box.textContent.includes('후보 모델: light, backup'));
assert.equal(calls.length,0,'rendering and opening never fetch models');
select(provider(),'anthropic');
assert.equal(provider().value,'anthropic','the select shows the chosen provider');
assert(box.textContent.includes('키 필요 — 기본 AI 설정에서 Anthropic API 키를 저장하세요.'));
assert.equal(submitButton().disabled,true);
assert(!find('input[type="password"]'));

const route=fixtures({mode:'explicit',active:{transport:'direct_api',api:'anthropic',provider:'anthropic',source:'owner',requested_model:'haiku',effort:'low',model_policy:'explicit'},
 effective:{text:'effective Anthropic judgment'}});
route.apis[1].configured=true;
render(route);
assert.equal(provider().value,'anthropic');
assert.equal(provider().children[2].textContent,'Anthropic API');
assert.equal(submitButton().disabled,false,'apis configured is authoritative even when direct_api is false');
assert(!box.textContent.includes('키 필요 —'));
assert($('judgment-follow').textContent.includes('사용 방식Anthropic API · haiku · 추론 강도: low'));
assert($('judgment-follow').textContent.includes('effective Anthropic judgment'));
assert.deepEqual(options(model()),['','haiku'],'an active pin remains available without a stored list');
assert(!effort());
render(fixtures({mode:'explicit',active:{transport:'direct_api',source:'owner',requested_model:'legacy'}}));
assert.equal(provider().value,'openai');
assert($('judgment-follow').textContent.includes('OpenAI API · legacy'));
render(fixtures({mode:'off',active:{transport:'off',source:'owner'}}));
assert.equal(provider().value,'off');
assert.equal(box.querySelectorAll('input[type="radio"]').length,0);
await submit();lastCall('activate',{transport:'off'});
""")


def test_follow_auto_and_pin_use_current_main_catalogue_and_effort():
    run_dom(r"""
const route=fixtures({main:'anthropic'});
modelReplies.anthropic=listed;
render(route);
await submit();lastCall('activate',{transport:'follow_main'});
assert($('judgment-feedback').textContent.includes('백그라운드에서 시작했습니다'));
assert(!model()&&!effort());
await choosePolicy('explicit');lastCall('models',{route:'anthropic'});
assert.equal(model().tag,'select');
assert(!find('datalist')&&!find('input[type="text"]'));
assert.deepEqual(options(model()),['','light','other-light','full']);
assert.equal(model().children[1].textContent,'light · 추천');
assert.equal(model().children[2].textContent,'other-light · 추천');
assert.equal(model().children[3].textContent,'full');
assert.deepEqual(options(effort()),['','low','medium']);
assert.equal(effort().children[0].textContent,'기본');
select(model(),'full');assert(!effort());
select(model(),'light');select(effort(),'medium');
await submit();lastCall('activate',{transport:'follow_main',model:'light',effort:'medium'});
await choosePolicy('fastest_qualified');assert(!effort()&&!model());
await submit();lastCall('activate',{transport:'follow_main'});
await choosePolicy('explicit');
assert.equal(calls.filter(call=>call.path.endsWith('/models')).length,1);
await click('모델 목록 새로고침');lastCall('models',{route:'anthropic'});
assert.equal(calls.filter(call=>call.path.endsWith('/models')).length,2);

render({...route,follow_pin:{model:'unlisted-pin',effort:'low'},follow:{...route.follow,pinned:true},
 active:{...route.active,requested_model:'unlisted-pin',effort:'low',model_policy:'explicit'}});
assert.equal(model().value,'unlisted-pin');
assert($('judgment-follow').textContent.includes('기본 AI 따라가기 · unlisted-pin (고정)'));
assert(!effort(),'unsupported effort is not exposed for an unlisted model');
""")


def test_configured_anthropic_submits_pin_and_optional_effort():
    run_dom(r"""
const route=fixtures({
 model_lists:{anthropic:{source:'saved',models:[{id:'haiku',rank:0,recommended:true,efforts:['low']},{id:'opus',efforts:[]}]}},
 active:{transport:'off'},mode:'off'
});
route.apis[1].configured=true;
route.apis[1].model_efforts={opus:['high']};
render(route);select(provider(),'anthropic');
await choosePolicy('explicit');assert.equal(calls.length,0,'a saved list does not need an automatic refresh');
assert(submitButton().disabled,'pick a model before submitting a pin');
select(model(),'haiku');assert(effort());assert.equal(effort().value,'');
await submit();lastCall('activate',{transport:'direct_api',api:'anthropic',model:'haiku'});
select(model(),'opus');assert.deepEqual(options(effort()),['','high']);
select(effort(),'high');await submit();lastCall('activate',{transport:'direct_api',api:'anthropic',model:'opus',effort:'high'});
await choosePolicy('fastest_qualified');await submit();lastCall('activate',{transport:'direct_api',api:'anthropic'});
""")


def test_cli_restrictions_and_engine_default_policy():
    run_dom(r"""
const route=fixtures();
route.subscription_cli[1].installed=true;
route.subscription_cli[1].login='signed-out';
render(route);assert(provider().children[6].disabled);
assert(provider().children[6].textContent.includes('로그인 안 됨'));
route.subscription_cli[1].isolated_deployment=true;
render(route);assert(provider().children[6].textContent.includes('지원 안 함'));
route.subscription_cli[1].isolated_deployment=false;
route.subscription_cli[1].login='signed-in';
route.subscription_cli[1].tool_surface='refused';route.subscription_cli[1].tool_surface_detail='feature';
render(route);assert(provider().children[6].disabled);
assert(provider().children[6].textContent.includes('켜진 기능: feature'));

select(provider(),'codex');
assert.deepEqual(form().querySelectorAll('input[type="radio"]').map(node=>node.value),['fastest_qualified','explicit','engine_default']);
await submit();lastCall('activate',{transport:'subscription_cli',engine:'codex',model_policy:'fastest_qualified'});
await choosePolicy('engine_default');await submit();
lastCall('activate',{transport:'subscription_cli',engine:'codex',model_policy:'engine_default'});
modelReplies.codex=[{id:'small',rank:0}];
await choosePolicy('explicit');lastCall('models',{route:'codex'});
select(model(),'small');select(effort(),'low');await submit();
lastCall('activate',{transport:'subscription_cli',engine:'codex',model_policy:'explicit',model:'small',effort:'low'});
route.subscription_cli[0].model_selection='unsupported';
render(route);select(provider(),'codex');
assert.deepEqual(form().querySelectorAll('input[type="radio"]').map(node=>node.value),['engine_default']);
await submit();lastCall('activate',{transport:'subscription_cli',engine:'codex',model_policy:'engine_default'});
""")


def test_openai_and_jev_credentials_and_jev_model_selector():
    run_dom(r"""
const route=fixtures({mode:'off',active:{transport:'off'}});
route.apis[0].configured=false;
route.direct_api.configured=true;
render(route);select(provider(),'openai');
assert(submitButton().disabled,'legacy direct_api state cannot enable activation');
click('전용 키 설정');const key=find('input[type="password"]');
key.value='synthetic';
const changed=structuredClone(route);
Object.assign(changed.apis[0],{has_decision_key:true,configured:true});
responder=()=>changed;
await key.parentNode.onsubmit({preventDefault(){}});
lastCall('credential',{transport:'direct_api',key:'synthetic'});
assert.equal(provider().value,'openai');assert(!submitButton().disabled);
assert(!find('input[type="password"]'));assert(button('키 변경'));
responder=null;

select(provider(),'jev');click('전용 키 설정');
const jevKey=find('input[type="password"]');jevKey.value='synthetic';
const withJev=structuredClone(changed);withJev.jev.configured=true;
responder=()=>withJev;
await jevKey.parentNode.onsubmit({preventDefault(){}});
lastCall('credential',{transport:'jev',key:'synthetic',model:'jev-latest'});
assert(model(),'saving the key exposes the TypeSafe picker without resetting the provider');
assert.equal(model().value,'jev-latest');
assert(!effort());assert(!find('input[type="text"]'));
responder=null;modelReplies.jev=[{id:'jev-listed'}];
await click('모델 목록 새로고침');lastCall('models',{route:'jev'});
select(model(),'jev-listed');assert(submitButton().disabled,'saving the choice and activating Jev are separate');
const jevModelForm=box.querySelectorAll('form').find(form=>form.contains(model()));
await jevModelForm.onsubmit({preventDefault(){}});
lastCall('credential',{transport:'jev',model:'jev-listed'});
await submit();lastCall('activate',{transport:'jev'});
""")


def test_model_and_key_drafts_keep_focus_and_survive_polling():
    run_dom(r"""
const route=fixtures({model_lists:{openai:{source:'saved',models:listed}}});
render(route);ctx.openJudgmentChooser(opener);
await choosePolicy('explicit');select(model(),'other-light');
const draft=model();draft.focus();
const listing=deferred();responder=()=>listing.promise;
const refresh=click('모델 목록 새로고침');
render({...route,effective:{text:'updated during polling'}},false);
assert.equal(model(),draft);assert.equal(document.activeElement,draft);
listing.resolve({route:'openai',source:'fresh',models:[{id:'new-recommended',recommended:true,efforts:['low']}]});
await refresh;responder=null;
assert.equal(model(),draft);assert.equal(model().value,'other-light');
assert(options(model()).includes('new-recommended'));
assert.equal(document.activeElement,draft);
assert($('judgment-follow').textContent.includes('updated during polling'));

select(provider(),'openai');assert.equal(provider().value,'follow_main');
assert($('decision-discard').textContent.includes('변경 사항 버리기'));
click('계속 편집',$('decision-discard'));assert.equal(model().value,'other-light');
select(provider(),'openai');click('변경 사항 버리기',$('decision-discard'));assert.equal(provider().value,'openai');
click('전용 키 설정');const secret=find('input[type="password"]');
secret.value='draft-key';secret.focus();secret.setSelectionRange(2,5);
render({...route,effective:{text:'polled again'}},false);
assert.equal(find('input[type="password"]'),secret);
assert.equal(document.activeElement,secret);assert.equal(secret.selectionStart,2);assert.equal(secret.selectionEnd,5);
$('judgment-chooser-close').onclick();assert(dialog.open);
click('변경 사항 버리기',$('decision-discard'));
assert(!dialog.open);assert.equal(document.activeElement,opener);
assert(!ctx.decisionDraftDirty());assert.equal(provider().value,'follow_main');
""")


def test_changed_follow_main_retains_draft_and_requires_reopening():
    run_dom(r"""
const route=fixtures({model_lists:{openai:{models:listed}}});
render(route);await choosePolicy('explicit');select(model(),'other-light');
const draft=model();
const next={...route,main:'anthropic'};
render(next,false);
assert.equal(model(),draft);assert(submitButton().disabled);
assert(box.textContent.includes('설정 정보가 바뀌어'));
await submit();assert.equal(calls.length,0);
click('현재 설정으로 다시 열기');click('변경 사항 버리기',$('decision-discard'));
assert(!model());assert(!submitButton().disabled);
await choosePolicy('explicit');lastCall('models',{route:'anthropic'});
""")


def test_pinned_model_list_auto_fetch_retries_only_on_explicit_refresh():
    run_dom(r"""
const route=fixtures({main:'claude-code',follow:{available:true,candidates:['haiku']}});
render(route);ctx.openJudgmentChooser(opener);
responder=()=>{throw new Error('model list temporarily unavailable');};
await choosePolicy('explicit');lastCall('models',{route:'claude-code'});
assert($('decision-route-feedback').textContent.includes('temporarily unavailable'));
await choosePolicy('fastest_qualified');await choosePolicy('explicit');
assert.equal(calls.length,1);
responder=null;modelReplies['claude-code']=[{id:'haiku',rank:0}];
await click('모델 목록 새로고침');lastCall('models',{route:'claude-code'});
assert(options(model()).includes('haiku'));
""")


def test_failed_activation_and_same_session_edits_keep_the_draft():
    run_dom(r"""
const route=fixtures({model_lists:{openai:{models:listed}}});
render(route);await choosePolicy('explicit');
responder=()=>{throw new Error('synthetic refused choice');};
await submit();
assert.equal(refreshes,0);assert($('judgment-feedback').textContent.includes('synthetic refused choice'));
assert.equal(model().value,'light');

const activation=deferred();responder=()=>activation.promise;
const request=submit();lastCall('activate',{transport:'follow_main',model:'light'});
select(model(),'other-light');
activation.resolve({...route,follow_pin:{model:'light'}});
await request;
assert.equal(model().value,'other-light');
assert(ctx.decisionDraftDirty());assert($('judgment-feedback').textContent.includes('백그라운드에서 시작했습니다'));
""")


def test_qualified_model_speed_and_technical_provenance():
    run_dom(r"""
const route=fixtures({
 active:{source:'follow',available:true,transport:'subscription_cli',model_policy:'fastest_qualified',requested_model:'qualified-model',
  destination:'qualified.example.invalid',qualification:{model:'qualified-model',p50_seconds:3.214,p95_seconds:4.9,
   compared:[{model:'qualified-model',qualified:true,p50_seconds:3.214},{model:'backup',qualified:true,p50_seconds:11.2},{model:'failed',qualified:false}]}},
 effective:{text:'Qualified model is active'}
});
render(route);
let text=$('judgment-follow').textContent;
assert(text.includes('기본 AI 따라가기 · qualified-model'));
assert(!text.includes('light'));
assert(text.includes('Qualified model is active'));
assert(text.includes('qualified.example.invalid'));
assert(text.includes('응답 시간 중앙값 3.2초, 95% 4.9초.'));
assert(text.includes('비교한 후보: backup 11.2초, failed 검사 미통과'));
render(fixtures({active:{transport:'none'},effective:{text:'Qualification pending'}}));
text=$('judgment-follow').textContent;
assert(text.includes('Qualification pending'));
assert(!text.includes('light')&&!text.includes('중앙값'));
assert(box.textContent.includes('후보 모델: light, backup'));
assert.equal(calls.length,0);

const facts=Object.fromEntries(ctx.decisionFacts({purpose:'conversation-followup',outcome:'decided',route:'subscription_cli',engine:'codex',model_policy:'explicit',requested_model:'small',observed_model:'not reported',elapsed_seconds:1.5},{relation:{kind:'retry',work_id:'w0'}}));
assert.equal(facts['판단 경로'],'subscription_cli / codex');assert.equal(facts['모델 정책'],'explicit');assert.equal(facts['요청 모델'],'small');assert.equal(facts['관측 모델'],'보고되지 않음');
assert.equal(facts['역할'],'대화 해석');assert.equal(facts['기록된 관계'],'retry → w0');
const legacy=Object.fromEntries(ctx.decisionFacts({purpose:'p',outcome:'decided',model:'requested',observed_model:'observed'},{}));
assert.equal(legacy['요청 모델'],'requested');assert.equal(legacy['관측 모델'],'observed');assert.equal(legacy['판단 경로'],'direct_api');
assert(!('기록된 관계' in legacy));
const confident=Object.fromEntries(ctx.decisionFacts({purpose:'p',outcome:'decided',confidence:0.874},{}));
assert.equal(confident['확신도'],'87% · 모델이 보고한 값, 보정되지 않음');assert(!('확신도' in legacy));
""")
