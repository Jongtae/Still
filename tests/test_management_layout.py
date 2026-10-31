"""#474: Settings panes show what is on; anything else is added through one dialog by type.

Structure checks read the shipped HTML. Behaviour checks run the production helpers and renderers
in node with stubbed DOM helpers: which rows count as not set up, when a section or a whole group
leaves its pane, and that the add dialog moves the type's own section (same ids and handlers) in and
back out. Nothing here calls a model, a CLI or a server.
"""
from pathlib import Path
import re
import shutil
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[1] / "src" / "personal_agent" / "web"
HTML = (ROOT / "index.html").read_text(encoding="utf-8")
CSS = (ROOT / "style.css").read_text(encoding="utf-8")


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
    def test_each_pane_has_one_add_action_for_its_own_types(self):
        for key, label in (("external", "연결 추가"), ("files", "폴더 추가"), ("privacy", "추가")):
            body = pane(f"settings-{key}")
            buttons = re.findall(r'<button[^>]*data-add-pane="(\w+)"[^>]*>([^<]*)</button>', body)
            self.assertEqual(buttons, [(key, label)], key)
            kinds = set(re.findall(r'data-add="(\w+)"', body))
            self.assertEqual(kinds, {key}, f"{key} offers only its own types")
        self.assertNotIn("data-add-pane", pane("settings-ai"), "AI keeps its own chooser")
        self.assertNotIn("data-add-pane", pane("settings-diagnostics"))
        self.assertEqual(HTML.count('<dialog id="add-dialog"'), 1)
        external = set(re.findall(r'<(?:section|details)[^>]*(?:id="([\w-]+)"|aria-labelledby="([\w-]+)")[^>]*data-add="external"', pane("settings-external")))
        self.assertEqual({a or b for a, b in external},
                         {"telegram-heading", "mattermost", "slack", "connector-heading", "browser-profile", "search-providers-heading", "company-network"})

    def test_add_only_editors_open_in_the_dialog_not_the_pane(self):
        for editor in ("roots-add", "owner-profile-more", "context-capture-more"):
            self.assertRegex(HTML, rf'<details id="{editor}"[^>]*data-add-editor')
        self.assertIn(".settings-pane [data-add-editor]", CSS)
        # Setup that changes an existing connection (Mattermost/Slack "연결 다시 설정") stays where it was.
        for editor in ("mattermost-setup", "slack-setup"):
            self.assertNotRegex(HTML, rf'<details id="{editor}"[^>]*data-add-editor')

    def test_groups_hold_their_sections_in_one_card(self):
        for key in ("external", "files", "privacy"):
            body = pane(f"settings-{key}")
            groups = re.findall(r'<section class="settings-group"[^>]*><h2[^>]*>[^<]*</h2>(<div class="settings-card">)?', body)
            self.assertTrue(groups, key)
            self.assertTrue(all(groups), f"every {key} group wraps its sections in a card")
        files = pane("settings-files")
        self.assertLess(files.index('id="file-access-group-heading"'), files.index('id="file-folders-heading"'))
        access = files[files.index('id="file-access-group-heading"'):files.index('id="file-folders-heading"')]
        for marker in ('id="file-access-scope"', 'id="document-boundary"'):
            self.assertIn(marker, access, "what can be reached stays visible; it is never an add type")
        self.assertNotIn("data-add", access)

    def test_the_pane_hides_only_what_is_off(self):
        rule = re.search(r'\.settings-pane \[data-off\],\.settings-pane \.settings-row\[data-inactive\],\.settings-pane \[data-add-editor\]\{display:none\}', CSS)
        self.assertIsNotNone(rule)
        self.assertNotIn("#add-dialog-host [data-off]{display:none", CSS)


