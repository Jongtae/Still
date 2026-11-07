"""Settings folder selection fills a draft and never issues authority (#390 U6)."""
import json
import shutil
import subprocess
import threading
from html.parser import HTMLParser
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from unittest.mock import Mock, patch

import pytest

from personal_agent import local_folder_picker
from personal_agent.messages import msg
from personal_agent.quickstart import make_handler
from personal_agent.quickstart_service import AgentService
from personal_agent.quickstart_store import QuickStore

WEB = Path(__file__).resolve().parents[1] / 'src/personal_agent/web'


@pytest.fixture
def service(tmp_path):
    return AgentService(QuickStore(tmp_path / 'data'))


def snapshot(service):
    with service.store.db() as db:
        database = list(db.iterdump())
    return (service.store.config('file_roots'), service.store.config('file_workspace'),
            database, dict(service._local_selections))


@pytest.mark.parametrize('kind,prompt', [('read', 'pick_read_folder'),
                                        ('reference', 'pick_reference_folder'),
                                        ('result', 'pick_result_folder')])
def test_selection_validates_path_uses_existing_prompt_and_writes_nothing(service, tmp_path, kind, prompt):
    folder = tmp_path / 'owner-documents'
    folder.mkdir()
    service.folder_picker = Mock(return_value=str(folder))
    before = snapshot(service)
    assert service.pick_settings_folder({'kind': kind}) == {
        'state': 'selected', 'name': folder.name, 'path': str(folder.resolve())}
    service.folder_picker.assert_called_once_with(msg('quickstart_service.' + prompt))
    assert snapshot(service) == before


def test_cancel_writes_nothing(service):
    service.folder_picker = Mock(return_value=None)
    before = snapshot(service)
    assert service.pick_settings_folder({'kind': 'read'}) == {'state': 'cancelled'}
    assert snapshot(service) == before


@pytest.mark.parametrize('value', [str(Path.home()), '/nonexistent-still-settings-picker-folder'])
def test_invalid_folder_writes_nothing(service, value):
    service.folder_picker = Mock(return_value=value)
    before = snapshot(service)
    with pytest.raises(ValueError):
        service.pick_settings_folder({'kind': 'read'})
    assert snapshot(service) == before


@pytest.mark.parametrize('kind', ['unknown', None, []])
def test_unknown_kind_does_not_open_picker(service, kind):
    service.folder_picker = Mock()
    before = snapshot(service)
    with pytest.raises(ValueError, match=msg('quickstart_service.invalid_folder_kind')):
        service.pick_settings_folder({'kind': kind})
    service.folder_picker.assert_not_called()
    assert snapshot(service) == before


@pytest.mark.parametrize('error', [local_folder_picker.FolderPickerBusy,
                                   local_folder_picker.FolderPickerUnavailable])
def test_picker_errors_propagate_without_writing(service, error):
    service.folder_picker = Mock(side_effect=error('picker refused'))
    before = snapshot(service)
    with pytest.raises(error):
        service.pick_settings_folder({'kind': 'result'})
    assert snapshot(service) == before


def test_default_native_picker_is_reused(service):
    service.folder_picker = None
    with patch.object(local_folder_picker, 'choose_folder', return_value=None) as picker:
        assert service.pick_settings_folder({'kind': 'read'}) == {'state': 'cancelled'}
    picker.assert_called_once_with(msg('quickstart_service.pick_read_folder'))


def test_http_surface_auth_origin_and_tunnel_gates(service, tmp_path):
    folder = tmp_path / 'owner-documents'
    folder.mkdir()
    service.folder_picker = Mock(return_value=str(folder))
    try:
        server = ThreadingHTTPServer(('127.0.0.1', 0), make_handler(service, (), 'pairing-token'))
    except PermissionError:
        pytest.skip('sandbox does not permit listening sockets')
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    try:
        service.store.claim(service.store.bootstrap.read_text(), 'long-password-test')
        base = f'http://127.0.0.1:{server.server_port}'
        session = {'Cookie': 'agentos_session=' + service.store.local_session()}

        def post(headers, body=None):
            request = Request(base + '/api/files/pick-folder',
                              data=json.dumps(body or {'kind': 'read'}).encode(),
                              headers={'Content-Type': 'application/json', **headers})
            try:
                response = urlopen(request, timeout=5)
            except HTTPError as error:
                response = error
            with response:
                return response.code, json.loads(response.read())

        status, reply = post({**session, 'X-Forwarded-For': '198.51.100.7'})
        assert status == 403
        assert reply == {'error': msg('quickstart.folder_continue_on_mac'), 'reason': 'owner_local_surface'}
        assert post({**session, 'Origin': 'https://other.example'})[0] == 403
        assert post({})[0] == 401
        service.folder_picker.assert_not_called()
        status, reply = post(session)
        assert (status, reply['state']) == (200, 'selected')
        service.folder_picker.assert_called_once()
        assert post(session, {'kind': 'unknown'})[0] == 400
    finally:
        server.shutdown()
        thread.join()
        server.server_close()


class FormParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.form = None
        self.nodes = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'form':
            self.form = attrs.get('id')
        self.nodes.append((self.form, tag, attrs))

    def handle_endtag(self, tag):
        if tag == 'form':
            self.form = None


def test_static_buttons_and_typed_path_fallback():
    parser = FormParser()
    parser.feed((WEB / 'index.html').read_text())
    buttons = [(form, attrs) for form, tag, attrs in parser.nodes if 'data-folder-pick' in attrs]
    assert len(buttons) == 3
    for kind, input_id, form_id in [('read', 'root-path-input', 'roots-form'),
                                     ('reference', 'workspace-reference-input', 'file-workspace-form'),
                                     ('result', 'file-workspace-path', 'file-workspace-form')]:
        form, button = next(row for row in buttons if row[1]['data-folder-pick'] == kind)
        assert form == form_id
        assert button['type'] == 'button'
        assert 'hidden' in button
        assert button['data-folder-input'] == input_id
        assert any(tag == 'input' and attrs.get('id') == input_id for _, tag, attrs in parser.nodes)
    assert any(form == 'roots-form' and tag == 'button' and attrs.get('type') == 'submit'
               for form, tag, attrs in parser.nodes)


def test_picker_client_drafts_visibility_cancel_and_error():
    node = shutil.which('node')
    if not node:
        pytest.skip('node is required')
    script = r'''
const fs=require('fs'),vm=require('vm'),assert=require('assert');
const app=fs.readFileSync(process.argv[1],'utf8'),nodes={},buttons=[];
for(const kind of ['read','reference','result']){
 const submit={focus(){this.focused=true;}};
 const input={value:'typed draft',closest(){return {querySelector(){return submit;}};}};
 const button={dataset:{folderPick:kind},hidden:true,disabled:false,textContent:'Pick'};
 buttons.push(button);nodes[kind]=input;nodes[kind+'-submit']=submit;
}
nodes['workspace-reference-add']=nodes['reference-submit'];
for(const id of ['folder-picker-hint','folder-path-hint'])nodes[id]={hidden:false};
let result={state:'selected',path:'/picked/folder',name:'folder'},calls=[],feedback=[],failure=null,resolve;
const ctx={folderRequests:{picker:false,local_surface:false},document:{querySelectorAll:()=>buttons,
 querySelector:s=>buttons.find(b=>s.includes('"'+b.dataset.folderPick+'"'))},$:id=>nodes[id],
 t:(s,args)=>args?s.replace('{name}',args.name):s,setFeedback:(...v)=>feedback.push(v),
 folderSaveRefused:(...v)=>feedback.push(v),api:async(path,body)=>{calls.push({path,body});if(failure)throw failure;return result;}};
vm.createContext(ctx);
vm.runInContext(app.slice(app.indexOf('async function busy('),app.indexOf('function safeTime('))+
 app.slice(app.indexOf('function paintFolderPickers('),app.indexOf('for(const button of document.querySelectorAll(\'[data-folder-pick]\'))')),ctx);
(async()=>{
 for(const picker of [false,true])for(const local_surface of [false,true]){
 ctx.folderRequests={picker,local_surface};ctx.paintFolderPickers();
 assert(buttons.every(b=>b.hidden===!(picker&&local_surface)));
 assert.equal(nodes['folder-picker-hint'].hidden,!(picker&&local_surface));
 assert.equal(nodes['folder-path-hint'].hidden,picker&&local_surface);
 }
 for(const kind of ['read','reference','result']){
 const button=ctx.folderPickButton(kind,nodes[kind],'feedback','selected {name}');
 await button.onclick();assert.equal(nodes[kind].value,'/picked/folder');
 assert(nodes[kind+'-submit'].focused);assert.equal(button.disabled,false);
 }
 assert(calls.every(c=>c.path==='/api/files/pick-folder'),'selection never calls save endpoints');
 assert.deepEqual(calls.map(c=>c.body.kind),['read','reference','result']);
 const button=buttons[0];nodes.read.value='keep draft';result={state:'cancelled'};
 await button.onclick();assert.equal(nodes.read.value,'keep draft');
 assert.equal(feedback.at(-1)[1],'폴더를 고르지 않았습니다.');
 failure=new Error('refused');await button.onclick();assert.equal(feedback.at(-1)[1],failure);
 failure=null;result=new Promise(r=>resolve=r);const pending=button.onclick();
 assert(button.disabled);resolve({state:'cancelled'});await pending;assert(!button.disabled);
})().catch(e=>{console.error(e);process.exitCode=1;});
'''
    result = subprocess.run([node, '-e', script, str(WEB / 'app.js')], capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
