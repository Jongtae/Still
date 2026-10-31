"""#390: the restructured management screen keeps every control and reads only real state.

Structure checks read the shipped HTML. Behaviour checks run the production renderers in node
with stubbed DOM helpers: routing and its conditional loading, the read-only 확인이 필요한 일
projection, one file-access reading shared by the AI and 파일 · 권한 panes, and the connection
summary above the stable service list. Nothing here calls a model, a CLI or a server.
"""
from pathlib import Path
import re
import shutil
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[1] / "src" / "personal_agent" / "web"
HTML = (ROOT / "index.html").read_text(encoding="utf-8")
APP = (ROOT / "app.js").read_text(encoding="utf-8")


def pane(panel_id):
    start = HTML.index(f'id="{panel_id}"')
    return HTML[start:HTML.index('\n', start)]


def run_node(script):
    node = shutil.which("node")
    if node is None:
        raise unittest.SkipTest("node is required for the renderer checks")
    result = subprocess.run([node, "-e", script, str(ROOT / "app.js")], capture_output=True, text=True, timeout=20)
    if result.returncode:
        raise AssertionError(result.stderr.strip()[-2000:])


class Structure(unittest.TestCase):
    def test_settings_order_and_the_diagnostics_foot_link(self):
        tabs = re.findall(r'data-settings="(\w+)"', HTML)
        self.assertEqual(tabs, ["ai", "external", "files", "privacy"])
        nav = HTML[HTML.index('id="settings-nav"'):HTML.index('</nav>', HTML.index('id="settings-nav"'))]
        self.assertNotIn("diagnostics", nav, "진단 is not a settings tab")
        foot = HTML[HTML.index('class="rail-foot"'):HTML.index('</aside>')]
        self.assertIn('data-settings-link="diagnostics"', foot)
        # Older #settings/<pane> links keep resolving: the pane ids and hashes are unchanged.
        for key in ("ai", "files", "external", "privacy"):
            self.assertIn(f'data-settings-panel="{key}"', HTML)
        self.assertIn('data-settings-panel="diagnostics"', HTML)

    def test_every_existing_control_is_kept(self):
        for control in ("work-mode-enabled", "intranet-form", "telegram-form", "mattermost-form", "mattermost-disconnect",
                        "mattermost-company-approved", "slack-form", "slack-disconnect", "slack-company-approved",
                        "connector-controls", "google-disconnect", "search-providers", "browser-status", "root-list",
                        "roots-form", "file-workspace-form", "document-boundary", "folder-requests", "projects",
                        "context-config", "current-context-enabled", "context-items", "context-capture",
                        "memory-candidate-list", "owner-profile-form", "saved-memory-list", "preparations-list",
                        "tool-history", "developer-mode", "task-list", "trace-jump-latest"):
            self.assertEqual(HTML.count(f'id="{control}"'), 1, control)

    def test_connections_put_status_first_and_fold_setup_and_company_network(self):
        external = pane("settings-external")
        order = [external.index(marker) for marker in ('id="connection-attention"', 'id="telegram-heading"', 'id="mattermost"',
                                                       'id="slack"', 'id="connector-heading"', 'id="browser-profile"',
                                                       'id="search-providers-heading"', 'id="company-network"')]
        self.assertEqual(order, sorted(order))
        company = external[external.index('id="company-network"'):]
        self.assertIn('<details id="company-network"', external)
        self.assertIn('id="work-mode"', company)
        self.assertIn('id="intranet"', company)
        for channel in ("mattermost", "slack"):
            section = external[external.index(f'id="{channel}"'):external.index('</section>', external.index(f'id="{channel}-form"'))]
            setup = section.index(f'<details id="{channel}-setup"')
            self.assertGreater(section.index(f'id="{channel}-form"'), setup, "the credential form opens on request")
            self.assertLess(section.index(f'id="{channel}-disconnect"'), setup, "disconnect stays visible when connected")
            self.assertNotIn(f'<details id="{channel}-setup" class="settings-editor" open', section)

    def test_files_pane_explains_execution_scope_before_folders(self):
        files = pane("settings-files")
        self.assertIn("<h1>파일 · 권한</h1>", files)
        self.assertLess(files.index('id="file-access-scope"'), files.index('id="root-list"'))
        self.assertLess(files.index('id="root-list"'), files.index('id="file-workspace-list"'))
        # #474: what leaves this computer (document excerpts) sits with the execution scope, before the folders.
        self.assertLess(files.index('id="document-boundary"'), files.index('id="root-list"'))

    def test_memory_comes_first_and_schedules_and_diagnostics_moved_out(self):
        privacy = pane("settings-privacy")
        self.assertIn("<h1>기억 · 개인정보</h1>", privacy)
        order = [privacy.index(f'id="{marker}"') for marker in ("memory-candidates", "owner-profile", "saved-memory",
                                                                "current-context", "context-config", "context-items")]
        self.assertEqual(order, sorted(order))
        for moved in ("preparations", "activity-details", "developer-settings"):
            self.assertNotIn(f'id="{moved}"', privacy)
        tasks = pane("view-tasks")
        self.assertIn('id="preparations"', tasks[tasks.index('id="task-scheduled"'):])
        self.assertLess(tasks.index('id="task-attention"'), tasks.index('id="task-list"'))
        diagnostics = pane("settings-diagnostics")
        # Codex review on #433: a link that focuses a turn must show 최근 기록 even when 예약된 일
        # was the last 작업 현황 view, or it scrolls to a turn inside the hidden panel.
        jumps = re.findall(r"navigate\('tasks'[^;]*;focusTurn\(", APP)
        self.assertTrue(jumps)
        for jump in jumps:
            self.assertIn("taskTab:'recent'", jump)
        self.assertIn('id="activity-details"', diagnostics)
        self.assertIn('id="developer-settings"', diagnostics)