ROWS = r"""
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const app=fs.readFileSync(process.argv[1],'utf8');
const pick=(from,to)=>{const a=app.indexOf(from);assert(a>=0,from);return app.slice(a,app.indexOf(to,a));};
const source=pick('function inactive(','function settingsDisclosure(')+pick('function renderMattermost(',"$('mattermost-form').onsubmit")
 +pick('const CONNECTOR_STATES=','function renderAiConnections(');
const list=()=>({dataset:{},rows:[],hidden:false,replaceChildren(){this.rows=[];},append(...rows){this.rows.push(...rows);}});
const node=()=>({hidden:false,disabled:false,checked:false,textContent:''});
const nodes={'mattermost-status':list(),'connector-controls':list(),'mattermost-disconnect':node(),'mattermost-setup-summary':node(),'mattermost-company':node(),'mattermost-company-approved':node()};
const ctx={$:id=>nodes[id]||node(),t:(text,vars)=>vars?text.replace(/\{(\w+)\}/g,(m,k)=>vars[k]):text,googleClientAvailable:false,
 settingsRow:(title,description,state,kind)=>({title,state,kind,dataset:{}}),element:(tag,text,cls)=>({tag,text,cls,dataset:{}}),
 settingsAction:label=>({label,dataset:{}}),settingsDisclosure:()=>null,focusKey:n=>n,rememberFocus:()=>'',restoreFocus(){},
 api:()=>{throw new Error('rendering calls no server');}};
vm.createContext(ctx);vm.runInContext(source,ctx);
const off=row=>Object.hasOwn(row.dataset,'inactive');
ctx.renderMattermost({connected:false});
assert.equal(off(nodes['mattermost-status'].rows[0]),true,'an unconnected channel is not shown in the pane');
ctx.renderMattermost({connected:true,usable:false,owner_username:'me',bot_username:'b'});
assert.equal(off(nodes['mattermost-status'].rows[0]),false,'a connected channel stays, even when work mode stops it');
const connectors=[{connector_id:'google-gmail-read',state:'connected',connect_path:'/c'},{connector_id:'google-drive-read',state:'disconnected',connect_path:'/c'},
 {connector_id:'google-calendar',state:'disconnected',connect_path:'/c'},{connector_id:'google-calendar-write',state:'reauth_required',connect_path:'/c'},{connector_id:'x',state:'new-state'}];
ctx.renderConnectors(connectors);
assert.deepEqual(nodes['connector-controls'].rows.map(off),[false,true,true,false,false],'only a disconnected service leaves the pane; attention and unknown states stay');
vm.runInContext("googlePending={'google-calendar':1}",ctx);nodes['connector-controls'].dataset={};
ctx.renderConnectors(connectors);
assert.equal(off(nodes['connector-controls'].rows[2]),false,'an unconfirmed Google-side revocation keeps its row visible');
assert.equal(nodes['connector-controls'].rows[2].kind,'attention','and marks it as needing attention, so the add dialog does not call it 사용 중');
console.log('rows ok');
"""

