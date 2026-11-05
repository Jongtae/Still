"""#651: importing the base service never requires the A2A extra."""
import subprocess
import sys
from pathlib import Path

import pytest


def test_base_import_and_optional_test_collection_without_a2a():
    root = Path(__file__).resolve().parents[1]
    script = r'''
import importlib.abc
import sys
class NoA2A(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, *args):
        if fullname == 'a2a' or fullname.startswith('a2a.'):
            raise ModuleNotFoundError("A2A extra is absent", name='a2a')
sys.meta_path.insert(0, NoA2A())
from personal_agent.quickstart_service import AgentService
from personal_agent.a2a_peers import A2APeers, PeerError
assert not any(name.startswith('a2a') for name in sys.modules)
try:
    A2APeers.transport(None)
except PeerError as exc:
    assert exc.code == 'optional_a2a_extra_required'
else:
    raise AssertionError('absent extra did not fail clearly')
import pytest
raise SystemExit(pytest.main(['--noconftest', '-p', 'no:xdist', '-q',
    'tests/test_a2a_compatibility.py', 'tests/test_a2a_peers.py']))
'''
    result = subprocess.run([sys.executable, '-c', script], cwd=root, capture_output=True, text=True, timeout=30)
    assert result.returncode == 5, result.stdout + result.stderr  # pytest's all-skipped/no-tests status
    assert '2 skipped' in result.stdout and 'ERROR' not in result.stdout


@pytest.mark.parametrize('language', ['ko', 'en', 'ja', 'zh-CN'])
def test_peer_settings_controls_preserve_drafts_and_one_time_credentials(language):
    import shutil
    node = shutil.which('node')
    if not node:
        pytest.skip('Node is unavailable for the model-free Settings DOM check')
    script = r'''
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const source=fs.readFileSync(process.argv[1],'utf8');
const roots=[];
class Element{
 constructor(tag,text){this.tagName=tag.toUpperCase();this.children=[];this.dataset={};this.attrs={};this.value='';this.textContent=text||'';this.listeners={};this.open=false;}
 append(...nodes){this.children.push(...nodes);}
 prepend(node){this.children.unshift(node);}
 setAttribute(key,value){this.attrs[key]=value;}
 addEventListener(key,fn){this.listeners[key]=fn;}
 contains(node){return this===node||this.children.some(child=>child.contains?.(node));}
 replaceChildren(...nodes){this.children=[...nodes];}
 querySelectorAll(selector){return walk(this).filter(node=>selector==='details'&&node.tagName==='DETAILS');}
}
function walk(node){return [node,...node.children.flatMap(child=>walk(child))];}
function $(id){return roots.flatMap(root=>walk(root)).find(node=>node.id===id);}
const pane=new Element('section');pane.id='settings-external';roots.push(pane);
const peer={id:'peer-1',card:{name:'Counterpart <script>',supportedInterfaces:[{url:'https://agent.example/'}]},scope:{sections:[],tools:[]},budget:100,rate:10};
let data={peers:[peer],turns:[],owner_items:[{id:'review-1',peer_id:'peer-1',state:'pending',request:'Quoted agent request'}]},calls=[],confirmed=false;
const catalog=JSON.parse(source.slice(source.indexOf('const I18N=')+11,source.indexOf('Object.assign(I18N')).trim().replace(/;$/,''));
let language=process.argv[2];
const ctx={t:(key,values={})=>(catalog[language]?.[key]||key).replace(/\{(\w+)\}/g,(_,name)=>values[name]??''),activeView:'settings',activeSettings:'external',element:(tag,text,cls)=>{const node=new Element(tag,text);node.className=cls||'';return node;},$,
 document:{getElementById:$,activeElement:null},window:{confirm:()=>confirmed},
 api:async(path,body)=>{calls.push([path,body]);if(path==='/api/a2a/preview')return {card:peer.card,card_url:body.card_url,digest:'reviewed'};
 if(path==='/api/a2a/register')return {peer,inbound_credential:'once-only-credential'};return data;}};
vm.createContext(ctx);vm.runInContext(source.slice(source.indexOf('// #651: counterpart setup'),source.indexOf('// A boot failure')),ctx);
(async()=>{
 await ctx.renderA2aPeers();
 assert(walk($('a2a-peers')).some(node=>node.textContent===ctx.t('다른 AI 에이전트')));
 assert(walk($('a2a-peers')).some(node=>node.textContent===ctx.t('내 확인이 필요한 요청 ({state}): {request}',{state:ctx.t('확인 대기'),request:'Quoted agent request'})));
 if(language!=='ko')for(const node of walk($('a2a-peers'))){
  assert(!/[가-힣]/.test(node.textContent),'translated controls must not fall back to Korean');
  assert(!/[가-힣]/.test(node.placeholder||''),'translated placeholders must not fall back to Korean');
 }
 vm.runInContext(source.match(/function requestSource\(task\).*\n/)[0],ctx);
 assert.equal(ctx.requestSource({channel:'a2a-peer:peer-1'}),ctx.t('다른 AI 에이전트 {name}',{name:'peer-1'}));
 const list=$('a2a-peer-list'),first=list.children[0],inputs=walk(first).filter(node=>node.tagName==='INPUT');
 assert.equal(inputs.length,7);inputs[0].checked=true;list.listeners.input();
 data={...data,turns:[{peer_id:'peer-1',direction:'inbound',state:'completed',question:'new',answer:'answer'}]};
 await ctx.renderA2aPeers();assert.equal(list.children[0],first,'polling must preserve a disclosure draft');assert(inputs[0].checked);
 const save=walk(first).find(node=>node.tagName==='FORM');await save.onsubmit({preventDefault(){}});
 assert(calls.some(([path,body])=>path==='/api/a2a/update'&&body.scope.sections.includes('profile')));
 const block=walk(first).find(node=>node.textContent===ctx.t('에이전트 차단'));await block.onclick();
 assert(!calls.some(([path])=>path==='/api/a2a/block'),'cancelled destructive confirmation must not revoke');
 confirmed=true;await block.onclick();assert(calls.some(([path])=>path==='/api/a2a/block'));
 const all=walk($('a2a-peers')),json=all.find(node=>node.name==='a2a-card-json'),form=all.find(node=>node.tagName==='FORM'&&walk(node).includes(json));
 json.value='{}';await form.onsubmit({preventDefault(){}});
 const register=all.find(node=>node.textContent===ctx.t('확인한 에이전트 등록'));assert(!register.hidden);
 json.oninput();assert(register.hidden,'changed card invalidates preview');
 const url=all.find(node=>node.name==='a2a-card-url');url.value='https://discovery.example/custom/card.json';
 await form.onsubmit({preventDefault(){}});await register.onclick();
 assert.equal(calls.find(([path])=>path==='/api/a2a/register')[1].card_url,url.value,'registration preserves the reviewed card source');
 const token=all.find(node=>node.attrs['aria-label']===ctx.t('상대에게 전달할 일회 표시 인증 정보'));assert.equal(token.value,'once-only-credential');
 const hide=all.find(node=>node.textContent===ctx.t('인증 정보 숨기기'));hide.onclick();
 await ctx.renderA2aPeers();assert.equal(token.value,'','a refresh cannot show the credential again');
})().catch(error=>{console.error(error);process.exitCode=1;});
'''
    path = Path(__file__).resolve().parents[1] / 'src/personal_agent/web/app.js'
    result = subprocess.run([node, '-e', script, str(path), language], capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stdout + result.stderr
