"""Connection presentation regressions; no provider, credentials or service writes."""
from html.parser import HTMLParser
from pathlib import Path
import json
import shutil
import subprocess
import unittest

WEB = Path(__file__).parents[1] / "src" / "personal_agent" / "web"


class Elements(HTMLParser):
    def __init__(self, text):
        super().__init__()
        self.items = []
        self.feed(text)

    def handle_starttag(self, tag, attrs):
        self.items.append((tag, dict(attrs)))


NODE_CHECKS = r"""
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const app = fs.readFileSync(process.argv[1], 'utf8');
const ids = new Map();
class Element {
 constructor(tag) { this.tag=tag; this.children=[]; this.dataset={}; this.attrs={}; this.value=''; this.disabled=false; this._text=''; }
 set id(value) { this._id=value; ids.set(value,this); }
 get id() { return this._id; }
 set textContent(value) { this._text=String(value); this.children=[]; }
 get textContent() { return this._text+this.children.map(item=>typeof item==='string'?item:item.textContent).join(''); }
 append(...items) { this.children.push(...items); }
 replaceChildren(...items) { this._text=''; this.children=[]; this.append(...items); }
 setAttribute(key,value) { this.attrs[key]=value; }
}
for(const id of ['active-ai','subscription-engines','settings-panel','model-name','api-key']) {
 const node=new Element('div');node.id=id;
}
const calls=[];let refreshes=0,fail=false;
const ctx={document:{getElementById:id=>ids.get(id),createElement:tag=>new Element(tag)},
 $:id=>ids.get(id),
 api:async(path,body)=>{calls.push({path,body});if(fail)throw new Error('synthetic failure');return {};},
 refresh:async()=>{refreshes++;}};
const line=prefix=>app.split('\n').find(value=>value.startsWith(prefix));
const source=[line('function error('),line('async function busy('),line('function element('),
 line('const providerNames='),line('function displayProvider('),
 app.slice(app.indexOf('function showExecutionConnection('),app.indexOf('function showOnboarding('))].join('\n');
assert(!source.includes('undefined\n'));
vm.runInNewContext(source,ctx);
const selected=id=>({selected:id,engines:[
 {id:'codex',name:'Codex',installed:true,connected:id==='codex',login_command:'codex login',login_url:'https://example.invalid/codex'},
 {id:'claude-code',name:'Claude Code',installed:true,connected:id==='claude-code',login_command:'claude',login_url:'https://example.invalid/claude'}]});
const model={provider:'openai',endpoint:'https://api.openai.com/v1',model:'synthetic-api-model'};
const settings={model,model_ready:true,model_test:{time:1700000000},subscription_engines:selected('codex')};
const text=id=>ids.get(id).textContent;
const descendants=node=>node.children.flatMap(item=>typeof item==='string'?[]:[item,...descendants(item)]);
const buttons=()=>descendants(ids.get('subscription-engines')).filter(item=>item.tag==='button');
const checks=[];
(async()=>{
 ctx.showExecutionConnection(settings);ctx.showSubscriptionEngines(settings.subscription_engines);
 assert(text('active-ai').includes('Codex · 구독 CLI 우선'));
 assert(text('active-ai').includes('웹·Telegram'));
 assert(text('active-ai').includes('직접 API를 설정하거나 테스트해도 이 선택은 바뀌지 않습니다.'));
 assert(text('active-ai').includes('파일 작업공간 요약은 현재 구독 CLI에서 지원하지 않습니다.'));
 assert(text('active-ai').includes('모델 정보 미제공'));
 assert(!text('active-ai').includes('직접 API 테스트 통과'));
 assert(text('subscription-engines').includes('현재 선택됨'));
 assert.equal(calls.length,0);
 checks.push('global CLI precedence without invented readiness or provider calls');

 ids.get('api-key').value='synthetic-unsaved-key';ids.get('model-name').value='synthetic-draft';
 const firstButton=buttons()[0];
 assert.equal(buttons().length,1);
 assert.equal(firstButton.textContent,'Claude Code 로그인 완료 · 전환');
 ctx.showSubscriptionEngines(JSON.parse(JSON.stringify(settings.subscription_engines)));
 assert.equal(buttons()[0],firstButton);
 assert.equal(ids.get('model-name').value,'synthetic-draft');
 assert.equal(ids.get('api-key').value,'synthetic-unsaved-key');
 checks.push('unchanged polling preserves control identity and unrelated drafts');

 await firstButton.onclick();
 assert.equal(calls.length,1);assert.equal(calls[0].path,'/api/subscription-engines/connect');
 assert.equal(JSON.stringify(calls[0].body),JSON.stringify({engine:'claude-code',officially_authenticated:true}));
 assert.equal(refreshes,1);assert.equal(firstButton.disabled,false);
 assert.equal(ids.get('api-key').value,'synthetic-unsaved-key');
 checks.push('explicit owner confirmation sends only engine and login attestation');

 fail=true;refreshes=0;await firstButton.onclick();
 assert.equal(refreshes,0);assert.equal(text('subscription-feedback'),'synthetic failure');
 assert(text('active-ai').includes('Codex · 구독 CLI 우선'));
 assert.equal(firstButton.disabled,false);
 checks.push('failed switch is inline and does not advertise a changed selection');

 const missing=selected('codex');missing.engines[0].installed=false;
 ctx.showExecutionConnection({...settings,subscription_engines:missing});ctx.showSubscriptionEngines(missing);
 assert(text('active-ai').includes('선택한 CLI를 찾지 못했습니다.'));
 assert(text('active-ai').includes('직접 API로 자동 전환하지 않습니다.'));
 assert(!text('active-ai').includes('직접 API 테스트 통과'));
 assert.equal(buttons().length,1);
 checks.push('missing selected CLI cannot inherit successful API readiness');

 ctx.showExecutionConnection({...settings,model:{},model_ready:false,subscription_engines:selected('claude-code')});
 assert(text('active-ai').includes('Claude Code · 구독 CLI 우선'));
 assert(text('active-ai').includes('직접 API 미설정 · 구독 CLI 선택과 별개입니다.'));
 ctx.showExecutionConnection({...settings,subscription_engines:selected('')});
 assert(text('active-ai').includes('synthetic-api-model · 직접 API'));
 assert(text('active-ai').includes('직접 API 테스트 통과'));
 ctx.showSubscriptionEngines(selected(''));
 assert.equal(buttons().length,2);
 assert(buttons().every(button=>button.textContent.includes('로그인 완료 · 선택')));
 checks.push('CLI-only and API-only configurations remain distinct');

 ctx.showExecutionConnection({});ctx.showSubscriptionEngines();
 assert(text('active-ai').includes('직접 API 미설정 · 구독 CLI도 선택되지 않았습니다.'));
 ctx.showExecutionConnection({...settings,subscription_engines:{selected:'unrecognized',engines:[]}});
 assert(text('active-ai').includes('선택된 구독 CLI · 구독 CLI 우선'));
 assert(!text('active-ai').includes('직접 API 테스트 통과'));
 checks.push('missing discovery data never fabricates a ready direct-API route');
 console.log(JSON.stringify({passed:checks.length,checks}));
})().catch(error=>{console.error(error);process.exitCode=1;});
"""