SYNC = r"""
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const app=fs.readFileSync(process.argv[1],'utf8');
const source=app.slice(app.indexOf('function addableOn('),app.indexOf('function paintNoteClamps('));
class El{constructor(cls='',attrs={},children=[]){this.cls=cls.split(' ').filter(Boolean);this.attrs={...attrs};this.hidden=Boolean(attrs.hidden);this.checked=Boolean(attrs.checked);this.children=children;for(const c of children)c.parent=this;}
 matches(sel){if(sel.includes(','))return sel.split(',').some(part=>this.matches(part.replace(/^:scope>/,'')));if(sel==='[hidden]')return this.hidden;if(sel==='input[role="switch"]')return this.attrs.role==='switch';
  if(sel.startsWith('#'))return this.attrs.id===sel.slice(1);const role=sel.match(/^\[role="(\w+)"\]/),classes=sel.replace(/^\[role="\w+"\]/,'').split('.').filter(Boolean);
  if(!role&&!classes.length)throw new Error('selector '+sel);if(role&&this.attrs.role!==role[1])return false;return classes.every(c=>this.cls.includes(c));}
 get textContent(){return this.attrs.text||'';}
 closest(sel){for(let n=this;n;n=n.parent)if(n.matches(sel))return n;return null;}
 querySelectorAll(sel){if(sel.startsWith(':scope>'))return this.children.filter(c=>c.matches(sel.replace(/^:scope>/,'')));const out=[],walk=n=>{for(const c of n.children){if(c.matches(sel))out.push(c);walk(c);}};walk(this);return out;}
 querySelector(sel){return this.querySelectorAll(sel)[0]||null;}
 hasAttribute(name){return Object.hasOwn(this.attrs,name);}
 toggleAttribute(name,on){if(on)this.attrs[name]='';else delete this.attrs[name];}}
const row=(attrs={})=>new El('settings-row',attrs);
const on=new El('settings-section',{},[new El('settings-list',{},[row({'data-inactive':''}),row()])]);
const offRows=new El('settings-section',{},[new El('settings-list',{},[row({'data-inactive':''})])]);
const empty=new El('settings-section',{},[new El('settings-list',{},[new El('settings-empty')])]);
const hiddenSwitch=new El('settings-section',{},[new El('pref-list',{hidden:true},[new El('switch',{role:'switch',checked:true})])]);
const company=new El('settings-section',{},[new El('pref-list',{},[new El('switch',{role:'switch',checked:true})])]);
const confirming=new El('settings-section',{},[new El('settings-inline-confirm')]);
const receipt=new El('settings-section',{},[new El('settings-list',{},[row({'data-inactive':''})]),new El('error',{role:'status',text:'세션은 지웠지만 키체인 키는 지우지 못했습니다.'})]);
const settled=new El('settings-section',{},[new El('settings-list',{},[row({'data-inactive':''})]),new El('field-feedback',{role:'status',text:'Slack 연결을 해제했습니다.'})]);
const silent=new El('settings-section',{},[new El('settings-list',{},[row({'data-inactive':''})]),new El('field-feedback',{role:'status'})]);
const editing=new El('settings-section',{},[new El('settings-list',{hidden:true}),new El('folder-edit',{},[new El('settings-list')])]);
const closedEdit=new El('settings-section',{},[new El('folder-edit',{hidden:true})]);
const card=new El('settings-card',{},[offRows,empty]),group=new El('settings-group',{},[card]);
const card2=new El('settings-card',{},[on,new El('settings-section',{hidden:true})]),group2=new El('settings-group',{},[card2]);
const sections=[on,offRows,empty,hiddenSwitch,company,confirming,receipt,silent,editing,closedEdit,settled];
const ctx={document:{querySelectorAll:sel=>sel.includes('settings-group')?[group,group2]:sections}};
vm.createContext(ctx);vm.runInContext(source,ctx);
ctx.syncAddables();
assert.deepEqual(sections.map(s=>s.hasAttribute('data-off')),[false,true,true,true,false,false,false,true,false,true,true],
 'on: an active row, a visible switch that is on, a pending confirmation, an unresolved error or an open edit; off: only inactive rows, an empty state, a hidden switch, an empty status line, a closed editor or a settled success receipt');
// 사용 중 in the add dialog: something of the type is set up and working, not merely visible.
const stateRow=kind=>{const r=row();r.children=[new El('settings-state '+kind)];r.children[0].parent=r;return r;};
const unavailable=new El('settings-section',{},[new El('settings-list',{},[stateRow('attention')])]);
const working=new El('settings-section',{},[new El('settings-list',{},[stateRow('attention'),stateRow('active')])]);
assert.equal(ctx.addableInUse(unavailable),false,'an unavailable or attention-only type is shown but not 사용 중');
assert.equal(ctx.addableInUse(working),true);
assert.equal(ctx.addableInUse(company),true,'a switch that is on');
assert.equal(ctx.addableInUse(receipt),false,'a section kept only for its error is not 사용 중');
assert.equal(group.hasAttribute('data-off'),true,'a group whose sections are all off leaves the pane with its heading');
assert.equal(group2.hasAttribute('data-off'),false);
on.children[0].children[1].attrs['data-inactive']='';ctx.syncAddables();
assert.equal(on.hasAttribute('data-off'),true,'disconnecting the last row takes the section out on the next sync');
console.log('sync ok');
"""

