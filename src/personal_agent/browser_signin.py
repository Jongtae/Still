"""Browser sign-in lifecycle and exact step approvals extracted from AgentService (#21).

AgentService remains the composition root and owns all mutable state. Each
entry point forwards here; calls within the moved code still go through the
service so existing callers and method patches retain their behavior.
Shared constants remain in quickstart_service, as in telegram_surface.
"""

from . import work_status as ws
from . import remote_login
from .agent_runtime import ToolError, not_run
from .browser_session import ascii_host, login_window_url, page_reference, registrable_domain
from .browser_jar import unexpired
from .messages import msg
from .providers import ProviderError
from urllib.parse import urlsplit
import hashlib
import hmac
import json
import secrets
import threading
import time

from .quickstart_service import (BROWSER_APPROVAL_PROMPT,
                                 BROWSER_LEGACY_KEY,
                                 BROWSER_LOGINS_KEY,
                                 BROWSER_LOGIN_ADDRESS_LINE,
                                 BROWSER_LOGIN_CLOSE_SECONDS,
                                 BROWSER_LOGIN_MOVED_LINE,
                                 BROWSER_LOGIN_NO_SESSION_LINE,
                                 BROWSER_LOGIN_OFFERED_TEXT,
                                 BROWSER_LOGIN_OTHER_PENDING_TEXT,
                                 BROWSER_LOGIN_PAGE_OFFERED_TEXT,
                                 BROWSER_LOGIN_PENDING_STATES,
                                 BROWSER_LOGIN_PHONE_FAILED_PROMPT,
                                 BROWSER_LOGIN_PHONE_PREPARING_PROMPT,
                                 BROWSER_LOGIN_PHONE_PROMPT,
                                 BROWSER_LOGIN_PROMPT,
                                 BROWSER_LOGIN_REASK_STATES,
                                 BROWSER_LOGIN_REOPENS,
                                 BROWSER_LOGIN_REOPEN_FAILED_TEXT,
                                 BROWSER_LOGIN_REOPEN_LABEL,
                                 BROWSER_LOGIN_REOPEN_TEXT,
                                 BROWSER_LOGIN_RESULT_TEXT,
                                 BROWSER_LOGIN_SECONDS,
                                 BROWSER_OWNER_SIGNINS_KEY,
                                 BROWSER_PROVIDERS_KEY,
                                 BROWSER_PROVIDER_SIGNIN_KEY,
                                 BROWSER_REQUESTS_KEY,
                                 LOG,
                                 PAYMENT_APPROVAL_SECONDS,
                                 UNATTENDED_APPROVAL_TEXT,
                                 UNKNOWN_EFFECT_RETRY_REFUSAL,
                                 request_prompt_parts)


