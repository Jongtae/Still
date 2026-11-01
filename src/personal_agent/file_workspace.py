"""Scoped local file workspace: references stay read-only; results are new files."""
from .messages import msg
from pathlib import Path
import hashlib, json, os, re, sqlite3, stat, time, unicodedata, uuid
from .document_reader import MAX_FILE_BYTES, read as read_document, supported as supported_document
from . import folder_grants


def search_key(text):
    """The Python side of the store's ``unicode_search_key`` SQL function: NFKC, then casefold."""
    return unicodedata.normalize('NFKC',text).casefold()


class FileWorkspace:
    """Owner-granted TXT/MD references and a separately writable workspace."""

    #: #203: the key saved-result searches match by, also for callers outside this module.
    search_key=staticmethod(search_key)

    def __init__(self, store): self.store=store

    def configure(self, references, workspace, keep_blocked_workspace=False):
        """Replace the references and the result folder.

        ``keep_blocked_workspace`` (#779, an off-Mac Settings removal): an
        unchanged result folder the rules now block is kept verbatim with its
        stored identity.  ``validate`` would follow a symlinked parent and move
        the result-write grant to the symlink's target.
        """
        if not isinstance(references,list) or not references: raise ValueError('하나 이상의 참고 폴더를 연결하세요.')
        refs=[]; usable=[]
        current=self.status()
        previous={ref.get('path'):ref for ref in current.get('references',[]) if isinstance(ref,dict)}
        for value in references:
            # Keep a previously stored blocked entry only when the owner leaves it
            # unchanged. This lets the settings editor remove blocked entries one
            # at a time while still validating every newly added folder.
            old=previous.get(value) if isinstance(value,str) else None
            if old and folder_grants.blocked(old.get('path',''),self.store):
                if all(ref.get('path')!=old.get('path') for ref in refs): refs.append(old)
                continue
            path=folder_grants.validate(value,self.store)
            if any(ref['path']==str(path) for ref in refs): continue
            stat=path.stat(); ref={'id':hashlib.sha256(f'{stat.st_dev}:{stat.st_ino}'.encode()).hexdigest()[:24],'path':str(path)}
            refs.append(ref); usable.append(ref)
        kept=(keep_blocked_workspace and isinstance(workspace,str) and workspace==current.get('workspace')
              and folder_grants.blocked(workspace,self.store))
        target=Path(workspace) if kept else folder_grants.validate(workspace,self.store)
        # Retained blocked references still count for overlap: if one later
        # becomes available again, active() will expose it without another edit.
        if any(target==Path(ref['path']) or target.is_relative_to(ref['path']) or Path(ref['path']).is_relative_to(target) for ref in refs): raise ValueError('참고 폴더와 관리 작업공간은 겹치지 않게 연결하세요.')
        if kept: workspace_id=current.get('workspace_id')
        else: target_stat=target.stat(); workspace_id=hashlib.sha256(f'{target_stat.st_dev}:{target_stat.st_ino}'.encode()).hexdigest()[:24]
        self.store.put('file_workspace',{'references':refs,'workspace':str(target),'workspace_id':workspace_id})
        self.store.put('document_sharing',{})
        return self.status()

    def plan_grant(self, kind, value):
        """Validate one contextual grant and return (resolved path, commit).

        #505's local handoff adds exactly one folder of exactly one kind:
        ``reference`` (read-only original) or ``workspace`` (new result files).
        The shared ``folder_grants`` rules and the existing non-overlap rule
        apply, and nothing is written until ``commit`` is called.  Like
        ``configure``, a changed folder set clears document sharing, so the
        grant never carries external-AI transmission with it.
        """
        if kind not in ('reference','workspace'): raise ValueError('허용하지 않은 폴더 권한입니다.')
        path=folder_grants.validate(value,self.store)
        def overlaps(a,b): return a==b or a.is_relative_to(b) or b.is_relative_to(a)
        def validate_overlap(status):
            refs=[ref for ref in status.get('references',[]) if isinstance(ref,dict)]
            workspace=status.get('workspace')
            if kind=='reference':
                if workspace and overlaps(path,Path(workspace)): raise ValueError('참고 폴더와 관리 작업공간은 겹치지 않게 연결하세요.')
            elif any(overlaps(path,Path(ref['path'])) for ref in refs): raise ValueError('참고 폴더와 관리 작업공간은 겹치지 않게 연결하세요.')
        validate_overlap(self.status())
        def commit(*, db=None):
            current=self.store.config('file_workspace',{'references':[],'workspace':None},db=db)
            validate_overlap(current)
            stat=path.stat(); identity=hashlib.sha256(f'{stat.st_dev}:{stat.st_ino}'.encode()).hexdigest()[:24]
            refs=[ref for ref in current.get('references',[]) if isinstance(ref,dict)]
            if kind=='reference':
                if all(ref.get('path')!=str(path) for ref in refs): refs.append({'id':identity,'path':str(path)})
                updated={**current,'references':refs}
            else: updated={**current,'references':refs,'workspace':str(path),'workspace_id':identity}
            self.store.put('file_workspace',updated,db=db)
            self.store.put('document_sharing',{},db=db)
        return path,commit

    def status(self): return self.store.config('file_workspace',{'references':[],'workspace':None})

    def active(self):
        """Stored configuration minus any folder the current grant rules forbid."""
        status=self.status()
        workspace=status.get('workspace')
        usable=workspace and not folder_grants.blocked(workspace,self.store)
        return {**status,'references':[ref for ref in status.get('references',[]) if not folder_grants.blocked(ref['path'],self.store)],
                'workspace':workspace if usable else None,'workspace_id':status.get('workspace_id') if usable else None}

    def projection(self):
        """Owner-facing status with the reason any stored folder is blocked."""
        status=self.status()
        workspace=status.get('workspace')
        return {**status,'references':[{**ref,'blocked':folder_grants.blocked(ref['path'],self.store)} for ref in status.get('references',[])],
                'workspace_blocked':folder_grants.blocked(workspace,self.store) if workspace else None}

    @staticmethod
    def _safe_relative(value):
        path=Path(value)
        if path.is_absolute() or '..' in path.parts or any(part.startswith('.') for part in path.parts): raise ValueError(msg('agent_runtime.file_path_not_allowed'))
        return path

    def _reference(self, ref_id, relative):
        # Scans may read many candidate files; revalidate only the selected grant
        # for each read rather than rechecking every configured reference.
        root=next((item for item in self.status().get('references',[]) if item.get('id')==ref_id),None)
        if not root or folder_grants.blocked(root.get('path',''),self.store): raise ValueError(msg('agent_runtime.reference_folder_not_allowed'))
        base=Path(root['path']); path=self._safe_relative(relative); candidate=base/path
        if any(part.is_symlink() for part in (base, *candidate.parents) if part.exists()): raise ValueError('심볼릭 링크를 통한 참고 자료 접근은 허용하지 않습니다.')
        resolved=candidate.resolve()
        # #203 review: ``is_file()`` would hide a transient lookup error (EIO, EACCES) as a missing file;
        # only a missing one is, and any other error stays the ValueError's cause.
        try: info=resolved.stat()
        except (FileNotFoundError,NotADirectoryError): info=None
        except OSError as exc: raise ValueError('참고 자료를 지금 읽을 수 없습니다.') from exc
        if (not resolved.is_relative_to(base) or resolved.suffix.lower() not in ('.txt','.md','.pdf','.docx','.xlsx') or info is None or not stat.S_ISREG(info.st_mode) or candidate.is_symlink()): raise ValueError('참고 폴더 밖 또는 지원하지 않는 파일입니다.')
        # #182 review: bounded like a connected-folder read, before any byte is read.
        if info.st_size>MAX_FILE_BYTES: raise ValueError(msg('agent_runtime.file_size_limit'))
        return resolved

    def read(self, ref_id, relative):
        path=self._reference(ref_id,relative)
        if path.suffix.lower() in ('.txt','.md'): content=path.read_text(encoding='utf-8')
        else:
            document=read_document(path); content='\n'.join(f'[{segment["location"]}] {segment["text"]}' for segment in document.segments)
        stat=path.stat()
        return {'reference_id':ref_id,'source_id':hashlib.sha256(f'{stat.st_dev}:{stat.st_ino}'.encode()).hexdigest()[:24],
                'path':str(Path(relative)),'version':hashlib.sha256(content.encode()).hexdigest(),'content':content}

    def find_reference(self, query):
        matches=self.find_references(query, limit=2)
        if not matches: raise ValueError('연결한 참고 폴더에서 일치하는 TXT/MD 자료를 찾지 못했습니다.')
        if len(matches)>1: raise ValueError('일치하는 자료가 여러 개입니다. 더 구체적인 자료 검색어로 다시 요청하세요.')
        return matches[0]

    def find_references(self, query, limit=20):
        if not isinstance(query,str) or not 2<=len(query.strip())<=160: raise ValueError('두 글자 이상의 자료 검색어를 입력하세요.')
        phrase=query.casefold().strip(); alternatives=[item.strip() for item in phrase.split('||') if item.strip()]
        matches=[]
        for root in self.active()['references']:
            base=Path(root['path'])
            for parent,dirs,names in os.walk(base,followlinks=False):
                dirs[:]=[name for name in dirs if not name.startswith('.') and not (Path(parent)/name).is_symlink()]
                for name in names:
                    if name.startswith('.') or Path(name).suffix.lower() not in ('.txt','.md','.pdf','.docx','.xlsx') or not supported_document(Path(parent)/name): continue
                    relative=str((Path(parent)/name).relative_to(base))
                    try: source=self.read(root['id'],relative)
                    except (OSError,UnicodeError,ValueError): continue
                    haystack=(relative+'\n'+source['content']).casefold()
                    matches_query=False
                    for alternative in alternatives:
                        terms=[term for term in re.findall(r'[\w가-힣-]{2,}',alternative) if len(term)>2]
                        candidate=alternative in haystack if alternative else False
                        if not candidate and terms:
                            candidate=(any(term in haystack for term in terms) if len(terms)==1 else all(term in haystack for term in terms))
                        matches_query=matches_query or candidate
                    if matches_query:
                        matches.append(source)
                        if len(matches)>=limit: return matches
        return matches

    @staticmethod
    def _ensure_results_table(db):
        db.execute('CREATE TABLE IF NOT EXISTS file_workspace_results(id TEXT PRIMARY KEY, request_id TEXT UNIQUE, path TEXT UNIQUE, content_hash TEXT, sources TEXT, created REAL, workspace_id TEXT, state TEXT NOT NULL DEFAULT "current")')
        columns={row['name'] for row in db.execute('PRAGMA table_info(file_workspace_results)')}
        if 'state' not in columns: db.execute('ALTER TABLE file_workspace_results ADD COLUMN state TEXT NOT NULL DEFAULT "current"')
        if 'workspace_id' not in columns: db.execute('ALTER TABLE file_workspace_results ADD COLUMN workspace_id TEXT')

    def _workspace_id(self):
        value=self.active().get('workspace_id')
        if not isinstance(value,str) or not value: raise ValueError('관리 작업공간을 먼저 연결하세요.')
        return value

    @staticmethod
    def _source_metadata(source):
        return {key:source[key] for key in ('reference_id','source_id','path','version') if key in source}

    def _result_path(self, relative):
        workspace=self.active().get('workspace')
        root=Path(workspace) if workspace else None
        if root is None or not root.is_absolute() or not root.is_dir() or root.is_symlink(): raise ValueError('관리 작업공간을 먼저 연결하세요.')
        candidate=root/self._safe_relative(relative)
        if candidate.is_symlink() or not candidate.resolve().is_relative_to(root): raise ValueError('관리 작업공간 밖의 결과에는 접근할 수 없습니다.')
        return candidate

    def recover(self, deadline=None):
        """Finalize only a verified publication, and discard unpublised reservations.

        ``deadline`` (``time.monotonic``) stops before the next reservation once passed, leaving the rest
        pending for a later call; a candidate file larger than any result Still writes is not read (#182 review).
        """
        with self.store.db() as db:
            self._ensure_results_table(db)
            pending=[dict(row) for row in db.execute("SELECT * FROM file_workspace_results WHERE state='pending'")]
        # #182 review: with no usable result folder now, nothing can be verified: reservations stay pending.
        active_id=self.active().get('workspace_id')
        if not pending or not active_id: return
        for record in pending:
            if deadline is not None and time.monotonic()>deadline: break
            if record.get('workspace_id')!=active_id:
                with self.store.db() as db: db.execute("UPDATE file_workspace_results SET state='detached' WHERE id=?",(record['id'],))
                continue
            try:
                published=self._result_path(record['path'])
                complete=(published.is_file() and published.stat().st_size<=MAX_FILE_BYTES
                          and hashlib.sha256(published.read_bytes()).hexdigest()==record['content_hash'])
            except (OSError,ValueError): complete=False
            with self.store.db() as db:
                if complete: db.execute("UPDATE file_workspace_results SET state='current' WHERE id=? AND state='pending'",(record['id'],))
                else: db.execute("DELETE FROM file_workspace_results WHERE id=? AND state='pending'",(record['id'],))

    def _mark_current(self, record):
        with self.store.db() as db: db.execute("UPDATE file_workspace_results SET state='current' WHERE id=? AND state='pending'",(record['id'],))

    def save(self, request_id, title, content, sources):
        if not request_id or not content.strip(): raise ValueError('저장 요청과 내용이 필요합니다.')
        if not isinstance(sources,list) or not sources: raise ValueError('원본 출처가 필요합니다.')
        self.recover()
        safe=''.join(char if char.isalnum() or char in ' -_' else '-' for char in title).strip()[:80] or 'result'
        sources=[self._source_metadata(source) for source in sources]
        if any(set(('reference_id','source_id','path','version'))-set(source) for source in sources): raise ValueError('원본 출처 정보가 올바르지 않습니다.')
        body=content.rstrip()+'\n\n---\nSources:\n'+''.join(f'- {source["path"]} @ {source["version"]}\n' for source in sources)
        with self.store.db() as db:
            self._ensure_results_table(db); db.execute('BEGIN IMMEDIATE')
            old=db.execute('SELECT * FROM file_workspace_results WHERE request_id=?',(request_id,)).fetchone()
            if old:return dict(old)
            suffix=1
            while True:
                target=self._result_path(safe+'.md' if suffix==1 else safe+f'-{suffix}.md')
                if target.exists() or db.execute('SELECT 1 FROM file_workspace_results WHERE path=?',(target.name,)).fetchone(): suffix+=1; continue
                record={'id':str(uuid.uuid4()),'request_id':request_id,'path':target.name,'content_hash':hashlib.sha256(body.encode()).hexdigest(),'sources':json.dumps(sources),'created':time.time(),'workspace_id':self._workspace_id(),'state':'pending'}
                db.execute('INSERT INTO file_workspace_results(id,request_id,path,content_hash,sources,created,workspace_id,state) VALUES (:id,:request_id,:path,:content_hash,:sources,:created,:workspace_id,:state)',record)
                break
        temporary=target.with_name(target.name+'.tmp-'+uuid.uuid4().hex); published=False
        try:
            temporary.write_text(body,encoding='utf-8')
            while True:
                try:
                    os.link(temporary,target); published=True; temporary.unlink(); break
                except FileExistsError:
                    temporary.unlink(missing_ok=True)
                    with self.store.db() as db: db.execute("DELETE FROM file_workspace_results WHERE id=? AND state='pending'",(record['id'],))
                    return self.save(request_id,title,content,sources)
            self._mark_current(record)
            # ``inserted``: this call published the result (a concurrent same-request save returns the row it lost to).
            return {**record,'state':'current','inserted':True}
        except Exception:
            temporary.unlink(missing_ok=True)
            if published: target.unlink(missing_ok=True)
            with self.store.db() as db: db.execute("DELETE FROM file_workspace_results WHERE id=? AND state='pending'",(record['id'],))
            raise

    #: ``_fresh``'s answer when its deadline passed before every source was checked (#182 review).
    UNCHECKED=None

    #: ``_fresh``'s answer when a cited reference folder is not usable now (removed from the grant, blocked,
    #: an unmounted drive): nothing was observed to differ, so the record keeps its state (#182 review).
    UNAVAILABLE='unavailable'

    def _fresh(self, record, deadline=None):
        """Whether every cited source is unchanged; ``UNCHECKED`` when ``deadline`` passed between sources,
        ``UNAVAILABLE`` when a cited reference folder cannot be read now."""
        try: sources=json.loads(record['sources'])
        except (TypeError,json.JSONDecodeError): return False
        try:
            if not sources: return False
            usable={ref.get('id'):ref.get('path') for ref in self.active().get('references') or () if isinstance(ref,dict)}
            for source in sources:
                if deadline is not None and time.monotonic()>deadline: return self.UNCHECKED
                folder=usable.get(source.get('reference_id'))
                if not folder or not Path(folder).is_dir(): return self.UNAVAILABLE
                current=self.read(source['reference_id'],source['path'])
                if current['version']!=source['version'] or current['source_id']!=source['source_id']: return False
            return True
        except ValueError as exc:
            # The document reader wraps an I/O failure as ValueError (``document_reader``); its cause decides (#203 review).
            return self._read_failure(exc.__cause__) if isinstance(exc.__cause__,OSError) else False
        # A source gone, outside the folder now, or no longer text: it differs (#203).
        except (KeyError,FileNotFoundError,UnicodeError): return False
        except OSError as exc: return self._read_failure(exc)

    def _read_failure(self, error):
        """A missing source differs; any other read error (permission, I/O) observed no difference (#203)."""
        return False if isinstance(error,FileNotFoundError) else self.UNAVAILABLE

    def current_results(self, needle=None, latest=False, path=None, deadline=None, terms=None):
        """Fresh saved results, newest first: each one's sources still match the originals.

        A result whose workspace changed is marked ``detached``, one whose
        sources changed ``stale``; neither is returned.  ``needle`` (casefolded)
        keeps only results whose name or content contains it; ``path`` checks
        only the one result of that name.  ``deadline`` (``time.monotonic``)
        stops checking once passed, so one call stays bounded (#182 review).
        ``terms``: results whose name holds one of them are checked first, beyond the
        newest-100 window, so an older result is found by its name (#182 review).
        """
        self.recover(deadline)
        # Results exist but no result folder is usable: the caller's setup case, never an empty answer.
        if self.has_file_results(): self._workspace_id()
        with self.store.db() as db:
            self._ensure_results_table(db)
            records=[dict(row) for row in (db.execute('SELECT * FROM file_workspace_results WHERE path=?',(path,)) if path is not None
                                           else db.execute("SELECT * FROM file_workspace_results WHERE state IN ('current','stale') ORDER BY created DESC LIMIT 101"))]
            terms=[search_key(term) for term in (terms or ()) if isinstance(term,str) and term] if path is None else []
            named=[dict(row) for row in db.execute(
                "SELECT * FROM file_workspace_results WHERE state IN ('current','stale') AND ("+' OR '.join(['instr(unicode_search_key(path),?)>0']*len(terms))+')'
                +' ORDER BY created DESC LIMIT 100',terms)] if terms else []
        results=[]
        # Whether the deadline, or the 100-record cap, stopped the checks before every record was seen (#182 review).
        self.stopped_early=len(records)>100
        seen=set()
        records=[record for record in named+records[:100] if not (record['id'] in seen or seen.add(record['id']))]
        for record in records:
            if deadline is not None and time.monotonic()>deadline:
                self.stopped_early=True; break
            if record.get('workspace_id')!=self._workspace_id():
                with self.store.db() as db: db.execute("UPDATE file_workspace_results SET state='detached' WHERE id=?",(record['id'],))
                continue
            fresh=self._fresh(record,deadline)
            if fresh is self.UNCHECKED:
                # Cut short: neither current nor stale, and the search says it is incomplete.
                self.stopped_early=True; break
            if fresh==self.UNAVAILABLE:
                # A cited reference folder is unusable now: left as it is, and the search is incomplete.
                self.stopped_early=True; continue
            with self.store.db() as db: db.execute('UPDATE file_workspace_results SET state=? WHERE id=?',('current' if fresh else 'stale',record['id']))
            if not fresh: continue
            try:
                published=self._result_path(record['path'])
                # #182 review: bounded before it is read, and only the exact text Still published counts:
                # a result file edited (or replaced) since is not this result any more.
                if published.stat().st_size>MAX_FILE_BYTES: raise ValueError('oversized result file')
                raw=published.read_bytes()
                if hashlib.sha256(raw).hexdigest()!=record.get('content_hash'): raise ValueError('edited result file')
                content=raw.decode('utf-8')
            except (OSError,UnicodeError,ValueError):
                # #182 review: deleted, unreadable, oversized or edited: not a current result any more.
                with self.store.db() as db: db.execute("UPDATE file_workspace_results SET state='stale' WHERE id=?",(record['id'],))
                continue
            if latest or needle is None or needle in search_key(record['path']+'\n'+content): results.append({'id':record['id'],'path':record['path'],'content':content,'sources':json.loads(record['sources'])})
            if latest and results: break
        return results

    def search(self, query):
        latest=isinstance(query,str) and query.strip() in ('*','__latest__')
        if not latest and (not isinstance(query,str) or not 2<=len(query.strip())<=160): raise ValueError('두 글자 이상의 결과 검색어를 입력하세요.')
        return self.current_results(search_key(query.strip()),latest)

    #: ``intact``'s answer when it could not tell: the result folder is unusable now, or the source check
    #: ran out of time.  The record keeps its state (#182 review).
    UNKNOWN=None

    def intact(self, record):
        """Whether a current record is still a current result: its file exactly what Still published and its
        cited sources unchanged (#182 review).  ``False`` marks it stale; ``UNKNOWN`` changes nothing."""
        if record.get('workspace_id')!=self.active().get('workspace_id') or not self.active().get('workspace_id'):
            return self.UNKNOWN
        try:
            published=self._result_path(record['path'])
            ok=(published.stat().st_size<=MAX_FILE_BYTES
                and hashlib.sha256(published.read_bytes()).hexdigest()==record.get('content_hash'))
        except (OSError,ValueError): ok=False
        if ok:
            fresh=self._fresh(record,time.monotonic()+5)
            if fresh is self.UNCHECKED or fresh==self.UNAVAILABLE: return self.UNKNOWN
            ok=fresh is True
        if not ok:
            with self.store.db() as db: db.execute("UPDATE file_workspace_results SET state='stale' WHERE id=?",(record['id'],))
        return ok

    def result_for(self, request_id):
        """The result record one request (Work) already saved, or None.

        A record of a result folder other than the active one is marked ``detached`` first (#182 review),
        as ``current_results`` would, so it never reads as this folder's result.
        """
        with self.store.db() as db:
            self._ensure_results_table(db)
            row=db.execute('SELECT * FROM file_workspace_results WHERE request_id=?',(request_id,)).fetchone()
        record=dict(row) if row else None
        active_id=self.active().get('workspace_id')
        # Only a usable, different result folder detaches it; an unavailable one (an unmounted drive) leaves it.
        if record and record.get('state')=='current' and active_id and record.get('workspace_id')!=active_id:
            with self.store.db() as db: db.execute("UPDATE file_workspace_results SET state='detached' WHERE id=?",(record['id'],))
            record['state']='detached'
        return record

    def has_file_results(self):
        """Whether a published result-file record exists (the result folder may be unavailable)."""
        with self.store.db() as db:
            self._ensure_results_table(db)
            return bool(db.execute("SELECT 1 FROM file_workspace_results WHERE state IN ('current','pending') LIMIT 1").fetchone())

    def has_results(self):
        """Whether any saved result record exists, checking no result file (``list_roots``).

        An interrupted save is settled first (bounded), so a published result left ``pending`` counts (#182 review).
        """
        self.recover(time.monotonic()+2)
        with self.store.db() as db:
            self._ensure_results_table(db)
            if db.execute("SELECT 1 FROM file_workspace_results WHERE state IN ('current','pending') LIMIT 1").fetchone(): return True
            tables={row['name'] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            return 'workspace_results' in tables and bool(db.execute('SELECT 1 FROM workspace_results LIMIT 1').fetchone())

    def project_results(self, item_id=None, terms=None, deadline=None):
        """Answers the owner saved into a project (the older ``workspace_results``), newest first.

        Still's own records, read-only: ``{id, title, content, created}``.  ``item_id`` reads that one
        directly; otherwise the newest 100, with ``project_capped`` set when more exist (#182 review).
        ``deadline`` (``time.monotonic``) interrupts the query once passed (``project_capped`` then too).
        """
        self.project_capped=False
        with self.store.db() as db:
            tables={row['name'] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if not {'workspace_results','workspaces'}<=tables: return []
            query='SELECT r.id,w.title,r.content,r.created FROM workspace_results r JOIN workspaces w ON w.id=r.workspace_id'
            if item_id is not None:
                return [dict(row) for row in db.execute(query+' WHERE r.id=?',(item_id,))]
            # #182 review: a search filters before the cap, so an older matching answer is reachable.
            # #203: Unicode case folding (the store's ``unicode_search_key``), not SQLite's ASCII-only ``lower()``.
            terms=[search_key(term) for term in (terms or ()) if isinstance(term,str) and term]
            where=(' WHERE '+' OR '.join(["instr(unicode_search_key(w.title||' '||r.content),?)>0"]*len(terms))) if terms else ''
            # #182 review: a full-text scan of a long history is interrupted at the caller's deadline.
            if deadline is not None:
                if time.monotonic()>deadline:
                    self.project_capped=True
                    return []
                db.set_progress_handler(lambda: 1 if time.monotonic()>deadline else 0, 200)
            try:
                rows=[dict(row) for row in db.execute(query+where+' ORDER BY r.created DESC LIMIT 101',terms)]
            except sqlite3.OperationalError:
                if deadline is None or time.monotonic()<=deadline: raise
                self.project_capped=True
                return []
            finally:
                if deadline is not None: db.set_progress_handler(None, 0)
        self.project_capped=len(rows)>100
        return rows[:100]