ROUTING = r"""
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const app=fs.readFileSync(process.argv[1],'utf8');
const source=app.slice(app.indexOf('const SETTINGS_PANES='),app.indexOf('// Opening one item is page state only'));
const history=[],loads=[],location={hash:''};
const ctx={activeView:'tasks',activeSettings:'ai',activeTaskTab:'recent',activeItem:null,location,itemRoute:()=>null,
 window:{scrollY:0,scrollTo(){}},history:{pushState:(s,t,h)=>{history.push(h);location.hash=h;},replaceState:(s,t,h)=>{location.hash=h;}},
 renderNavigation(){},loadOwnerProfile:()=>loads.push('profile'),loadSavedMemory:()=>loads.push('memory'),loadPreparations:()=>loads.push('preparations'),loadGoogleRevocations:()=>loads.push('google')};
vm.createContext(ctx);vm.runInContext(source,ctx);
const route=hash=>{location.hash=hash;return JSON.parse(JSON.stringify(ctx.routeFromLocation()));};
assert.deepEqual(route('#tasks'),{view:'tasks',settings:'ai',taskTab:'recent'});
assert.deepEqual(route('#tasks/scheduled'),{view:'tasks',settings:'ai',taskTab:'scheduled'});
assert.deepEqual(route('#settings/diagnostics'),{view:'settings',settings:'diagnostics',taskTab:'recent'});
for(const pane of ['ai','files','external','privacy'])assert.equal(route('#settings/'+pane).settings,pane,'older settings links still open');
assert.equal(route('#records').settings,'privacy','the #562 records bookmark still opens memory');
assert.equal(route('#settings/unknown').settings,'ai');
// Conditional loading follows the moved sections: memory with 기억 · 개인정보, preparations with 예약된 일.
ctx.navigate('settings','privacy');assert.deepEqual(loads,['profile','memory']);assert.equal(history.at(-1),'#settings/privacy');
loads.length=0;ctx.navigate('tasks','',{taskTab:'scheduled'});assert.deepEqual(loads,['preparations']);assert.equal(history.at(-1),'#tasks/scheduled');
loads.length=0;ctx.navigate('settings','diagnostics');assert.deepEqual(loads,[]);assert.equal(history.at(-1),'#settings/diagnostics');
ctx.navigate('tasks');assert.equal(history.at(-1),'#tasks/scheduled','returning to 작업 현황 keeps its last view');
ctx.navigate('tasks','',{taskTab:'recent'});assert.equal(history.at(-1),'#tasks');
assert(!app.slice(app.indexOf('function refreshSavedMemory('),app.indexOf('function refreshSavedMemory(')+120).includes("'diagnostics'"),'memory refresh still follows its own pane');
console.log('routing ok');
"""

