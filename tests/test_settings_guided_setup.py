"""#317: console paths and connected-state forms, using shipped HTML and renderers."""
from html.parser import HTMLParser
import json
from pathlib import Path
import re
import shutil
import subprocess
import unittest

from personal_agent import main_ai, search_providers

WEB = Path(__file__).resolve().parents[1] / 'src' / 'personal_agent' / 'web'
APP = (WEB / 'app.js').read_text()
HTML = (WEB / 'index.html').read_text()


class Markup(HTMLParser):
    def __init__(self):
        super().__init__()
        self.root = {'tag': '', 'attrs': {}, 'children': []}
        self.stack = [self.root]
        self.feed(HTML)

    def handle_starttag(self, tag, attrs):
        node = {'tag': tag, 'attrs': dict(attrs), 'children': []}
        self.stack[-1]['children'].append(node)
        if tag not in {'input', 'meta', 'link', 'br', 'hr', 'img'}:
            self.stack.append(node)

    def handle_endtag(self, tag):
        for i in range(len(self.stack) - 1, 0, -1):
            if self.stack[i]['tag'] == tag:
                del self.stack[i:]
                break

    def handle_data(self, data):
        self.stack[-1]['children'].append(data)


def descendants(node):
    for child in node['children']:
        if isinstance(child, dict):
            yield child
            yield from descendants(child)


def text(node):
    return ''.join(text(c) if isinstance(c, dict) else c for c in node['children'])