MAIN_AI_CHOOSER_CHECKS = r"""
const assert=require('node:assert/strict'),vm=require('node:vm'),fs=require('node:fs');
const app=fs.readFileSync(process.argv[1],'utf8');
class E {
 constructor(tag,text=''){this.tag=tag;this.children=[];this.dataset={};this.text=text;this.value='';this.classList={toggle(){}};}
 append(...items){this.children.push(...items);}
 replaceChildren(...items){this.children=[];this.append(...items);}
 setAttribute(){} focus(){} close(){this.open=false;}
 get textContent(){return this.text+this.children.map(c=>c.textContent||'').join('');}
 querySelectorAll(selector){return this.children.flatMap(c=>[...((selector==='button'?c.tag==='button':selector==='input, select'?['input','select'].includes(c.tag):c.tag==='input')?[c]:[]),...c.querySelectorAll(selector)]);}
}
const ids=new Map(),get=id=>{if(!ids.has(id))ids.set(id,new E('div'));return ids.get(id);};
const t=(s,args={})=>s.replace(/\{(\w+)\}/g,(_,k)=>args[k]??'{'+k+'}');
const calls=[],feedback=[];
const route={id:'route-fixture',kind:'api',name:'Fixture',model:'fixture-old',model_mode:'pinned',default_model:'fixture-default',effort:'high',key:{saved:true},destination:'fixture.invalid'};
const ctx={element:(tag,text)=>new E(tag,text),t,$:get,document:{activeElement:null},MAIN_AI_GROUPS:[],
 aiSettings:{main_ai:{current:route.id,routes:[route]},decision_route:{mode:'follow_main',active:{},model_lists:{}}},
 aiModelDraft:{},aiInitialModels:{[route.id]:{mode:'pinned',model:'fixture-old',effort:'high'}},
 aiChoice:route.id,aiInitialChoice:route.id,aiChooserSession:1,aiPending:false,aiModelNode:null,
 chooserAccountDirty:()=>false,chooserClearDiscard(){},aiFeedback(){},refresh:async()=>{},
 chooserFeedback:(...args)=>feedback.push(args),agencyText:()=>'',decisionActiveTitle:a=>a.title||'Explicit fixture',
 mainAiRoutes:settings=>Object.fromEntries(settings.main_ai.routes.map(r=>[r.id,r])),loginSupersedesCheck:()=>false,
 settingsAction:(text,callback)=>{const b=new E('button',text);b.onclick=callback;return b;},
 api:async(path,body)=>{calls.push({path,body});if(path.endsWith('/models'))throw Error('fixture list refused');return {judgment:{}};}};
vm.createContext(ctx);
const extract=(start,end)=>app.slice(app.indexOf(start),app.indexOf(end,app.indexOf(start)));
vm.runInContext(extract('function chooserModel(','function renderAiChooser(')+extract('function renderAiChooser(','function openJudgmentChooser(')+extract('function mainAiView(','function judgmentView('),ctx);
const controls=(field,name)=>field.querySelectorAll('input, select').filter(c=>c.name===name);
(async()=>{
 // Rendering never fetches a list (#1051): the pinned model is the only option until a list exists.
 const failing={...route,id:'route-failing'};ctx.aiSettings.main_ai.routes.push(failing);
 let field=ctx.chooserModel(failing);await new Promise(setImmediate);
 assert.equal(calls.length,0,'rendering fetches no model list');
 let select=controls(field,'main-ai-model')[0];assert.equal(select.tag,'select');assert.equal(select.children.length,1);assert.equal(select.value,'fixture-old');
 assert.equal(controls(field,'main-ai-effort').length,0);
 // 목록 새로고침 is the owner's request; a refusal is shown and the pinned model stays.
 field.children.find(c=>c.tag==='button').onclick();await new Promise(setImmediate);
 assert.equal(calls.length,1);assert.equal(calls[0].body.route,'route-failing');assert.equal(feedback.at(-1)[0],'fixture list refused');
 field=ctx.chooserModel(failing);assert.equal(calls.length,1,'a failed lookup is not retried on render');
 assert.equal(controls(field,'main-ai-model')[0].value,'fixture-old');
 calls.length=0;feedback.length=0;
 // A list the judgment panel already fetched is reused.
 ctx.aiSettings.decision_route.model_lists[route.id]={models:[{id:'fixture-default',label:'Recommended',efforts:['low','high']},{id:'fixture-other',efforts:[]}]};
 field=ctx.chooserModel(route);select=controls(field,'main-ai-model')[0];assert(select.children.some(o=>o.value==='fixture-old'),'current pin survives list omission');
 assert.equal(calls.length,0);
 controls(field,'main-ai-model-mode').find(r=>r.value==='auto').onchange();
 field=ctx.aiModelNode;assert(field.textContent.includes('자동 (fixture-default)'));
 let effort=controls(field,'main-ai-effort')[0];assert.deepEqual(effort.children.map(c=>c.value),['','low','high']);effort.value='low';effort.onchange();
 assert(ctx.mainAiView({...ctx.aiSettings,main_ai:{...ctx.aiSettings.main_ai,routes:[{...route,model_mode:'auto',effort:'low'}]}}).model==='자동 · fixture-default');
 await ctx.applyAiChoice(new E('button'));
 const activation=calls.find(c=>c.path==='/api/main-ai/activate');assert.deepEqual(JSON.parse(JSON.stringify(activation.body)),{route:route.id,model_mode:'auto',effort:'low'});
 ctx.aiPending=false;ctx.renderAiChooser();assert(get('ai-chooser-consequence').textContent.includes('지금 판단 경로를 씁니다.'));
 ctx.aiSettings.decision_route.mode='explicit';ctx.aiSettings.decision_route.active={title:'Pinned Judgment'};ctx.renderAiChooser();assert(get('ai-chooser-consequence').textContent.includes('따로 지정한 Pinned Judgment'));
 ctx.aiSettings.decision_route.mode='off';ctx.renderAiChooser();assert(get('ai-chooser-consequence').textContent.includes('사용 안 함으로 그대로'));
 controls(ctx.aiModelNode,'main-ai-model-mode').find(r=>r.value==='pinned').onchange();
 select=controls(ctx.aiModelNode,'main-ai-model')[0];select.value='fixture-other';select.onchange();assert.equal(controls(ctx.aiModelNode,'main-ai-effort').length,0);assert.equal(ctx.aiModelDraft[route.id].effort,'');
 await ctx.applyAiChoice(new E('button'));assert.equal(calls.at(-1).body.model,'fixture-other');assert.equal(calls.at(-1).body.model_mode,'pinned');
 const isolated=ctx.chooserModel({...route,model_selectable:false});assert.equal(isolated.querySelectorAll('input, select').length,0);
 console.log('Main AI chooser behavior passed');
})().catch(error=>{console.error(error);process.exitCode=1;});
"""