class BrowserSignin:
    """Sign-in and approval operations over one owning service's existing state."""

    def __init__(self, service):
        self.service = service

    def browser_status(self):
        service=self.service
        status=service.browser_profile.status()
        status['legacy_profile_removed_at']=service.store.config(BROWSER_LEGACY_KEY)
        status['pending_steps']=[{'work_id':row['work_id'],'action':row['action'],'label':row.get('label',''),
                                  'host':row.get('host',''),'state':row.get('state'),'requested_at':row.get('requested_at'),'digest':row.get('offer_digest')}
                                 for row in service.browser_step_requests()]
        # #709: logins waiting for the owner (host and times only); decided at /api/browser/login/decision.
        # #749: the Settings login window's observed outcome after its request answered ``opening``.
        settings_login=service._settings_login or {}
        status['settings_login']={'state':settings_login.get('state'),'at':settings_login.get('at'),
                                  'saved':settings_login.get('saved'),'sites':settings_login.get('sites') or []} if settings_login else None
        # #488: whether each stored site still holds the owner's login-window sign-in (``_owner_signed_in``, checked
        # off the poll), and whether a keep-alive was since refused.  ``login_url`` reopens the window for that site.
        signins=service.store.config(BROWSER_OWNER_SIGNINS_KEY,{})
        signins=signins if isinstance(signins,dict) else {}
        paused=service.store.config(service.KEEPALIVE_PAUSED_KEY,{})
        paused=paused if isinstance(paused,dict) else {}
        signed={}
        for session in status.get('sessions') or []:
            record=signins.get(session.get('site'))
            at=record.get('at') if isinstance(record,dict) and record.get('marks') else None
            if isinstance(at,(int,float)) and at>0:
                signed[session['site']]=(session.get('cookies'),session.get('last_used'),at,tuple(map(str,record['marks'])))
        current=service._owner_signin_view(signed)
        for session in status.get('sessions') or []:
            site=session.get('site')
            record=signins.get(site)
            if site in signed:
                at=signed[site][2]
                held=current.get(site,'checking')
                refused=isinstance(paused.get(site),(int,float)) and paused[site]>=at
                state='checking' if held=='checking' else 'unknown' if held is None else 'expired' if not held else 'refresh_refused' if refused else 'signed_in'
                session.update(sign_in=state,signed_in_at=at)
            else:
                session['sign_in']='none'
            host=ascii_host(record.get('host')) if isinstance(record,dict) and record.get('host') else None
            session['login_url']=f"https://{host or site}/"
        status['pending_logins']=[{'work_id':row['work_id'],'host':row.get('host',''),'site':row.get('site'),
                                   'landed_host':row.get('landed_host'),'landed_site':row.get('landed_site'),
                                   'stored_session':row.get('stored_session'),'offered_at':row.get('offered_at'),
                                   'deadline':row.get('deadline')}
                                  for row in service.browser_login_requests().values()
                                  if isinstance(row,dict) and row.get('state')=='offered']
        return status

    def _owner_signin_view(self, signed):
        """``{site: True | False | None}`` of ``_owner_signed_in`` for the sites in ``signed`` (#488), never blocking.

        ``signed`` maps each site with a login-window sign-in record to a key of
        its stored row and record.  A result is kept while that key is unchanged
        and for ``SIGNIN_VIEW_SECONDS`` (a cookie expires with time alone); a
        site not checked for its current key is absent until the off-thread
        check finishes, so the poll never waits on the Keychain.
        """
        service=self.service
        now=time.time()
        with service.lock:
            view=service._signin_view
            fresh={site:entry[1] for site,entry in view.items() if site in signed and entry[0]==signed[site]}
            due=len(fresh)<len(signed) or any(now-view[site][2]>=service.SIGNIN_VIEW_SECONDS for site in fresh)
            if not due or service._signin_view_refreshing:return fresh
            service._signin_view_refreshing=True
        def check():
            results={}
            try:
                for site,key in signed.items():
                    try:held=service._owner_signed_in(site)
                    except Exception:held=None
                    results[site]=(key,held,time.time())
            finally:
                with service.lock:
                    service._signin_view=results
                    service._signin_view_refreshing=False
        threading.Thread(target=check,daemon=True,name='agentos-signin-view').start()
        return fresh

    def open_browser_for_login(self, body):
        """Show the embedded engine's login window; Still types nothing in it (#680).

        #749: never waits for the window (the request answers ``opening``);
        a close that saved evidence of a sign-in records the site(s) the owner
        signed in to, the requested one or the one the window landed on, by
        the in-flow rule (``_signed_in_sites``, #765).
        """
        service=self.service
        url=(body or {}).get('url') if isinstance(body,dict) else None
        if not isinstance(url,str) or not url.strip():raise ValueError(msg('service.enter_the_site_address_to_log'))
        try:
            host=urlsplit(url.strip()).hostname
        except ValueError:
            host=None
        # #939: ``phone``: once the window shows, send the paired owner a one-time link that drives it.
        phone=isinstance(body,dict) and body.get('phone') is True
        if phone:
            cfg=service.store.config('telegram',{})
            if not (cfg.get('enabled') and isinstance(cfg.get('user_id'),int)):
                raise ValueError(msg('quickstart_service.phone_login_no_destination'))
            if service.remote_login_session() is not None:raise ValueError(remote_login.BUSY_TEXT)
        row={'host':host,'cookies_before':service._login_cookie_marks({'host':host}) if host else None}
        # What Settings shows after answering ``opening``: the window's observed outcome (in memory only).
        token=secrets.token_hex(8)
        def mark(state,**outcome):
            with service.lock:
                if state=='opening' or (service._settings_login or {}).get('token')==token:
                    service._settings_login={'token':token,'state':state,'at':time.time(),**outcome}
        def opened(window,landed=None):
            # #765: the same baselines as the in-flow window (after the landing's own cookies were saved).
            row.update(service._login_window_baselines(window,host,landed))
            mark('opened')
            if phone:
                site=row.get('landed_site') or registrable_domain(host) or ''
                chat=service.store.config('telegram',{}).get('user_id')
                try:service.start_remote_login(window,site,lambda reason:service.browser_profile.close_login_window(window,timeout=0),chat_id=chat)
                except remote_login.RemoteLoginError as exc:
                    # Review P2-1: the window stays open for the owner at the Mac; the owner is told why.
                    service._notify_owner(f'telegram:{chat}',str(exc))
        def closed(window,reason,saved):
            failed=reason=='failed' and (service._settings_login or {}).get('state')=='opening'
            signed=service._signed_in_sites({**row,'window':window}) if saved else []
            started=time.time()
            if signed:service._record_owner_signins(signed,authorizations=service.browser_profile.login_window_authorizations(window))
            # #488: Settings tells the owner what the close saved: the site(s) whose sign-in was just recorded.
            signins=service.store.config(BROWSER_OWNER_SIGNINS_KEY,{}) if signed else {}
            sites=sorted(site for site,record in (signins.items() if isinstance(signins,dict) else ())
                         if isinstance(record,dict) and float(record.get('at') or 0)>=started)
            mark('failed' if failed else 'closed',saved=bool(saved),sites=sites)
        mark('opening')
        result=service.browser_profile.open_for_login(url.strip(),on_opened=opened,on_closed=closed)
        if not isinstance(result,dict) or result.get('state')!='opening':
            mark((result or {}).get('state') or 'failed')
        return result

    def delete_browser_sessions(self, body):
        """Delete one site's saved sign-in cookies, or all of them (#680).

        Deletion removes the site from the encrypted jar and from a running
        worker; ``all`` also deletes the jar's Keychain key.  Returns names
        only, never cookie values.
        """
        service=self.service
        body=body if isinstance(body,dict) else {}
        if body.get('all') is True:
            result=service.browser_profile.delete_all()
            service.store.put(BROWSER_LEGACY_KEY,None)
            service.store.put(BROWSER_PROVIDERS_KEY,{})   # #636: no sign-in left, no provider record either
            return result
        site=body.get('site')
        if not isinstance(site,str) or not site.strip():raise ValueError(msg('quickstart_service.specify_site_to_delete'))
        result=service.browser_profile.delete_site(site)
        with service.lock:
            records=service.store.config(BROWSER_PROVIDERS_KEY,{})
            if isinstance(records,dict) and registrable_domain(site.strip()) in records:
                records.pop(registrable_domain(site.strip()))
                service.store.put(BROWSER_PROVIDERS_KEY,records)
        return result

    def browser_step_requests(self):
        service=self.service
        rows=service.store.config(BROWSER_REQUESTS_KEY,{})
        if not isinstance(rows,dict):return []
        return sorted((row for row in rows.values() if isinstance(row,dict) and row.get('work_id')),
                      key=lambda row:row.get('requested_at') or 0)

    def _browser_request(self, work_id):
        service=self.service
        rows=service.store.config(BROWSER_REQUESTS_KEY,{})
        row=rows.get(work_id) if isinstance(rows,dict) and isinstance(work_id,str) else None
        return row if isinstance(row,dict) else None

    def _put_browser_request(self, work_id, row, *, expected=...):
        service=self.service
        with service.lock:
            with service.store.db() as db:
                db.execute('BEGIN IMMEDIATE')
                found=db.execute('SELECT value FROM config WHERE key=?',(BROWSER_REQUESTS_KEY,)).fetchone()
                rows=json.loads(found['value']) if found else {}
                rows=rows if isinstance(rows,dict) else {}
                if expected is not ... and rows.get(work_id)!=expected:return False
                if row is None:rows.pop(work_id,None)
                else:rows[work_id]=row
                db.execute('INSERT INTO config VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',
                           (BROWSER_REQUESTS_KEY,json.dumps(rows)))
                return True

    def _browser_step_keys(self, binding):
        """Keyed digests of one step binding: what the approval row and request row hold.

        The binding's own digests are plain SHA-256 of short values (a card
        number is guessable from its hash), so only an HMAC under this
        installation's secret is ever stored.
        """
        service=self.service
        from .browser_session import keyed_step_binding
        return keyed_step_binding(service.store,binding)

    def _consume_browser_step(self, job, binding):
        """Spend an approval the owner issued for exactly this step of this Work, once.

        Different typed text, another target or a changed form (price,
        quantity, terms) is a different binding: the issued approval is left
        unspent and the step asks again.
        """
        service=self.service
        row=service._browser_request(job['id'])
        if row and row.get('state')=='issued' and service.unattended_work(job):
            raise not_run(ToolError(UNATTENDED_APPROVAL_TEXT,'unattended_run'))
        keys=service._browser_step_keys(binding)
        surface=service.surface_claiming(job)
        if (surface is not None and not surface.owns(job) or
                row and row.get('generation')!=service._owner_generation(service.issuer_owner_id(job))):return False
        if not row or row.get('state')!='issued' or not hmac.compare_digest(str(row.get('digest','')),keys['digest']):return False
        try:
            service.store.consume_browser_step_approval(service.issuer_owner_id(job),job['id'],binding['action'],
                                                     keys['page_digest'],keys['target_digest'],keys['step_digest'],row.get('token'))
        except ValueError:
            return False
        finally:
            # Spent or unusable, the row is gone: a token never outlives its one use.
            service._put_browser_request(job['id'],None,expected=row)
        return True

    def _request_browser_step(self, job, binding, description):
        """Record the refused step for the owner's approval surfaces (web Settings, Telegram).

        #619: a scheduled run (no owner present) records nothing and raises ``unattended_run``.
        #662: every caller tries payment autonomy before asking, so a step it kept out of the
        mode (a card, password or one-time-code field) still gets the owner's approval request here.
        """
        service=self.service
        if service.unattended_work(job):
            raise not_run(ToolError(UNATTENDED_APPROVAL_TEXT,'unattended_run'))  # #618: Still refused it, ran: false
        # #1456: a code run's request is approved as written (method, path, body), so its label is shown whole.
        from .quickstart_store import REQUEST_APPROVAL_ACTIONS
        label=(str(description or '') if binding.get('action') in REQUEST_APPROVAL_ACTIONS
               else service._redact_reason(' '.join(str(description or '').split()))[:120])
        row={'work_id':job['id'],'action':binding['action'],'label':label,**service._browser_step_keys(binding),
             'state':'requested','requested_at':time.time(),
             'offer_digest':secrets.token_hex(32),
             'generation':service._owner_generation(service.issuer_owner_id(job))}
        current=service._browser_request(job['id'])
        if (current and current.get('state')=='requested' and current.get('digest')==row['digest']
                and current.get('generation')==row['generation'] and current.get('offer_digest')
                and time.time()<=float(current.get('requested_at',0))+PAYMENT_APPROVAL_SECONDS):return
        if service._put_browser_request(job['id'],row,expected=current):
            # #1468: a request that replaced an earlier one in this Work rebinds the Work's one prompt.
            if not service.store.rebind_notification(job['id'],'browser_approval_needed',row['offer_digest']):
                service.queue_notification(job,'browser_approval_needed',fingerprint=row['offer_digest'])

    def step_approval_pending(self, job_id):
        """Whether this Work waits for the owner's decision on a step it asked to approve (#1468)."""
        service=self.service
        row=service._browser_request(job_id) or {}
        return row.get('state')=='requested' and time.time()<=float(row.get('requested_at',0))+PAYMENT_APPROVAL_SECONDS

    def browser_step_prompt(self, work_id):
        service=self.service
        row=service._browser_request(work_id) or {}
        label=row.get('label') or msg('quickstart_service.browser_step_label')
        from .quickstart_store import REQUEST_APPROVAL_ACTIONS
        if row.get('action') in REQUEST_APPROVAL_ACTIONS:
            # #1470: a request is shown as written (the AI's description first, when given).
            title,summary,rest,check,exact=request_prompt_parts(label)
            lines=[title]+([summary+('\n'+'\n'.join(rest) if rest else '')] if summary else [])+([check] if check else [])
            return '\n\n'.join(lines+([f"{msg('notice.request_raw')}\n{exact}"] if exact else [])+[msg('notice.approval_answer_hint')])
        return msg('quickstart_service.browser_step_prompt', prompt=BROWSER_APPROVAL_PROMPT, label=label)+"\n\n"+msg('notice.approval_answer_hint')

    def browser_step_prompt_html(self, work_id):
        """#1472: the request prompt with the exact request folded (Telegram expandable quote), or None.

        The AI's description is read first; the exact request it does not replace stays one tap away.
        """
        service=self.service
        from html import escape
        from .quickstart_store import REQUEST_APPROVAL_ACTIONS
        row=service._browser_request(work_id) or {}
        label=row.get('label') or ''
        if row.get('action') not in REQUEST_APPROVAL_ACTIONS or not label:return None
        # Owner choice 2026-10-14 (mockup C): title, the summary's key line in bold, Still's check of its
        # numbers against the exact request, the exact request folded, a one-line answer hint.
        title,summary,rest,check,exact=request_prompt_parts(label)
        parts=[f'<b>{escape(title)}</b>']
        if summary:parts.append(f'<b>{escape(summary)}</b>'+''.join('\n'+escape(line) for line in rest))
        if check:parts.append(escape(check))
        if exact:parts.append(f"<blockquote expandable>{escape(msg('notice.request_raw'))}\n{escape(exact)}</blockquote>")
        parts.append(escape(msg('notice.approval_answer_hint')))
        return '\n\n'.join(parts)

    def browser_step_decision(self, body):
        """The owner's web decision on one pending browser step."""
        service=self.service
        if not isinstance(body,dict) or not isinstance(body.get('work_id'),str) or body.get('decision') not in ('approve','deny'):
            raise ValueError(msg('quickstart_service.check_browser_approval'))
        pending=service._browser_request(body['work_id'])
        if not pending or pending.get('state')!='requested':raise ValueError(msg('quickstart_service.no_browser_approval'))
        if not isinstance(body.get('digest'),str) or not hmac.compare_digest(body['digest'],str(pending.get('offer_digest',''))):
            raise ValueError(msg('quickstart_service.browser_approval_changed'))
        return service._decide_browser_step(body['work_id'],body['decision']=='approve',body['digest'])

    def open_step_notification(self, notification):
        """``(notification, request)`` when this sent approval prompt's request still waits for it, else None (#1472).

        Exact: the request is still ``requested``, offered with this prompt's digest, and not expired.
        """
        service=self.service
        if not notification or notification.get('kind')!='browser_approval_needed' or notification.get('state')!='sent':
            return None
        notification=dict(notification)
        request=service._browser_request(notification['job_id']) or {}
        if (request.get('state')!='requested' or request.get('offer_digest')!=notification.get('fingerprint')
                or time.time()>float(request.get('requested_at',0))+PAYMENT_APPROVAL_SECONDS):return None
        return notification,request

    def answer_step_approval(self, notification, approve):
        """The owner's judged answer to an approval prompt settles exactly that request (#1472, #172).

        Owner choice 2026-10-13: approvals are answered like a person would, not with a button.  Which
        prompt an act answers, and whether plainly yes or no, is the one owner-act judgment (#172);
        this settles only what it named, through the exact offer digest.  Returns what the owner is told.
        """
        service=self.service
        decision=service._decide_browser_step(notification['job_id'],approve,notification.get('fingerprint'))
        service.store.update_notification(notification['id'],'browser_approved' if decision.get('approved') else 'browser_denied')
        LOG.info('step approval by owner answer work=%s approve=%s approved=%s resumed=%s',notification['job_id'],
                 approve,bool(decision.get('approved')),bool(decision.get('resumed')))
        return service.step_approval_result_text(notification['job_id'],decision,approve)

    def step_approval_result_text(self, work_id, decision, approve):
        """What really happened after the owner answered an approval (#1472): never "continuing" when it did not."""
        service=self.service
        if not approve:return msg('notice.step_declined')
        if decision.get('approved') and decision.get('resumed'):return msg('notice.step_approved_resuming')
        if decision.get('approved'):
            cause=((service.store.job(work_id) or {}).get('owner_cause') or '').strip()
            return msg('notice.step_approved_not_resumed')+(f' {cause}' if cause else '')
        return msg('notice.step_approval_stale')

    def _decide_browser_step(self, work_id, approve, expected_digest=None):
        """Settle exactly the offered action; duplicate callbacks never erase an issued token."""
        service=self.service
        with service.lock:
            job=service.store.job(work_id);row=service._browser_request(work_id)
            result={'approved':False,'resumed':False,'work_id':work_id}
            if not job or not row or row.get('state')!='requested':return result
            if expected_digest is not None and not hmac.compare_digest(expected_digest,str(row.get('offer_digest',''))):return result
            current=(not service.work_stopped(work_id) and time.time()<=float(row.get('requested_at',0))+PAYMENT_APPROVAL_SECONDS
                     and row.get('generation',service._owner_generation(service.issuer_owner_id(job)))==service._owner_generation(service.issuer_owner_id(job)))
            if not approve or not current:
                with service.store.db() as db:
                    db.execute('BEGIN IMMEDIATE')
                    refusal=service.preparations.run_refusal(db,work_id,service.preparations.clock())
                    if refusal:service.preparations.refuse_run(db,work_id,service.preparations.clock(),refusal)
                service._put_browser_request(work_id,None,expected=row)
                if not service.photo_work_has_pending_resume(work_id):service.store.remove_telegram_photo(work_id)
                return result
            result={'approved':False,'resumed':False,'work_id':work_id}
            # #662 (Codex P2 on #668): the owner's approval issues the exact token in either mode; a
            # step the mode leaves to the owner (a card, password or one-time-code field) needs it.
            def commit(db,approval):
                config=db.execute('SELECT value FROM config WHERE key=?',(BROWSER_REQUESTS_KEY,)).fetchone()
                requests=json.loads(config['value']) if config else {}
                actual=requests.get(work_id) or {}
                if actual.get('state')!='requested' or actual.get('offer_digest')!=row.get('offer_digest'):return False
                if (service.work_stopped(work_id) or time.time()>float(actual.get('requested_at',0))+PAYMENT_APPROVAL_SECONDS or
                        actual.get('generation')!=service._owner_generation(service.issuer_owner_id(job))):return False
                current_job=db.execute('SELECT status FROM jobs WHERE id=?',(work_id,)).fetchone()
                if not current_job or not ws.is_resumable_action_decision(current_job['status']):return False
                issued={**row,'state':'issued','token':approval['approval_token'],'issued_at':time.time()}
                requests[work_id]=issued
                db.execute('INSERT INTO config VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',
                           (BROWSER_REQUESTS_KEY,json.dumps(requests)))
                result['resumed']=service._queue_action_continuation(db,job,issued)
                result['approved']=True
                return True
            try:
                service.store.issue_browser_step_approval(service.issuer_owner_id(job),work_id,row['action'],
                    row['page_digest'],row['target_digest'],row['step_digest'],on_issue=commit)
            except ValueError:
                return result
            return result

    def browser_login_requests(self, *, db=None):
        service=self.service
        rows=service.store.config(BROWSER_LOGINS_KEY,{},db=db)
        return rows if isinstance(rows,dict) else {}

    def _browser_login(self, work_id):
        service=self.service
        row=service.browser_login_requests().get(work_id) if isinstance(work_id,str) else None
        return row if isinstance(row,dict) else None

    def _put_browser_login(self, work_id, row, *, db=None):
        service=self.service
        with service.lock:
            cached=service._browser_login_window_urls.get(work_id)
            # #340: keep an unanswered expiry only while it may be reopened; pruning removes it too.
            if row is None or row.get('state') in ('resumed','skipped','not_logged_in','resolved') or (cached and cached[0]!=row.get('nonce')) \
                    or (row.get('state')=='unavailable' and not row.get('reopens')) \
                    or (row.get('state')=='expired' and (row.get('cause')=='close_failed' or int(row.get('reopens') or 0)>=BROWSER_LOGIN_REOPENS)):
                service._browser_login_window_urls.pop(work_id,None)
            rows=service.browser_login_requests(db=db)
            if row is None:rows.pop(work_id,None)
            else:rows[work_id]=row
            service.store.put(BROWSER_LOGINS_KEY,rows,db=db)

    def _browser_login_window_url(self, work_id, row):
        """#340: use memory only for this exact request and the stored scheme/host; caller holds lock."""
        service=self.service
        cached=service._browser_login_window_urls.get(work_id)
        if cached and cached[0]==row.get('nonce'):
            try:
                target,reference=urlsplit(cached[1]),urlsplit(row['url'])
                if (target.scheme,target.hostname,target.port)==(reference.scheme,reference.hostname,reference.port):return cached[1]
            except ValueError:pass
        return row['url']

    def _request_browser_login(self, job, url, explicit=False, *, window_url=None):
        """Record that this Work needs the owner's login at ``url``; returns the model-facing text or None.

        A Work is asked again only after its last login expired or its window
        could not open; one that is waiting, was resumed or was skipped is not.
        Nothing is recorded when this computer cannot show the window.
        ``explicit`` (#978): the model asked for the sign-in itself
        (``browser_sign_in``); the row is then offered however the Work ends.
        """
        service=self.service
        try:
            parts=urlsplit(str(url or ''))
            host=parts.hostname
        except ValueError:
            return None
        if parts.scheme not in ('http','https') or not host or not service.browser_profile.available():
            return None
        target=page_reference(url) if '[가림]' not in url else page_reference(f'{parts.scheme}://{parts.netloc}/')
        with service.lock:
            existing=service._browser_login(job['id'])
            if existing is not None and existing.get('state') not in BROWSER_LOGIN_REASK_STATES:
                if explicit and existing.get('state')=='requested' and not existing.get('explicit'):
                    service._put_browser_login(job['id'],{**existing,'explicit':True})
                # #1006: a login still to be shown for the same site is the same request: the model reads the
                # same in-flow text, never the Settings pointer.  Another site waits for its own request.
                if existing.get('state') not in BROWSER_LOGIN_PENDING_STATES:return None
                same=registrable_domain(existing.get('host'))==registrable_domain(host)
                return (BROWSER_LOGIN_OFFERED_TEXT if explicit else BROWSER_LOGIN_PAGE_OFFERED_TEXT) if same \
                    else BROWSER_LOGIN_OTHER_PENDING_TEXT
            nonce=secrets.token_hex(16)
            service._put_browser_login(job['id'],{'work_id':job['id'],'url':target,'host':host,
                                               'state':'requested','requested_at':time.time(),
                                               'nonce':nonce,'explicit':bool(explicit)})
            # #340: redacted references cannot vouch for an unmediated address.
            if window_url and '[가림]' not in url:
                service._browser_login_window_urls[job['id']]=(nonce,login_window_url(window_url))
        return BROWSER_LOGIN_OFFERED_TEXT if explicit else BROWSER_LOGIN_PAGE_OFFERED_TEXT

    def resolve_browser_login(self, job, url):
        """#636 (Codex P2 on #637): settle this Work's login request for ``url``'s site as ``resolved``.

        Called only when the Work's re-sign-in through the owner's provider completed with
        evidence (a new or renewed site session, no OAuth error).  Only a request still to be
        shown (``requested``: no window opened, no prompt armed) of this Work and of the same
        registrable domain moves, by compare-and-set; another site's or another Work's request is
        never touched, and a window already offered is left to the owner.  True when it moved.
        """
        service=self.service
        try:
            site=registrable_domain(urlsplit(str(url or '')).hostname or '')
        except ValueError:
            return False
        if not site or not isinstance((job or {}).get('id'),str):return False
        with service.lock:
            row=service._browser_login(job['id'])
            if not row or row.get('state')!='requested' or registrable_domain(row.get('host'))!=site:return False
            service._put_browser_login(job['id'],{**row,'state':'resolved','cause':'provider_sign_in','closed_at':time.time()})
        LOG.info('browser login resolved by re-sign-in work=%s',job['id'])
        return True

    def offer_browser_login(self, job):
        """After a Work's run: show the login window it asked for and ask the owner (#709).

        Never waits for the window (#716): the profile is taken here, so the
        next Work cannot take it first, and the window shows on its own
        thread (``_browser_login_opened``), which then arms the prompt.
        Returns True when the window is opening.  A window that cannot open
        (another holder, no engine) records the row as ``unavailable``; the
        Work keeps its ended state.
        """
        service=self.service
        row=service._browser_login(job['id'])
        if not row or row.get('state')!='requested':return False
        # The site's stored sign-in cookies before the window: a login is evidenced by their change.
        now=time.time()
        row={**row,'cookies_before':service._login_cookie_marks(row),'site':registrable_domain(row['host'])}
        work_id,nonce=job['id'],row['nonce']
        service._put_browser_login(work_id,{**row,'state':'opening','window':None,'offered_at':now,
                                         'deadline':now+BROWSER_LOGIN_SECONDS})
        with service.lock:window_url=service._browser_login_window_url(work_id,row)
        try:
            opened=service.browser_profile.open_for_login(
                window_url,seconds=BROWSER_LOGIN_SECONDS,work_id=work_id,
                on_opened=lambda window,landed=None:service._browser_login_opened(work_id,nonce,window,landed),
                on_closed=lambda window,reason,saved:service._browser_login_window_closed(work_id,nonce,window))
        except (ValueError,OSError):
            opened={'state':'failed'}
        if not isinstance(opened,dict) or opened.get('state')!='opening':
            service._put_browser_login(work_id,{**row,'state':'unavailable','closed_at':time.time()})
            LOG.info('browser login window not shown work=%s state=%s',work_id,opened.get('state') if isinstance(opened,dict) else None)
            return False
        with service.lock:
            current=service._browser_login(work_id) or {}
            if current.get('nonce')==nonce and current.get('state') in ('opening','offered') and not current.get('window'):
                service._put_browser_login(work_id,{**current,'window':opened.get('window')})
        return True

    def _browser_login_opened(self, work_id, nonce, window, landed=None):
        """``open_for_login``'s ``on_opened`` (the window's thread): the window shows, ask the owner.

        #749: ``landed`` is the host the window's first navigation landed on
        after redirects; the prompt names that site (the one the owner would
        sign in to) and whether the owner signed in to it before.
        """
        service=self.service
        current=service._browser_login(work_id) or {}
        baselines=service._login_window_baselines(window,current.get('host'),landed)
        stored=service._owner_signed_in(baselines['landed_site'] or current.get('site') or registrable_domain(current.get('host')))
        with service.lock:
            row=service._browser_login(work_id)
            if not row or row.get('state')!='opening' or row.get('nonce')!=nonce:return
            service._put_browser_login(work_id,{**row,**baselines,'state':'offered','window':window,'stored_session':stored})
        job=service.store.job(work_id)
        if job:service._arm_login_notification(job,nonce)

    def _login_window_baselines(self, window, host, landed):
        """The login row's fields once its window showed (#762/#765), for the in-flow and Settings windows.

        ``landed`` is the host the window's first navigation landed on.  After
        the window saved what that landing set (``login_window_landed_saved``),
        the requested site's stored cookies are re-read as ``cookies_before``
        (#765: a cookie the landing page itself set is not the owner's
        sign-in), and, when the window landed on another site (a separate
        sign-in domain, #762), that site's are ``landed_cookies_before``.
        Without that save the pre-window baseline stays and the landed site
        gets none (review P2-1: no baseline is no evidence).
        """
        service=self.service
        landed=ascii_host(landed) if landed else None
        shown=registrable_domain(landed) if landed else None
        fields={'landed_host':landed,'landed_site':shown,'landed_cookies_before':None}
        if not service.browser_profile.login_window_landed_saved(window):return fields
        if host:
            fresh=service._login_cookie_marks({'host':host})
            if fresh is not None:fields['cookies_before']=fresh
        if shown and shown!=registrable_domain(host):
            fields['landed_cookies_before']=service._login_cookie_marks({'host':landed})
        return fields

    def _owner_signed_in(self, site):
        """Whether a cookie the owner's own login-window sign-in to ``site`` produced is still stored,
        unexpired and unchanged (#749).  False otherwise: a cookie a Work's browsing left, or one the
        site set after that sign-in's session ended, is not it.  None when the jar cannot be read, so
        the prompt makes no claim."""
        service=self.service
        signins=service.store.config(BROWSER_OWNER_SIGNINS_KEY,{})
        record=signins.get(site) if site and isinstance(signins,dict) else None
        if not isinstance(record,dict) or not record.get('marks'):return False
        current=service._login_cookie_marks({'host':site})
        if current is None:return None
        return bool(set(record['marks'])&unexpired(current['marks'],current['at']))

    def _record_owner_signin(self, host, before=None, *, db=None):
        """Record the owner's login-window sign-in to the site of ``host`` (#749): the keyed digests
        of the cookies that sign-in added or changed (never a value), compared with ``before``
        (``_login_cookie_marks`` taken before the window)."""
        service=self.service
        site=registrable_domain(host)
        after=service._login_cookie_marks({'host':host}) if site else None
        if after is None:return
        now=after['at']
        added=unexpired(after['marks'],now)-unexpired((before or {}).get('marks'),now)
        if not added:return
        with service.lock:
            signins=service.store.config(BROWSER_OWNER_SIGNINS_KEY,{},db=db)
            signins=signins if isinstance(signins,dict) else {}
            # #990: the host the owner signed in at is where a keep-alive loads a page.
            signins[site]={'at':time.time(),'marks':sorted(added),'host':ascii_host(host) or site}
            service.store.put(BROWSER_OWNER_SIGNINS_KEY,signins,db=db)

    def _record_owner_signins(self, signed, *, db=None, authorizations=((),False)):
        """Record each ``(host, before)`` of ``_signed_in_sites`` as a site the owner signed in to.

        #636: ``authorizations`` is ``(hosts, complete)`` of the same owner login window
        (``BrowserProfile.login_window_authorizations``): every host it sent an OAuth 2.0
        authorization request to, in order.  Each signed-in site records that whole chain
        but the site's own hosts (Codex P1 on #637: a federated sign-in goes site -> broker
        -> upstream; every hop is protected), first hop first.  A sign-in with none observed
        (the owner used another way this time) forgets the site's earlier provider.  The AI's
        own browsing never reaches here.
        """
        service=self.service
        for host,before in signed or ():
            service._record_owner_signin(host,before,db=db)
        hosts,complete=authorizations if isinstance(authorizations,tuple) and len(authorizations)==2 else ((),False)
        service._record_signin_providers([host for host,_before in signed or ()],hosts,complete,db=db)

    def _record_signin_providers(self, hosts, providers, complete=True, *, db=None):
        service=self.service
        sites=[site for site in dict.fromkeys(registrable_domain(host) for host in hosts or () if host) if site]
        if not sites:return
        observed=[ascii_host(host) if isinstance(host,str) else '' for host in providers or ()]
        with service.lock:
            records=service.store.config(BROWSER_PROVIDERS_KEY,{},db=db)
            records=records if isinstance(records,dict) else {}
            now=time.time()
            for site in sites:
                chain=list(dict.fromkeys(host for host in observed if registrable_domain(host)!=site))
                if chain:
                    # A hop that is no host, or a chain the worker could not keep whole, protects nothing for
                    # certain: recorded as incomplete, which gives no re-sign-in (the owner signs in).
                    whole=bool(complete) and all(chain)
                    records[site]={'provider':chain[0],'chain':chain,'complete':whole,'observed_at':now,
                                   'observed_in':'owner_login_window'}
                else:records.pop(site,None)
            service.store.put(BROWSER_PROVIDERS_KEY,records,db=db)

    @staticmethod
    def _provider_chain(record):
        """#636: a provider record's protected hosts, first hop first; a record from before the chain
        (``{'provider': host}``) is a one-host chain.  () when it cannot be relied on."""
        if not isinstance(record,dict) or record.get('complete') is False:return ()
        chain=record.get('chain') if isinstance(record.get('chain'),list) else [record.get('provider')]
        chain=[str(host) for host in chain if isinstance(host,str) and host]
        return tuple(chain) if chain and len(chain)==len(record.get('chain') or [record.get('provider')]) else ()

    def provider_signin_settings(self):
        """#636: the owner's switch (on unless turned off), the sites it is off for, and each recorded
        site's provider host (names only)."""
        service=self.service
        switch=service.store.config(BROWSER_PROVIDER_SIGNIN_KEY,{})
        switch=switch if isinstance(switch,dict) else {}
        records=service.store.config(BROWSER_PROVIDERS_KEY,{})
        records=records if isinstance(records,dict) else {}
        return {'enabled':switch.get('enabled') is not False,
                'off_sites':sorted(str(site) for site in switch.get('off_sites') or () if isinstance(site,str)),
                'sites':{site:' → '.join(service._provider_chain(row)) or str(row.get('provider')) for site,row in sorted(records.items())
                         if isinstance(row,dict) and isinstance(row.get('provider'),str)}}

    def set_provider_signin(self, value):
        """#636: ``on``/``off`` for every site, or ``<site>|on``/``<site>|off`` for one."""
        service=self.service
        site,sep,state=str(value or '').rpartition('|')
        if state not in ('on','off') or (sep and not site):raise ValueError(msg('settings_orchestrator.invalid_value'))
        with service.lock:
            switch=service.store.config(BROWSER_PROVIDER_SIGNIN_KEY,{})
            switch=dict(switch) if isinstance(switch,dict) else {}
            if not sep:
                switch['enabled']=state=='on'
            else:
                off=set(switch.get('off_sites') or ())
                site=registrable_domain(site)
                if state=='off':off.add(site)
                else:off.discard(site)
                switch['off_sites']=sorted(off)
            service.store.put(BROWSER_PROVIDER_SIGNIN_KEY,switch)
        return switch

    def browser_provider_hosts(self):
        """#636 (Codex P1 on #637): every host any recorded provider chain names, complete or not, whatever the
        hint switch says: the exact hosts the owner's own login window saw carrying an OAuth 2.0
        authorization request.  An AI step is never pressed or typed on their pages (``BrowserSession._observe``)."""
        service=self.service
        records=service.store.config(BROWSER_PROVIDERS_KEY,{})
        hosts=set()
        for record in (records.values() if isinstance(records,dict) else ()):
            if not isinstance(record,dict):continue
            chain=record.get('chain') if isinstance(record.get('chain'),list) else []
            hosts.update(ascii_host(host) for host in [*chain,record.get('provider')] if isinstance(host,str) and host)
        return frozenset(hosts)

    def browser_provider_signin_enabled(self, url):
        service=self.service
        settings=service.provider_signin_settings()
        site=registrable_domain(urlsplit(str(url or '')).hostname)
        return settings['enabled'] and site not in settings['off_sites']

    def browser_signin_provider(self, url):
        """#636: ``{site, provider}`` when a Work may sign back in to ``url``'s site through the provider the
        owner used there, else None.

        Only a provider the owner's own login window recorded (``_record_signin_providers``), only
        while the owner has not turned this off for every site or for this one, and while the jar holds an unexpired
        cookie of that provider.  Nothing is sent anywhere here.
        """
        service=self.service
        try:
            host=urlsplit(str(url or '')).hostname
        except ValueError:
            return None
        site=registrable_domain(host) if host else None
        if not site:return None
        records=service.store.config(BROWSER_PROVIDERS_KEY,{})
        chain=service._provider_chain(records.get(site) if isinstance(records,dict) else None)
        if not chain or not service.browser_provider_signin_enabled(url):return None
        # Which hop holds the session that lets the provider answer silently is not known, so every hop's is required.
        for hop in chain:
            read=service.browser_profile.site_cookie_marks(hop)
            if not read or not unexpired(read[0],read[1]):return None
        return {'site':site,'provider':chain[0],'chain':list(chain)}

    def _login_cookie_marks(self, row):
        """``{'at': jar time, 'marks': [[keyed digest, expires], ...], 'ids': [[keyed identity, expires], ...]}``
        of the login host's stored sign-in cookies, or None when the jar cannot be read.  ``marks``
        covers each cookie's value, ``ids`` only its name, domain and path (#765); both are keyed
        digests, never a name or value."""
        service=self.service
        read=service.browser_profile.site_cookie_marks(row.get('host'))
        if read is None:return None
        marks,now=read
        secret=service.store.secret('browser_step_secret',create=lambda:secrets.token_hex(32)).encode()
        keyed=lambda kind,digest:hmac.new(secret,(kind+'|'+digest).encode(),hashlib.sha256).hexdigest()
        return {'at':now,'marks':[[keyed('login-cookie',mark[0]),mark[1]] for mark in marks],
                'ids':[[keyed('login-cookie-id',mark[2]),mark[1]] for mark in marks if len(mark)>2]}

    def _signed_in_sites(self, row):
        """``[(host, baseline)]`` of the login's sites the owner signed in to while its window was open (#765).

        The sites are the requested host and, when the window landed on
        another site, that landed host, each read against its own baseline
        (#762).  Only the unexpired cookies count (``browser_jar.unexpired``,
        the rule the worker import applies): the close's at its jar time, and
        for an added cookie the baseline's at the baseline's jar time.

        * A site that gained a cookie (a new name, domain or path) since its
          baseline is signed in.  A cookie whose value rotated or was refreshed
          (a bot-management, consent or anonymous cookie a script or background
          request set) is not new.
        * Only when no site gained one: a site whose cookies changed at all
          counts, and only if the owner navigated the window after its landing
          (``login_window_navigated``; a form submission or the sign-in's
          redirect), so a re-login that only rotated a stale session cookie is
          still seen while a change nobody caused is not.

        No baseline (never recorded, or the jar was unreadable) is no evidence.
        A site is recorded as signed in only by its own evidence.
        """
        service=self.service
        readings=[]
        for host,before in ((row.get('host'),row.get('cookies_before')),
                            (row.get('landed_host'),row.get('landed_cookies_before'))):
            if not host or not isinstance(before,dict):continue
            after=service._login_cookie_marks({'host':host})
            if after is None:continue
            now=after['at']
            # A baseline cookie counts as it was then (Codex P2 on #773): one that expired while the window
            # was open and a background refresh re-set under the same name, domain and path is not new.
            added=isinstance(before.get('ids'),list) and bool(
                unexpired(after['ids'],now)-unexpired(before['ids'],before.get('at',now)))
            changed=unexpired(before.get('marks'),now)!=unexpired(after['marks'],now)
            readings.append((host,before,added,changed))
        signed=[(host,before) for host,before,added,_changed in readings if added]
        if signed or not service.browser_profile.login_window_navigated(row.get('window')):return signed
        return [(host,before) for host,before,_added,changed in readings if changed]

    def _arm_login_notification(self, job, nonce):
        """Queue the optional Telegram prompt for this login (a Work asked again re-arms its row)."""
        service=self.service
        service.queue_notification(job,'browser_login_needed',fingerprint=nonce)
        service.store.telegram.rearm(job['id'],'browser_login_needed',nonce)

    def browser_login_prompt(self, work_id, phone=False, *, preparing=False, failed=False):
        """The owner's login prompt: the site first, the full host when longer, and a note when no
        sign-in for that site is stored (#716).  Names only; never the page path.  ``phone``: the
        same lines while the phone link drives the window, ending in its one way to finish (#255)."""
        service=self.service
        row=service._browser_login(work_id) or {}
        requested=row.get('site') or registrable_domain(row.get('host'))
        # #749: the site the window actually landed on (after redirects) leads; the requested one is named.
        host=ascii_host(row.get('landed_host') or row.get('host'))
        site=row.get('landed_site') or requested or registrable_domain(host) or msg('quickstart_service.this_site')
        template=(BROWSER_LOGIN_PHONE_PREPARING_PROMPT if preparing else BROWSER_LOGIN_PHONE_FAILED_PROMPT if failed
                  else BROWSER_LOGIN_PHONE_PROMPT if phone else BROWSER_LOGIN_PROMPT)
        return template.format(
            site=site,address=BROWSER_LOGIN_ADDRESS_LINE.format(host=host) if host and host!=site else '',
            moved=BROWSER_LOGIN_MOVED_LINE.format(site=requested) if row.get('landed_site') and requested and requested!=site else '',
            session=BROWSER_LOGIN_NO_SESSION_LINE if row.get('stored_session') is False else '')

    def browser_login_decision(self, body):
        """The owner's optional web decision on one offered login: ``done`` resumes once, ``skip`` finishes.

        The work loop closes the window and settles it; this only records the decision.
        """
        service=self.service
        if not isinstance(body,dict) or not isinstance(body.get('work_id'),str) or body.get('decision') not in ('done','skip'):
            raise ValueError(msg('quickstart_service.check_login_decision'))
        row=service._browser_login(body['work_id'])
        if not row or row.get('state')!='offered':raise ValueError(msg('quickstart_service.no_waiting_login'))
        return service._request_login_decision(body['work_id'],row['nonce'],'resume' if body['decision']=='done' else 'skip')

    @staticmethod
    def _login_window_mark(nonce):
        """#1572: a short public mark of one offered login (never its nonce), so a draft binds to exactly that window."""
        return hashlib.sha256(('login-window:'+str(nonce or '')).encode()).hexdigest()[:12]

    def open_login_windows(self):
        """#1572: the in-flow login windows waiting for the owner: ``{value: label}`` for ``browser close_login``.

        Site and minutes left only; never a URL path, query or what was typed in the window.
        """
        service=self.service
        now,windows=time.time(),{}
        for work_id,row in sorted(service.browser_login_requests().items()):
            if not isinstance(row,dict) or row.get('state')!='offered':continue
            site=row.get('landed_site') or row.get('site') or registrable_domain(row.get('host')) or msg('quickstart_service.this_site')
            left=max(0,int((float(row.get('deadline') or now)-now)//60))
            windows[f"{work_id}|{service._login_window_mark(row.get('nonce'))}"]=(
                msg('quickstart_service.login_window_title', site=site, work_id=work_id[:8], minutes=left))
        return windows

    def close_login_window_by_owner(self, value):
        """#1572: the owner confirmed closing one exact offered login window (``browser close_login``).

        The same settlement as the prompt's 건너뛰기: the window stops, its cookies are saved, and the Work
        that asked is not resumed as if signed in.  Returns the owner-facing text; it never claims the
        profile is free before the window has actually closed.
        """
        service=self.service
        work_id,_sep,mark=str(value or '').partition('|')
        row=service._browser_login(work_id) or {}
        if row.get('state')!='offered' or not hmac.compare_digest(service._login_window_mark(row.get('nonce')),mark):
            raise ValueError(msg('quickstart_service.login_window_changed'))
        decided=service._request_login_decision(work_id,row['nonce'],'skip')
        if decided.get('state')!='closing':
            raise ValueError(msg('quickstart_service.login_window_changed'))
        return msg('quickstart_service.login_window_closing')

    def _request_login_decision(self, work_id, nonce, intent):
        """Mark one offered login for the work loop to close and settle; the first decision wins."""
        service=self.service
        with service.lock:
            row=service._browser_login(work_id)
            if not row or row.get('state')!='offered' or not hmac.compare_digest(str(row.get('nonce','')),str(nonce or '')):
                return {'work_id':work_id,'state':None}
            service._put_browser_login(work_id,{**row,'state':'closing','intent':intent,'decided_at':time.time()})
        # #716: only asks the window to close (never waits); its own thread settles it once closed and saved.
        service.browser_profile.close_login_window(row.get('window'),timeout=0)
        return {'work_id':work_id,'state':'closing','intent':intent}

    def _browser_login_window_closed(self, work_id, nonce, window):
        """``BrowserProfile.open_for_login``'s ``on_closed`` (the window's thread): the window closed,
        was saved and released.  One that never showed leaves its login ``unavailable``."""
        service=self.service
        with service.lock:
            row=service._browser_login(work_id)
            if not row or row.get('nonce')!=nonce:return
            if row.get('state')=='opening':
                service._put_browser_login(work_id,{**row,'state':'unavailable','window':window,'closed_at':time.time()})
                return
        service._reconcile_browser_login(work_id)

    def _reconcile_browser_login(self, work_id, now=None):
        """Settle one waiting login from what its window did (#709).  Returns the final state or None.

        * a window this process does not know (a restart) -> ``expired``;
        * a decision was recorded (or the deadline passed, recorded as an
          ``expire`` decision) -> ask the window to close, and settle it once
          it has closed and saved: its ``on_closed`` or a later pass does;
        * the owner closed the window -> resume (only if its cookies were saved);
        * the window timed out or failed -> ``expired``.

        Never waits for the window (#716): this runs on the work loop and on
        the window's own thread.  A close that has not finished
        ``BROWSER_LOGIN_CLOSE_SECONDS`` after it was asked settles as not closed, so
        the Work is never re-queued while the window may still hold the profile.
        """
        service=self.service
        now=time.time() if now is None else now
        row=service._browser_login(work_id)
        if not row or row.get('state') not in ('offered','closing'):return None
        if not row.get('window'):
            # #749: a row an older version left waiting without its window id: nothing can close or
            # settle that window, so it expires (never re-queued) and a later login page may ask again.
            return service._settle_browser_login(work_id,row.get('nonce'),'expire',closed=False)
        window,nonce=row['window'],row.get('nonce')
        if not service.browser_profile.login_window_known(window):
            return service._settle_browser_login(work_id,nonce,'expire',closed=False)
        outcome=service.browser_profile.login_window_outcome(window)
        if row['state']=='offered' and outcome is None and now>=float(row.get('deadline') or 0):
            with service.lock:
                current=service._browser_login(work_id)
                if current and current.get('state')=='offered' and current.get('nonce')==nonce:
                    row={**current,'state':'closing','intent':'expire','decided_at':now}
                    service._put_browser_login(work_id,row)
        if row['state']=='closing':
            if outcome is None:
                service.browser_profile.close_login_window(window,timeout=0)
                outcome=service.browser_profile.login_window_outcome(window)
            if outcome is None:
                if now<float(row.get('decided_at') or 0)+BROWSER_LOGIN_CLOSE_SECONDS:return None
                return service._settle_browser_login(work_id,nonce,row.get('intent'),closed=False)
            closed=outcome[1]
            return service._settle_browser_login(work_id,nonce,row.get('intent'),closed=closed,
                                              signed=service._signed_in_sites(row) if closed and row.get('intent')!='expire' else ())
        if outcome is None:return None
        reason,saved=outcome
        if reason=='owner':
            return service._settle_browser_login(work_id,nonce,'owner_close',closed=saved,
                                              signed=service._signed_in_sites(row) if saved else ())
        return service._settle_browser_login(work_id,nonce,'expire',closed=saved and reason=='timeout')

    def _settle_browser_login(self, work_id, nonce, intent, closed, signed=()):
        """Finish one login exactly once.  Only a closed and saved window with evidence that the owner
        signed in (``signed``, the sites of ``_signed_in_sites``) resumes the Work.

        ``intent`` is ``owner_close`` (the owner closed the window), ``resume``
        (로그인 완료), ``skip`` (건너뛰기) or ``expire``.  A close or 로그인 완료
        with no sign-in evidence is not a login: the Work is not re-queued and a
        later login page may ask again (``not_logged_in``); a close says so as
        a skip, 로그인 완료 says the session was not saved.

        The exact nonce, observed sign-in bookkeeping, continuation receipt and
        original queue transition settle in one SQLite transaction under the
        service lock, so a second tap, a stale message, the owner's close and
        the timeout race to one outcome.  ``resume`` re-queues this exact Work
        once by compare-and-set; when the window could not be closed and saved
        the row expires instead (``close_failed``) and the Work is not re-queued.
        """
        service=self.service
        with service.lock, service.store.db() as db:
            db.execute('BEGIN IMMEDIATE')
            row=service.browser_login_requests(db=db).get(work_id)
            if not row or row.get('state') not in ('offered','closing') or not hmac.compare_digest(str(row.get('nonce','')),str(nonce or '')):
                return None
            if not closed and intent in ('resume','owner_close','expire'):
                final,shown='expired','close_failed'
            elif intent in ('resume','owner_close') and not signed:
                final,shown='not_logged_in',('no_session' if intent=='resume' else 'skipped')
            elif intent in ('resume','owner_close'):
                job_row=db.execute('SELECT * FROM jobs WHERE id=?',(work_id,)).fetchone()
                job=dict(job_row) if job_row else None
                current=bool(job and (ws.is_resumable_login_explicit(job['status']) if row.get('explicit') else ws.is_resumable_continuation(job['status'])) and not service.work_stopped(work_id)
                             and (not any(surface.claims_channel(job.get('channel')) for surface in service.surfaces) or service._surface_work(job))
                            )
                if time.time()>float(row.get('deadline') or 0):
                    final=shown='expired'
                else:
                    resumed=False
                    if current:
                        # Reuse the existing exact-offer receipt, common bound
                        # and remaining-obligation context, never action authority.
                        binding='browser-login:'+str(nonce)
                        try:
                            resumed=service._queue_action_continuation(db,job,
                                {'action':'browser_sign_in','digest':binding,'offer_digest':binding})
                        except (ValueError,TypeError,KeyError):
                            # Unreadable effect/request evidence cannot permit replay.
                            db.execute("UPDATE jobs SET status='unknown',error=?,owner_cause=? WHERE id=?",
                                       (UNKNOWN_EFFECT_RETRY_REFUSAL,UNKNOWN_EFFECT_RETRY_REFUSAL,work_id))
                    final=shown='resumed' if resumed else 'not_resumed'
                    if resumed:service._record_owner_signins(signed,db=db,authorizations=service.browser_profile.login_window_authorizations(row.get('window')))
            elif intent=='skip':
                final=shown='skipped'
            else:
                final=shown='expired'
            # Login settlement, sign-in attribution, original queue and its
            # receipt are one commit. No durable intermediate resuming state.
            service._put_browser_login(work_id,{**row,'state':final,'cause':shown,'closed_at':time.time()},db=db)
        if final!='resumed' and not service.photo_work_has_pending_resume(work_id):service.store.remove_telegram_photo(work_id)
        service._finish_login_notification(work_id,shown)
        return final

    def _finish_login_notification(self, work_id, shown):
        """Tell the owner the outcome once: edit the Telegram prompt, cancel an unsent one, or (for a
        login that expired unanswered with no prompt shown) leave one line in the conversation."""
        service=self.service
        note=service.store.notification_of(work_id,'browser_login_needed')
        if note and not service._surface_work(service.store.job(work_id)):
            if note['state'] in ('sent','queued'):service.store.update_notification(note['id'],'cancelled')
            return
        row=service._browser_login(work_id) or {}
        if note and note['fingerprint']==row.get('nonce') and note['state']=='sent':
            service.store.update_notification(note['id'],'browser_login_'+shown)
            service._edit_login_result(work_id,note,shown)
            return
        if note and note['fingerprint']==row.get('nonce') and note['state']=='queued':
            service.store.update_notification(note['id'],'cancelled')
        if shown in ('expired','close_failed','no_session'):
            job=service.store.job(work_id)
            if job:
                with service.store.db() as db:
                    db.execute('INSERT INTO messages(role,content,channel,created,workspace_id,job_id) VALUES (?,?,?,?,?,?)',
                               ('assistant',BROWSER_LOGIN_RESULT_TEXT[shown],job['channel'],time.time(),job.get('workspace_id'),work_id))

    def _edit_login_result(self, work_id, note, shown):
        """Edit the sent prompt ``note`` to the login's outcome ``shown``."""
        service=self.service
        if not (isinstance(note.get('message_id'),int) and note['message_id']>0):return
        # #1573: an unanswered window that expired keeps one button to reopen it for this same Work.
        reopen=shown=='expired' and service._login_reopenable(work_id)
        text=BROWSER_LOGIN_REOPEN_TEXT if reopen else BROWSER_LOGIN_RESULT_TEXT.get(shown,'')
        markup={'inline_keyboard':[[{'text':BROWSER_LOGIN_REOPEN_LABEL,
                                     'callback_data':f"p7l:{note['id']}:reopen"}]] if reopen else []}
        try:service.telegram.edit_message_text(note['chat_id'],note['message_id'],text,markup)
        except ProviderError:pass

    def _show_phone_login_prompt(self, work_id, nonce, *, failed=False):
        """#255 (owner choice 2026-10-24): once the phone link was delivered, the prompt drops its buttons.

        Its 로그인 완료 and 건너뛰기 sat above the link, next to the phone page's [완료]; one tap there
        (live 2026-10-23) skipped a sign-in in progress.  The settled outcome replaces this text as before,
        also when the login settled while this edit was in flight.
        """
        service=self.service
        session=service.remote_login_session()
        note=service.store.notification_of(work_id,'browser_login_needed')
        row=service._browser_login(work_id) or {}
        if not ((failed or session is not None and session.window==row.get('window')) and note and note['state']=='sent'
                and note['fingerprint']==nonce and row.get('state')=='offered' and row.get('nonce')==nonce
                and isinstance(note.get('message_id'),int) and note['message_id']>0):
            return False
        # #599: an automatic start had no buttons; restore the Mac path and phone retry on failure.
        markup=service.telegram_surface._notice_markup(note,{}) if failed else {'inline_keyboard':[]}
        try:service.telegram.edit_message_text(note['chat_id'],note['message_id'],service.browser_login_prompt(work_id,phone=not failed,failed=failed),markup)
        except ProviderError:return False
        after=service.store.notification(note['id']) or {}
        state=str(after.get('state') or '')
        if state.startswith('browser_login_'):
            service._edit_login_result(work_id,after,state[len('browser_login_'):])
        return True

    def _login_reopenable(self, work_id):
        """Whether this Work's unanswered, expired login may be reopened from its prompt (#1573).

        Only the window's own timeout (not a skip, a close without sign-in or a failed close), at most
        ``BROWSER_LOGIN_REOPENS`` times, while the Work still ended short (partial/failed), is not
        stopped, is this chat's Telegram Work and the site may still be signed in to.
        """
        service=self.service
        row=service._browser_login(work_id) or {}
        job=service.store.job(work_id)
        return bool(row.get('state')=='expired' and row.get('cause')=='expired'
                    and int(row.get('reopens') or 0)<BROWSER_LOGIN_REOPENS
                    and job and ws.is_resumable_continuation(job['status']) and not service.work_stopped(work_id)
                    and service._surface_work(job))

    def reopen_browser_login(self, work_id, nonce):
        """The expired prompt's 로그인 창 다시 열기: ask the same login again for the same Work (#1573).

        Exact: the row is still the expired one this prompt named (``nonce``).  The row returns to
        ``requested`` with a new nonce; the work loop opens the window (``process_browser_logins``)
        and re-arms the Work's prompt, and a completed sign-in resumes the original Work through the
        same continuation as the first window.  Returns whether it was reopened.
        """
        service=self.service
        with service.lock:
            row=service._browser_login(work_id)
            if (not row or not hmac.compare_digest(str(row.get('nonce','')),str(nonce or ''))
                    or not service._login_reopenable(work_id)):
                return False
            kept={key:row[key] for key in ('work_id','url','host') if key in row}
            window_url=service._browser_login_window_url(work_id,row)
            cached=work_id in service._browser_login_window_urls
            new_nonce=secrets.token_hex(16)
            service._put_browser_login(work_id,{**kept,'state':'requested','requested_at':time.time(),
                                             'nonce':new_nonce,'explicit':bool(row.get('explicit')),
                                             'reopens':int(row.get('reopens') or 0)+1})
            if cached:service._browser_login_window_urls[work_id]=(new_nonce,window_url)
        LOG.info('browser login reopened by owner work=%s',work_id)
        return True

    def _reopen_failed(self, work_id, nonce, now):
        """A reopened login whose window could not open (#1573): it stays an expired login, and the
        prompt says so and offers the button again while a reopen is left."""
        service=self.service
        with service.lock:
            row=service._browser_login(work_id)
            if not row or row.get('nonce')!=nonce or row.get('state') not in ('requested','unavailable'):return
            service._put_browser_login(work_id,{**row,'state':'expired','cause':'expired','closed_at':now})
        note=service.store.telegram.expire_reopened_login(work_id,nonce)
        if not note or not isinstance(note['message_id'],int) or note['message_id']<=0:return
        reopen=service._login_reopenable(work_id)
        markup={'inline_keyboard':[[{'text':BROWSER_LOGIN_REOPEN_LABEL,'callback_data':f"p7l:{note['id']}:reopen"}]]
                if reopen else []}
        try:service.telegram.edit_message_text(note['chat_id'],note['message_id'],BROWSER_LOGIN_REOPEN_FAILED_TEXT
                                            if reopen else BROWSER_LOGIN_RESULT_TEXT['expired'],markup)
        except ProviderError:pass