def run_node(script):
    node = shutil.which('node')
    if node is None:
        raise unittest.SkipTest('node is required for renderer checks')
    result = subprocess.run([node, '-e', script, str(WEB / 'app.js')],
                            capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr[-4000:]
    return result.stdout


BOOT = r"""
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const app=fs.readFileSync(process.argv[1],'utf8');
const nodes={};
function element(tag,value,cls){return {tag,textContent:value||'',className:cls||'',dataset:{},hidden:false,value:'',open:false,disabled:false,children:[],append(...items){this.children.push(...items);},replaceChildren(...items){this.children=items;},setAttribute(k,v){this[k]=v;},focus(){}};}
const ctx={element,$:id=>nodes[id],t:v=>v,settingsRow:()=>element('div'),inactive:v=>v};
vm.createContext(ctx);
const load=(start,end)=>vm.runInContext(app.slice(app.indexOf(start),app.indexOf(end,app.indexOf(start))),ctx);
"""


class Structure(unittest.TestCase):
    def setUp(self):
        self.nodes = list(descendants(Markup().root))

    def by_id(self, id):
        return next(n for n in self.nodes if n['attrs'].get('id') == id)

    def test_channel_lists_and_safe_official_links(self):
        for id, expected in [('telegram-form', 'https://t.me/BotFather'),
                             ('mattermost-form', 'https://docs.mattermost.com/developers/integrate/reference/bot-accounts'),
                             ('slack-setup', 'https://api.slack.com/apps')]:
            section = list(descendants(self.by_id(id)))
            steps = [n for n in section if n['tag'] == 'ol' and n['attrs'].get('class') == 'settings-steps']
            self.assertEqual(len(steps), 1)
            links = [n for n in descendants(steps[0]) if n['tag'] == 'a']
            self.assertIn(expected, [n['attrs'].get('href') for n in links])
            for link in links:
                self.assertEqual(link['attrs'].get('target'), '_blank')
                self.assertEqual(link['attrs'].get('rel'), 'noreferrer')
                self.assertEqual(link['attrs'].get('class'), 'settings-step-link')
            for item in steps[0]['children']:
                self.assertEqual(item['tag'], 'li')
                self.assertTrue(all(isinstance(c, str) or c['tag'] == 'a' for c in item['children']))

    def test_slack_bot_token_path_precedes_app_token_path(self):
        steps = next(n for n in descendants(self.by_id('slack-setup')) if n['tag'] == 'ol')
        items = [text(n) for n in steps['children']]
        bot = next(i for i, value in enumerate(items) if 'Bot User OAuth Token' in value)
        app = next(i for i, value in enumerate(items) if 'App-Level Tokens' in value)
        self.assertIn('OAuth & Permissions', items[bot])
        self.assertNotIn('Basic Information', items[bot])
        self.assertIn('connections:write', items[app])
        self.assertLess(bot, app)
        self.assertEqual([n['tag'] for n in self.by_id('slack-setup')['children'] if isinstance(n, dict)],
                         ['summary', 'details', 'form'])

    def test_guides_cover_exact_server_key_providers(self):
        data = run_node(BOOT + "load('const SETUP_GUIDES=', 'function setupStepList('); console.log(vm.runInContext('JSON.stringify(SETUP_GUIDES)',ctx));")
        guides = json.loads(data)
        self.assertEqual(set(guides), set(main_ai.API_ROUTES) | set(search_providers.SearchProviderSettings.PROVIDERS))
        for steps in guides.values():
            self.assertTrue(steps)
            self.assertTrue(all(len(step) == 3 for step in steps))
        self.assertIsNotNone(re.search(r'^const SETUP_GUIDES=.+;$', APP, re.M))

    def test_token_inputs_remain_write_only(self):
        for id in ['telegram-token', 'mattermost-token', 'slack-bot-token', 'slack-app-token']:
            attrs = self.by_id(id)['attrs']
            self.assertEqual(attrs['type'], 'password')
            self.assertEqual(attrs['autocomplete'], 'off')
            self.assertNotIn('value', attrs)


class Renderers(unittest.TestCase):
    def test_connected_values_and_drafts_and_tokens(self):
        run_node(BOOT + r"""
for(const channel of ['mattermost','slack']){
 const tokens=channel==='slack'?['slack-bot-token','slack-app-token']:['mattermost-token'];
 const ids=['status','disconnect','setup-summary','company','company-approved','setup','owner','token-hint'].map(s=>channel+'-'+s);
 for(const id of [...ids,...tokens,'mattermost-server'])nodes[id]=element('div');
 load('function render'+(channel==='slack'?'Slack':'Mattermost')+'(',"$('"+channel+"-form').onsubmit");
 const render=ctx[channel==='slack'?'renderSlack':'renderMattermost'];
 const status={connected:true,usable:true,server_url:'https://chat.example',owner_username:'alice',owner_user_id:'U123'};
 render(status);
 assert.equal(nodes[channel+'-owner'].value,channel==='slack'?'U123':'alice');
 if(channel==='mattermost')assert.equal(nodes['mattermost-server'].value,'https://chat.example');
 for(const id of tokens)assert.equal(nodes[id].value,'');
 assert.equal(nodes[channel+'-token-hint'].hidden,false);
 nodes[channel+'-setup'].open=true;nodes[channel+'-owner'].value='draft';nodes['mattermost-server'].value='draft server';
 for(const id of tokens)nodes[id].value='unsaved token';
 render({...status,owner_username:'changed',owner_user_id:'U456',server_url:'https://changed.example'});
 assert.equal(nodes[channel+'-owner'].value,'draft');
 if(channel==='mattermost')assert.equal(nodes['mattermost-server'].value,'draft server');
 for(const id of tokens)assert.equal(nodes[id].value,'unsaved token');
 render({connected:false});assert.equal(nodes[channel+'-token-hint'].hidden,true);
 // Missing optional nodes are supported by older renderer fixtures.
 for(const id of ['setup','owner','token-hint','server'])delete nodes[channel+'-'+id];
 render(status);
}
""")

    def test_shared_google_list_and_open_memory(self):
        run_node(BOOT + r"""
load('const SETUP_GUIDES=', 'function settingsRow(');
load('const GOOGLE_CLIENT_STEPS=', 'let googleClientRemoving=');
const count=n=>n.children.filter(v=>typeof v==='object').reduce((a,c)=>a+(c.tag==='li'?1:0)+count(c),0);
const expected=vm.runInContext('GOOGLE_CLIENT_STEPS.length',ctx);
let guide=ctx.googleClientGuide(true);assert.equal(count(guide),expected);assert.equal(guide.open,true);
assert.equal(guide.children.at(-1).className,'settings-technical-line');
guide.open=false;guide.ontoggle();guide=ctx.googleClientGuide(true);assert.equal(guide.open,false);
for(const child of guide.children[1].children)for(const link of child.children.filter(c=>c.tag==='a')){assert.equal(link.target,'_blank');assert.equal(link.rel,'noreferrer');}
""")

    def test_telegram_guide_opens_by_default_only_before_a_bot_is_set_up(self):
        run_node(BOOT + r"""
ctx.telegramDraftOpen=false;ctx.setFeedback=()=>{};
for(const id of ['telegram-current','telegram-change','telegram-form','telegram-guide','telegram-token','telegram-status','disconnect','new-pair','telegram-submit','telegram-pair'])nodes[id]=element('div');
load('function renderTelegram(', '// Owner-visible words');
load('// #317: the BotFather steps', "$('telegram-form-cancel').onclick");
nodes['telegram-form'].hidden=true;
ctx.renderTelegram({telegram:{enabled:false}});assert.equal(nodes['disconnect'].hidden,true);
nodes['telegram-change'].onclick();assert.equal(nodes['telegram-form'].hidden,false);assert.equal(nodes['telegram-guide'].open,true);
nodes['telegram-guide'].open=false;ctx.renderTelegram({telegram:{enabled:false}});assert.equal(nodes['telegram-guide'].open,false,'polling keeps the owner\'s fold');
nodes['telegram-change'].onclick();assert.equal(nodes['telegram-form'].hidden,true);
ctx.renderTelegram({telegram:{enabled:true}});assert.equal(nodes['disconnect'].hidden,false);
nodes['telegram-change'].onclick();assert.equal(nodes['telegram-guide'].open,false,'a configured bot opens the token form with the steps folded');
""")

    def test_api_guide_defaults_and_search_fallback(self):
        run_node(BOOT + r"""
ctx.settingsAction=label=>element('button',label);ctx.aiKeyEditing='';ctx.aiKeyRemoving='';ctx.aiDate=()=>'';
load('const SETUP_GUIDES=', 'function settingsRow(');
load('function chooserKeyForm(', 'function chooserRow(');
const find=(node,tag)=>node.children.find(c=>c.tag===tag);
let panel=ctx.chooserAccount({id:'openai',name:'OpenAI',kind:'api',key:{saved:false}});
assert.equal(find(panel,'details').open,true);assert.equal(find(panel,'details').children[1].children.length,3);
assert(panel.children.indexOf(find(panel,'details'))<panel.children.indexOf(find(panel,'form')));
panel=ctx.chooserAccount({id:'openai',name:'OpenAI',kind:'api',key:{saved:true}});assert.equal(find(panel,'details'),undefined);assert.equal(find(panel,'form'),undefined);
ctx.aiKeyEditing='openai';panel=ctx.chooserAccount({id:'openai',name:'OpenAI',kind:'api',key:{saved:true}});assert.equal(find(panel,'details').open,false);
load('function searchKeyForm(', 'function renderSearchProviders(');
let form=ctx.searchKeyForm({id:'brave',setup:'legacy setup',fields:[]});assert.equal(find(form,'details').open,true);assert.equal(find(form,'span'),undefined);
form=ctx.searchKeyForm({id:'future-provider',setup:'legacy setup',fields:[]});assert.equal(find(form,'details'),undefined);assert.equal(find(form,'span').textContent,'legacy setup');
""")