class ConnectionRouteUiTests(unittest.TestCase):
    def test_ai_tab_has_peer_preferences_with_dialog_choosers(self):
        # #781: compact peer groups; credential editing remains in accessible dialogs.
        html = (WEB / "index.html").read_text(encoding="utf-8")
        items = Elements(html).items
        by_id = {attrs["id"]: (tag, attrs) for tag, attrs in items if "id" in attrs}
        all_ids = [attrs["id"] for _, attrs in items if "id" in attrs]
        self.assertEqual(len(all_ids), len(set(all_ids)))
        pane = html[html.index('id="settings-ai"'):html.index('id="settings-files"')]
        for retired in ("advanced-model", "optional-providers", "model-form", "직접 API 설정 변경", "다른 연결 방법"):
            self.assertNotIn(retired, pane)
        self.assertNotIn("<details", pane[:pane.index("<dialog")])
        self.assertEqual(by_id["ai-chooser"][0], "dialog")
        self.assertEqual(by_id["judgment-chooser"][0], "dialog")
        self.assertEqual(by_id["ai-chooser"][1]["aria-labelledby"], "ai-chooser-title")
        self.assertIn("<h1>AI</h1>", pane)
        self.assertNotIn("<h2>AI 설정</h2>", pane)
        # Explicit Judgment routes remain in their settings dialog.
        self.assertLess(html.index('id="judgment-chooser"'), html.index('id="decision-route"'))
        self.assertIn("확인하고 사용", pane)
        self.assertIn("preference-dialog", by_id["ai-chooser"][1]["class"])
        self.assertEqual(by_id["ai-chooser-feedback"][1].get("tabindex"), "-1")
        for control in ("ai-chooser-discard", "ai-discard-keep", "ai-discard-confirm"):
            self.assertIn(control, by_id)
        self.assertIn("hidden", by_id["ai-chooser-discard"][1])
        chooser = pane[pane.index('<dialog id="ai-chooser"'):pane.index('</dialog>')]
        self.assertIn('<footer class="button-row dialog-actions">', chooser)
        self.assertLess(chooser.index('id="ai-chooser-discard"'), chooser.index('<footer'))

    def test_existing_draft_and_primary_route_wiring_are_preserved(self):
        app = (WEB / "app.js").read_text(encoding="utf-8")
        self.assertIn("renderExecutionConnection(settings);", app)
        self.assertIn("renderSubscriptionEngines(settings.subscription_engines);", app)
        self.assertIn("api('/api/main-ai/activate',body)", app)
        self.assertIn("api('/api/main-ai/key',{provider:route.id,key:input.value})", app)
        self.assertIn("contextDraftDirty", app)
        self.assertIn("if(!contextDraftDirty", app)
        self.assertNotIn("Telegram용 구독 엔진", app)
        self.assertNotIn("테스트한 설정을 현재 사용 모델로 적용했습니다.", app)

    def test_main_ai_model_chooser_behavior(self):
        node = shutil.which('node')
        if node is None:
            self.skipTest('Node is needed for chooser behavior verification')
        result = subprocess.run([node, '-e', MAIN_AI_CHOOSER_CHECKS, str(WEB / 'app.js')],
                                capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_javascript_parses(self):
        node = shutil.which("node")
        if node is None:
            self.skipTest("Node is needed for JavaScript syntax validation")
        subprocess.run([node, "--check", str(WEB / "app.js")], check=True, capture_output=True, text=True, timeout=20)

    def test_actual_connection_renderers_and_explicit_switch_callback(self):
        app = (WEB / "app.js").read_text(encoding="utf-8")
        self.assertIn("구독 CLI 우선", app)
        self.assertIn("직접 API로 자동 전환하지 않습니다.", app)
        self.assertIn("모델 정보 미제공", app)
        self.assertIn("officially_authenticated:true", app)
        self.assertNotIn("Telegram용 구독 엔진", app)


if __name__ == "__main__":
    unittest.main()
