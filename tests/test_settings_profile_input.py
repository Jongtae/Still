"""#390 U6: readable profile input without changing stored keys."""
import json
import re
import shutil
import subprocess
from html.parser import HTMLParser
from pathlib import Path

import pytest

from personal_agent.memory_service import _PROFILE_KEY
from test_owner_profile import UI_CHECK

WEB = Path(__file__).parents[1] / 'src/personal_agent/web'


def node_run(script):
    node = shutil.which('node')
    if not node:
        pytest.skip('Node is needed for JavaScript behaviour checks')
    return subprocess.run([node, '-e', script, str(WEB / 'app.js')],
                          capture_output=True, text=True, check=True, timeout=20).stdout


def test_profile_labels_and_server_key_compatibility():
    cases = {
        '땅콩 알러지': 'profile.땅콩_알러지',
        'allergy.peanut': 'profile.allergy.peanut',
        'profile.place.home': 'profile.place.home',
        '  ..  ': '',
        '': '',
        'profile.': '',
        '  profile. 집 . 자주\t 가는   서점  . ': 'profile.집.자주_가는_서점',
        '__food_preference__': 'profile.food_preference',
        ' . __ . ': '',
        'a\u0085b\u001cc': 'profile.a_b_c',
        '日本語 好み': 'profile.日本語_好み',
    }
    outputs = json.loads(node_run("""
const fs=require('node:fs'),vm=require('node:vm');
const ctx={module:{exports:{}}};vm.createContext(ctx);
vm.runInContext(fs.readFileSync(process.argv[1],'utf8'),ctx);
process.stdout.write(JSON.stringify(INPUTS.map(ctx.module.exports.profileKeyFromLabel)));
""".replace('INPUTS', json.dumps(list(cases)))))
    assert outputs == list(cases.values())
    server_pattern = re.compile(_PROFILE_KEY.pattern)
    for key in outputs:
        if key:
            assert server_pattern.fullmatch(key), repr(key)


def test_profile_titles_are_words_with_non_profile_fallback():
    cases = {'profile.allergy.peanut': 'allergy · peanut',
             'profile.땅콩_알러지': '땅콩 알러지',
             'profile.food_preference': 'food preference',
             'meeting-time': 'meeting-time'}
    outputs = json.loads(node_run("""
const fs=require('node:fs'),vm=require('node:vm');
const ctx={module:{exports:{}}};vm.createContext(ctx);
vm.runInContext(fs.readFileSync(process.argv[1],'utf8'),ctx);
process.stdout.write(JSON.stringify(INPUTS.map(ctx.module.exports.profileTitle)));
""".replace('INPUTS', json.dumps(list(cases)))))
    assert outputs == list(cases.values())


class ProfileInput(HTMLParser):
    def __init__(self):
        super().__init__()
        self.attrs = None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'input' and attrs.get('id') == 'owner-profile-key':
            self.attrs = attrs


def test_ordinary_profile_input_uses_a_human_label():
    html = (WEB / 'index.html').read_text()
    parser = ProfileInput()
    parser.feed(html)
    assert parser.attrs is not None
    for name, value in [('id', 'owner-profile-key'), ('name', 'owner-profile-key'),
                        ('maxlength', '150'), ('required', None),
                        ('placeholder', '예: 땅콩 알러지, 집, 자주 가는 서점…')]:
        assert name in parser.attrs and parser.attrs[name] == value
    form = html.split('<form id="owner-profile-form"', 1)[1].split('</form>', 1)[0]
    assert '<label>항목<input' in form
    assert 'allergy.peanut' not in form
    assert 'profile.이 자동으로 붙습니다' not in form
    assert '같은 항목 이름으로 다시 저장하면 이전 내용을 바꿉니다.' in form
    assert 'id="owner-profile-content"' in form


def test_submit_feedback_and_disclosed_key_use_real_profile_code():
    # Reuse the existing profile DOM/API stand-in; execute the shipped submit handler too.
    harness = UI_CHECK.split('(async()=>{', 1)[0]
    node_run(harness + r"""
for(const id of ['owner-profile-form','owner-profile-key','owner-profile-content'])new Element('input').id=id;
let focused=false;$('owner-profile-key').focus=()=>{focused=true;};
vm.runInContext(line("$('owner-profile-form').onsubmit="),ctx);
const submit=()=> $('owner-profile-form').onsubmit({preventDefault(){},submitter:new Element('button')});
(async()=>{
 ctx.profileRows=[];await ctx.loadOwnerProfile();
 $('owner-profile-key').value='  ..  ';$('owner-profile-content').value='keep this draft';
 await submit();assert(focused);assert.equal(calls.filter(c=>c.body).length,0);
 assert.equal($('owner-profile-feedback').textContent,'항목 이름을 적어 주세요.');
 assert.equal($('owner-profile-content').value,'keep this draft');
 $('owner-profile-key').value='땅콩 알러지';await submit();
 const writes=()=>calls.filter(c=>c.path==='/api/personal-space/profile/request');
 assert.equal(writes()[0].body.memory_key,'profile.땅콩_알러지');
 assert.equal(writes()[0].body.content,'keep this draft');
 assert.equal($('owner-profile-feedback').textContent,'프로필을 저장했습니다.');
 assert.equal($('owner-profile-key').value,'');assert.equal($('owner-profile-content').value,'');
 ctx.profileRows=[{id:'m1',memory_key:'profile.땅콩_알러지',content:'old',created:1}];await ctx.loadOwnerProfile();
 const row=rows()[0],details=descendants(row).find(n=>n.tag==='details');
 assert(details);assert(!details.open);assert.equal(details.children[0].textContent,'기술 정보');
 assert.equal(details.children[1].textContent,'키: profile.땅콩_알러지');
 assert.equal(details.children[1].attrs.translate,'no');
 assert(!row.children[0].textContent.includes('profile.'));
 $('owner-profile-key').value='땅콩 알러지';$('owner-profile-content').value='new';await submit();
 assert.equal(writes()[1].body.memory_key,'profile.땅콩_알러지');assert.equal(writes()[1].body.content,'new');
 assert.equal($('owner-profile-feedback').textContent,'땅콩 알러지 항목을 새 내용으로 바꿨습니다.');
})().catch(error=>{console.error(error);process.exitCode=1;});
""")
