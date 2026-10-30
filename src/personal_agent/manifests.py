"""Validated declarations for Still roles and bounded local tools."""
import json
import re
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class HostTool:
    """Every attribute of one host action, declared once (HARDEN-GENERIC-01 #1445 part 4).

    No attribute has a default: a new host action names each one, or this module does not
    import.  The sets the runtime, the CLI profiles and the information-use audit read are
    derived from ``HOST_TOOLS``; adding ``code_run`` once took about fifteen hand-kept lists.
    """
    #: ``bounded_write`` mode: a read-only role cannot declare it, and it is not an effect-free read.
    write: bool
    #: Offered on a read-only Work surface.
    read_only_surface: bool
    #: Runs in the Still service, which holds its owner state; a trusted-local CLI reaches it through the relay.
    service_held: bool
    #: The information-use category of the owner information it reads, or None.
    reads: str | None
    #: Sends a lookup to a public destination (the information-use audit lists where).
    lookup: bool
    #: The argument(s) recorded only by length (typed text, a body, code), or None.
    recorded_by_length: str | tuple | None


#: name -> (write, read_only_surface, service_held, reads, lookup, recorded_by_length)
HOST_TOOLS={
    'web_search': HostTool(False, True, False, None, True, None),
    'public_page_read': HostTool(False, True, False, None, True, None),
    'weather': HostTool(False, True, False, None, True, None),
    'calendar_query': HostTool(False, True, True, 'calendar', False, None),
    'list_roots': HostTool(False, True, False, 'files', False, None),
    'find_files': HostTool(False, True, False, 'files', False, None),
    'read_file': HostTool(False, True, False, 'files', False, None),
    'list_notes': HostTool(False, True, False, 'notes', False, None),
    'save_note': HostTool(True, False, False, 'notes', False, None),
    # #182: a new result file in the file workspace's result folder; its text is recorded by length only.
    'save_result': HostTool(True, False, False, 'files', False, 'content'),
    'list_memory': HostTool(False, True, True, 'memory', False, None),
    'search_memory': HostTool(False, True, True, 'memory', False, None),
    'search_conversation': HostTool(False, True, True, 'conversation', False, None),
    'save_memory': HostTool(True, False, True, 'memory', False, None),
    'list_agents': HostTool(False, True, False, None, False, None),
    'delegate_agent': HostTool(True, False, False, None, False, None),
    # SEC-BROWSER-01 (#656): the owner-logged-in browser profile; a navigation or read submits no form.
    'browser_open': HostTool(False, True, False, 'browser', False, None),
    'browser_read': HostTool(False, True, False, 'browser', False, None),
    'browser_find': HostTool(False, True, False, 'browser', False, None),
    'browser_click': HostTool(True, True, False, 'browser', False, None),
    'browser_type': HostTool(True, True, False, 'browser', False, 'text'),
    # BROWSE-09 (#953): a sign-in request for the owner, read-only on the site.
    'browser_sign_in': HostTool(False, True, False, 'browser', False, None),
    # CONTEXT-STATE-01 (#627): a revisable current-state hypothesis.
    'propose_current_state': HostTool(True, False, False, 'current_context', False, 'value'),
    # SEC-ATTN-01 (#659): an owner-accepted preparation (reminder/prepare).
    'schedule_preparation': HostTool(True, False, True, None, False, None),
    # #774: ask the owner for a current position in the paired Telegram chat.
    'ask_location': HostTool(True, False, True, None, False, None),
    # #407: one short message to the owner while the Work runs; its text is the transcript's, recorded by length.
    'tell_owner': HostTool(True, False, True, None, False, 'text'),
    # #794 phase 3: the owner's exact forget, complete deletion and undo.
    'forget_record': HostTool(True, False, True, 'memory', False, None),
    # #814: owner settings read, and a change drafted for the owner to confirm.
    'settings_read': HostTool(False, True, True, 'settings', False, None),
    'settings_change': HostTool(True, False, True, None, False, None),
    'capability_search': HostTool(False, True, True, 'settings', True, None),
    # #826: which owner information an earlier answer used and where it went (read-only).
    'information_use': HostTool(False, True, True, 'records', False, None),
    # #961: read one pinned skill this Work may load, or one of its packaged text files.
    'skill_load': HostTool(False, True, False, None, False, None),
    'skill_resource': HostTool(False, True, False, None, False, None),
    # #32: an advisory note in the Work's own record that no available skill had the method it needed.
    # Its note is recorded by length only; the bounded, filtered note is the call's evidence (#32 review).
    'method_gap': HostTool(False, True, False, None, False, 'note'),
    # #1172: read-only search and read of the owner's connected Google Drive.
    'drive_search': HostTool(False, True, True, 'drive', False, None),
    'drive_read': HostTool(False, True, True, 'drive', False, None),
    # #150: read-only search and decoded body read of the owner's connected Gmail.
    'mail_search': HostTool(False, True, True, 'mail', False, None),
    'mail_read': HostTool(False, True, True, 'mail', False, None),
    # #1216: an authenticated API call by secret-slot reference (non-read needs approval).
    'api_request': HostTool(True, False, True, 'api', False, 'body'),
    # #1380: Python the AI wrote, run in an isolated sandbox. It sends only GET/HEAD and owner-reviewed
    # reads (``code_run.CodeRunner._decide``), so it is not a write; it becomes one if state changes are enabled.
    # #1486: a proposed function library is code too.
    'code_run': HostTool(False, False, True, None, True, ('code', 'library')),
}
HOST_ACTIONS=set(HOST_TOOLS)
WRITE_ACTIONS={name for name,tool in HOST_TOOLS.items() if tool.write}
#: #627: declared host actions ``Capabilities.offered_tools`` offers only while
#: the owner has current context on.  Off, every route's surface is exactly the
#: pre-#627 one; the declarations (CLI profiles, Settings, the Claude
#: allowlist) are the same either way.
CONTEXT_GATED_ACTIONS=frozenset({'propose_current_state'})
ROLE_PERMISSIONS={'read_only','bounded_write'}
#: Tool ids the agent loop itself owns (#657 `finish`); no package may declare one.
RESERVED_TOOL_IDS=frozenset({'finish'})
#: Package ids only Still may use: its tools (#604) and, for skill packages, its bundled skills (#961).
#: ``agentos`` is reserved only for skill packages, so an existing tool package of that id keeps working.
RESERVED_PACKAGE_IDS=frozenset({'builtin'})
RESERVED_SKILL_PACKAGE_IDS=frozenset({'agentos'})
#: #960 section 5: the compatibility statuses a declared skill may carry.
SKILL_STATUSES=frozenset({'supported_as_is','adapted','requires_connection','unsupported_environment',
                          'scripts_or_hooks_required','licence_unknown','invalid_package'})