ATTENTION = r"""
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const app=fs.readFileSync(process.argv[1],'utf8');
const source=app.slice(app.indexOf('const TASK_ATTENTION_LIMIT='),app.indexOf('function renderTasks(){'));
const box=()=>({hidden:false,children:[],rebuilt:0,replaceChildren(){this.children=[];this.rebuilt++;},append(...nodes){this.children.push(...nodes);}});
const nodes={'task-attention':box(),'task-attention-list':box()},calls=[];
const ctx={$:id=>nodes[id],currentLanguage:'ko',activeTaskTab:'scheduled',
 t:(text,vars)=>vars?text.replace(/\{(\w+)\}/g,(m,k)=>vars[k]):text,element:(tag,text)=>({tag,text}),focusKey:node=>node,
 settingsAction:(label,onClick)=>({label,onClick}),settingsRow:(title,description,state,kind,actions)=>({title,description,state,kind,actions}),
 rememberFocus:()=>'',restoreFocus(){},navigate:(...args)=>calls.push(['navigate',...args]),focusTurn:id=>calls.push(['focus',id]),
 api:()=>{throw new Error('the projection never calls the server');}};
vm.createContext(ctx);vm.runInContext(source,ctx);
const section=nodes['task-attention'],list=nodes['task-attention-list'];
ctx.renderTaskAttention([]);assert.equal(section.hidden,true,'no section without a waiting request');
ctx.renderTaskAttention([{id:'ok',title:'done',status:'succeeded',status_kind:'finished',started_at:1},{id:'bad',title:'failed',status:'failed',status_kind:'finished',started_at:2}]);
assert.equal(section.hidden,true,'finished and failed requests are not waiting on the owner');
const tasks=[
 {id:'conn',title:'calendar',status:'awaiting_connection',status_kind:'active',waits:['연결 대기'],started_at:10},
 {id:'approve',title:'reply',status:'partial',status_kind:'finished',waiting_for_owner:true,started_at:20},
 {id:'effect',title:'event',status:'unknown',status_kind:'finished',started_at:30},
 {id:'delivery',title:'answer',status:'succeeded',status_kind:'finished',delivery:'unknown',started_at:40},
 {id:'running',title:'busy',status:'running',status_kind:'active',delivery:'unknown',started_at:50}];
ctx.renderTaskAttention(tasks);
assert.equal(section.hidden,false);
assert.deepEqual(list.children.map(row=>[row.title,row.state,row.kind]),[['answer','전달 여부 알 수 없음','unknown'],['event','외부 결과 불확실','unknown'],['reply','승인 대기','attention'],['calendar','연결 대기','attention']],'newest first; an active Work is listed only for a real wait');
for(const row of list.children){assert.equal(row.actions.length,1,'one action per row');assert.equal(row.actions[0].label,'요청 보기','it opens the request; nothing is retried here');}
assert.match(list.children[1].description,/자동으로 다시 실행하지 않습니다/,'an unknown external result is never offered a re-run');
list.children[1].actions[0].onClick();
assert.deepEqual(JSON.parse(JSON.stringify(calls)),[['navigate','tasks','',{taskTab:'recent'}],['focus','effect']],'the row opens the original request in the trace');
const rebuilt=list.rebuilt;ctx.renderTaskAttention(tasks);assert.equal(list.rebuilt,rebuilt,'polling with unchanged state keeps the rows and focus');
ctx.renderTaskAttention(Array.from({length:7},(_,i)=>({id:'u'+i,title:'t'+i,status:'unknown',status_kind:'finished',started_at:i})));
assert.equal(list.children.length,6);assert.match(list.children[5].text,/외 2개/);
console.log('attention ok');
"""