DIALOG = r"""
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const app=fs.readFileSync(process.argv[1],'utf8');
const start=app.indexOf('let addSection=null'),source=app.slice(start,app.indexOf("$('add-dialog').addEventListener",start));
// a minimal tree: enough for before()/append()/replaceWith() and the handful of selectors the dialog uses
class El{constructor(id,cls='',attrs={}){this.id=id;this.cls=cls;this.attrs={...attrs};this.children=[];this.parent=null;this.hidden=false;this.open=false;this.textContent=attrs.text||'';this.dataset=attrs.dataset||{};}
 get tag(){return this.attrs.tag||'section';}
 append(...nodes){for(const n of nodes){n.remove?.();n.parent=this;this.children.push(n);}}
 remove(){if(this.parent){this.parent.children.splice(this.parent.children.indexOf(this),1);this.parent=null;}}
 before(n){const i=this.parent.children.indexOf(this);n.parent=this.parent;this.parent.children.splice(i,0,n);}
 replaceWith(n){const p=this.parent,i=p.children.indexOf(this);n.remove?.();n.parent=p;p.children.splice(i,1,n);this.parent=null;}
 get isConnected(){let n=this;while(n.parent)n=n.parent;return n===root;}
 hasAttribute(name){return Object.hasOwn(this.attrs,name);}
 matches(sel){return sel==='details'?this.tag==='details':false;}
 querySelectorAll(sel){const out=[],walk=n=>{for(const c of n.children){if(sel==='details[data-add-editor]'&&c.tag==='details'&&c.hasAttribute('data-add-editor'))out.push(c);else if(sel==='details.settings-editor'&&c.tag==='details'&&c.cls.includes('settings-editor'))out.push(c);else if(sel==='h3,summary'&&(c.tag==='h3'||c.tag==='summary'))out.push(c);walk(c);}};walk(this);return out;}
 querySelector(sel){return this.querySelectorAll(sel)[0]||null;}
 focus(){focused=this;}}
let focused=null;
const root=new El('root'),pane=new El('settings-external'),card=new El('card'),host=new El('add-dialog-host'),title=new El('add-dialog-title');
const elsewhere=new El('elsewhere'),feedback=new El('slack-feedback');elsewhere.append(feedback);
const slack=new El('slack','settings-section',{'data-add':'external','data-off':'',dataset:{addFeedback:'slack-feedback'}}),setup=new El('slack-setup','settings-editor',{tag:'details'}),form=new El('slack-form');
setup.append(form);slack.append(new El('h','',{tag:'h3',text:'Slack'}),setup);card.append(slack);pane.append(card);root.append(pane,host,title,elsewhere);
const nodes={'slack-feedback':feedback,'add-dialog-host':host,'add-dialog-title':title,'add-dialog-types':new El('types'),'add-dialog-back':new El('back')};
let syncs=0;const ctx={$:id=>nodes[id],document:{createComment:()=>new El('placeholder','',{tag:'#comment'})},scheduleSettingsPaint:()=>syncs++,t:x=>x};
vm.createContext(ctx);vm.runInContext(source,ctx);
ctx.showAddType(slack,'Slack');
assert.equal(slack.parent,host,'the type opens its own section in the dialog');
assert.equal(setup.open,true,'an off section opens its setup form');
assert.equal(form.parent,setup,'the same form element (and its handlers) is used; nothing is copied');
assert.equal(feedback.parent,host,'its status line comes along, so an error is shown next to the form');
assert.equal(title.textContent,'Slack');assert.equal(nodes['add-dialog-back'].hidden,false);assert.equal(focused,title);
ctx.restoreAddSection();
assert.equal(slack.parent,card,'closing puts the section back where it was');
assert.equal(card.children.indexOf(slack),0);
assert.equal(feedback.parent,elsewhere,'and the status line goes back too');
assert(syncs>0,'and re-checks what the pane shows');
console.log('dialog ok');
"""


class Renderers(unittest.TestCase):
    def test_rows_for_services_not_set_up_are_marked(self):
        run_node(ROWS)

    def test_sections_and_groups_leave_the_pane_only_when_off(self):
        run_node(SYNC)

    def test_add_dialog_moves_the_types_own_section_in_and_back(self):
        run_node(DIALOG)


if __name__ == "__main__":
    unittest.main()