BUILTIN_MANIFEST={'version':1,'id':'builtin','tools':[{'id':name,'host_action':name,'mode':'bounded_write' if name in WRITE_ACTIONS else 'read_only'} for name in sorted(HOST_ACTIONS)],'roles':[
 {'id':'researcher','name':'조사 에이전트','instructions':'Research the assigned question using read-only tools when needed. Cite evidence and identify gaps. Never invent findings.','permissions':['read_only'],'tools':['web_search','weather','list_roots','find_files','read_file','list_notes','list_agents']},
 {'id':'reviewer','name':'검토 에이전트','instructions':'Produce a review report of the supplied material: findings, uncertainties, and concrete improvements. Review what is provided now. Do not ask whether to edit or save files; editing is not your task. Use read-only tools only if evidence is missing.','permissions':['read_only'],'tools':['web_search','weather','list_roots','find_files','read_file','list_notes','list_agents']},
 {'id':'planner','name':'계획 에이전트','instructions':'Turn the supplied goal into a concise, ordered plan. State assumptions, dependencies, risks, and what needs the owner\'s decision. Use only supplied material or read-only evidence. Never perform actions, change notes, or delegate work.','permissions':['read_only'],'tools':['web_search','weather','list_roots','find_files','read_file','list_notes']},
]}

def _id(value):return isinstance(value,str) and bool(value) and value.replace('-','').replace('_','').isalnum() and value[0].isalpha()