FILE_ACCESS = r"""
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const app=fs.readFileSync(process.argv[1],'utf8');
const source=app.slice(app.indexOf('function executionAccessView('),app.indexOf('// Settings › AI shows Main AI'));
const box={dataset:{},rows:[],replaceChildren(){this.rows=[];},append(row){this.rows.push(row);}};
const ctx={$:()=>box,currentLanguage:'ko',t:text=>text,focusKey:node=>node,rememberFocus:()=>'',restoreFocus(){},navigate(){},
 settingsAction:label=>({label}),settingsRow:(title,description,state,kind,actions)=>({title,description,state,kind,actions}),
 mainAiRoutes:settings=>Object.fromEntries((settings.main_ai.routes||[]).map(route=>[route.id,route])),
 api:()=>{throw new Error('rendering calls no server, model or CLI');}};
vm.createContext(ctx);vm.runInContext(source,ctx);
const settings=(current,kind,execution)=>({main_ai:{current,routes:current?[{id:current,kind}]:[]},subscription_engines:{selected:kind==='subscription'?current:''},subscription_execution:execution});
const shown=value=>{box.dataset={};ctx.renderFileAccess(value);assert.equal(box.rows.length,1);return box.rows[0];};
for(const [execution,label,kind] of [
 [{trust:'trusted-local',selectable:['trusted-local','strict-isolated']},'작업 폴더 밖 접근 가능','neutral'],
 [{trust:'strict-isolated',selectable:['trusted-local','strict-isolated'],requalify_needed:false},'작업 폴더로 제한','neutral'],
 [{trust:'strict-isolated',selectable:['trusted-local','strict-isolated'],requalify_needed:true},'격리 다시 확인 필요','attention'],
 [{trust:'unknown',selectable:['trusted-local','strict-isolated']},'접근 범위 확인 필요','attention']]){
 const value=settings('codex','subscription',execution),row=shown(value);
 assert.equal(row.state,label);assert.equal(row.kind,kind);
 assert.equal(row.state,ctx.executionAccessView(value).label,'파일 · 권한 shows the same reading as the AI pane');
 assert.equal(row.description,ctx.executionAccessView(value).description);}
assert.equal(shown(settings('codex','subscription',{trust:'isolated-restricted'})).state,'폴더 읽기 없음');
const api=shown(settings('anthropic','api',{trust:'trusted-local',selectable:['trusted-local','strict-isolated']}));
assert.equal(api.state,'연결한 폴더만','an API route has no file system of its own; a stored CLI profile does not apply to it');
assert.equal(shown(settings('','',{})).state,'AI 선택 전');
const ai=app.slice(app.indexOf('function renderExecutionConnection('),app.indexOf('function executionAccessView('));
assert(ai.includes('executionAccessView(settings)'),'the AI pane reads the same function');
assert(!/작업 폴더 밖 접근 가능'/.test(ai),'the AI pane keeps no second copy of the labels');
console.log('file access ok');
"""