def validate(manifest):
 if not isinstance(manifest,dict) or manifest.get('version')!=1:raise ValueError('매니페스트 버전 1이 필요합니다.')
 if 'id' in manifest and not _id(manifest['id']):raise ValueError('플러그인 id가 올바르지 않습니다.')
 tools=manifest.get('tools',[]);roles=manifest.get('roles',[])
 if not isinstance(tools,list) or not isinstance(roles,list):raise ValueError('tools와 roles는 목록이어야 합니다.')
 tool_ids=set()
 for tool in tools:
  if not isinstance(tool,dict) or not _id(tool.get('id')) or tool['id'] in tool_ids or tool.get('host_action') not in HOST_ACTIONS:raise ValueError('허용하지 않은 도구 매니페스트입니다.')
  if tool['id'] in RESERVED_TOOL_IDS:raise ValueError(f"도구 id '{tool['id']}'는 Still 실행 루프 전용이라 패키지에서 선언할 수 없습니다.")
  if tool.get('mode') != ('bounded_write' if tool['host_action'] in WRITE_ACTIONS else 'read_only'):raise ValueError('도구 모드는 host action의 안전 등급과 일치해야 합니다.')
  tool_ids.add(tool['id'])
 role_ids=set()
 for role in roles:
  permissions=role.get('permissions',[]) if isinstance(role,dict) else []
  declared_tools=role.get('tools',[]) if isinstance(role,dict) else []
  if not isinstance(role,dict) or not _id(role.get('id')) or role['id'] in role_ids or not isinstance(role.get('name',''),str) or not isinstance(role.get('instructions',''),str) or not isinstance(permissions,list) or not permissions or any(permission not in ROLE_PERMISSIONS for permission in permissions) or not isinstance(declared_tools,list) or len(set(declared_tools))!=len(declared_tools) or any(tool not in tool_ids for tool in declared_tools):raise ValueError('허용하지 않은 역할 매니페스트입니다.')
  actions={tool['host_action'] for tool in tools if tool['id'] in declared_tools}
  if 'bounded_write' not in permissions and actions & WRITE_ACTIONS:raise ValueError('읽기 전용 역할은 쓰기 도구를 선언할 수 없습니다.')
  role_ids.add(role['id'])
 _validate_skills(manifest)
 return manifest

def _validate_skills(manifest):
 """#961: an optional ``skills`` declaration: knowledge only, so its package declares no tools or roles."""
 skills=manifest.get('skills')
 if skills is None:return
 if not isinstance(skills,list) or not 1<=len(skills)<=8 or manifest.get('tools') or manifest.get('roles'):
  raise ValueError('스킬 패키지는 도구나 역할 없이 스킬 1~8개만 선언할 수 있습니다.')
 names=set()
 for skill in skills:
  if (not isinstance(skill,dict) or not isinstance(skill.get('name'),str) or not re.fullmatch(r'[a-z0-9]+(-[a-z0-9]+)*',skill['name'])
      or len(skill['name'])>64 or skill['name'] in names or not isinstance(skill.get('digest'),str) or not re.fullmatch(r'[0-9a-f]{64}',skill['digest'])
      or not isinstance(skill.get('description'),str) or not 0<len(skill['description'])<=1024
      or not isinstance(skill.get('status'),list) or not skill['status'] or any(code not in SKILL_STATUSES for code in skill['status'])):
   raise ValueError('허용하지 않은 스킬 선언입니다.')
  names.add(skill['name'])

def validate_package(manifest):
 """Validate a third-party package: the built-in ids are reserved (#604, #961)."""
 manifest=validate(manifest)
 if manifest.get('id') in RESERVED_PACKAGE_IDS or (manifest.get('skills') is not None and manifest.get('id') in RESERVED_SKILL_PACKAGE_IDS):raise ValueError(f"{manifest['id']}은 Still 기본 패키지 전용 id입니다.")
 return manifest

def load(path):return validate(json.loads(Path(path).read_text()))

def runtime_packages(manifests):
 """Resolve built-ins plus enabled declarations without granting new actions."""
 builtin=validate(BUILTIN_MANIFEST);packages=[{'id':'builtin','enabled':True,**builtin}];seen_tools={tool['id'] for tool in builtin['tools']};seen_roles={role['id'] for role in builtin['roles']}
 for package in manifests:
  manifest=validate_package(package)
  if manifest.get('enabled') is not True:continue
  package_id=manifest.get('id')
  if not package_id:raise ValueError('활성 플러그인에는 id가 필요합니다.')
  tools=manifest['tools'];roles=manifest['roles'];tool_ids={tool['id'] for tool in tools};role_ids={role['id'] for role in roles}
  if seen_tools & tool_ids or seen_roles & role_ids:raise ValueError('활성 패키지의 도구 또는 역할 id가 중복됩니다.')
  seen_tools|=tool_ids;seen_roles|=role_ids;packages.append({'id':package_id,'enabled':True,'tools':tools,'roles':roles})
 return packages