CONNECTIONS = r"""
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const app=fs.readFileSync(process.argv[1],'utf8');
const start=app.indexOf('function revealSetting('),source=app.slice(start,app.indexOf('// #209: intranet destinations',start));
const box=()=>({hidden:false,dataset:{},rows:[],replaceChildren(){this.rows=[];},append(row){this.rows.push(row);},querySelector:()=>null,closest:()=>null,scrollIntoView(){},focus(){}});
const nodes={'connection-attention':box(),'connection-attention-list':box(),'work-mode':box(),'work-mode-enabled':box(),'connector-controls':box(),mattermost:box(),slack:box()};
const ctx={$:id=>nodes[id],currentLanguage:'ko',t:text=>text,focusKey:node=>node,rememberFocus:()=>'',restoreFocus(){},
 settingsAction:(label,onClick)=>({label,onClick}),settingsRow:(title,description,state,kind,actions)=>({title,description,state,kind,actions}),
 connectorRowModel:connector=>({id:connector.connector_id,name:connector.connector_id,description:'d',state:connector.state,kind:'attention'}),
 api:()=>{throw new Error('the summary connects, disconnects and switches nothing');}};
vm.createContext(ctx);vm.runInContext(source,ctx);
const section=nodes['connection-attention'],list=nodes['connection-attention-list'];
const render=settings=>{ctx.renderConnectionAttention(settings);return list.rows.map(row=>[row.title,row.state,row.kind]);};
assert.deepEqual(render({work_mode:{enabled:false},connectors:[{connector_id:'gmail',state:'connected'},{connector_id:'drive',state:'disconnected'}],mattermost:{connected:false},slack:{connected:false}}),[]);
assert.equal(section.hidden,true,'nothing to show when every connection is fine or unused');
const rows=render({work_mode:{enabled:true},connectors:[{connector_id:'gmail',state:'connected'},{connector_id:'calendar',state:'reauth_required'}],
 mattermost:{connected:true,usable:false},slack:{connected:true,usable:true,state:'error'}});
assert.equal(section.hidden,false);
assert.deepEqual(rows,[['회사 모드 켜짐','켜짐','neutral'],['calendar','reauth_required','attention'],['Mattermost','회사 모드로 멈춤','attention'],['Slack','확인 필요','attention']]);
for(const row of list.rows)row.actions[0].onClick();
console.log('connections ok');
"""

CHANNEL = r"""
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const app=fs.readFileSync(process.argv[1],'utf8');
const source=app.slice(app.indexOf('function inactive('),app.indexOf('function settingsDisclosure('))+app.slice(app.indexOf('function renderMattermost('),app.indexOf("$('mattermost-form').onsubmit"));
const node=()=>({hidden:false,disabled:false,checked:false,textContent:'',replaceChildren(){},append(){}});
const nodes=Object.fromEntries(['mattermost-status','mattermost-disconnect','mattermost-setup-summary','mattermost-company','mattermost-company-approved'].map(id=>[id,node()]));
const ctx={$:id=>nodes[id],t:(text,vars)=>vars?text.replace(/\{(\w+)\}/g,(m,k)=>vars[k]):text,settingsRow:()=>({dataset:{}}),element:()=>({})};
vm.createContext(ctx);vm.runInContext(source,ctx);
ctx.renderMattermost({connected:false});
assert.equal(nodes['mattermost-disconnect'].hidden,true);assert.equal(nodes['mattermost-company'].hidden,true);
assert.equal(nodes['mattermost-setup-summary'].textContent,'Mattermost 연결 설정');
ctx.renderMattermost({connected:true,usable:true,owner_username:'me',bot_username:'still',company_approved:true});
assert.equal(nodes['mattermost-disconnect'].hidden,false);assert.equal(nodes['mattermost-company'].hidden,false);
assert.equal(nodes['mattermost-setup-summary'].textContent,'연결 다시 설정','a connected channel offers a change, not an empty setup');
assert.equal(nodes['mattermost-company-approved'].checked,true);
console.log('channel ok');
"""


class Renderers(unittest.TestCase):
    def test_routes_and_conditional_loading(self):
        run_node(ROUTING)

    def test_needs_attention_is_a_read_only_projection_of_real_waits(self):
        run_node(ATTENTION)

    def test_file_access_reads_one_profile_for_both_panes(self):
        run_node(FILE_ACCESS)

    def test_connection_summary_shows_only_server_reported_problems(self):
        run_node(CONNECTIONS)

    def test_connected_channel_shows_state_and_folds_its_form(self):
        run_node(CHANNEL)


if __name__ == "__main__":
    unittest.main()
