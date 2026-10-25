"""#248 / #171: expand the existing guard without editing its shared test file.

Reuse: the AST scanner and classifications in test_no_scenario_code. No new
runtime component, dependency, or authority; these are static regression tests.
"""
import test_no_scenario_code as guard


# These modules do not pass the existing scanner. Keep them visible here rather
# than adding broad table-name classifications that would hide future cues.
# The forbidden modules cannot be repaired in this worktree; the others need
# separate classification of protocol/security syntax or product-code changes.
EXCLUDED = {
    'bounded_execution.py': 'Forbidden: CLI authentication, quota and error regexes need protocol classification.',
    'quickstart.py': 'Forbidden: provider routes, OAuth hosts and UI labels need separate classification.',
    'telegram_surface.py': 'Forbidden: photo attachment marker comparisons are reported as language cues.',
    'connector_revocation.py': 'OAuth revoke endpoint hostname is provider protocol syntax.',
    'context_inbox.py': 'Sensitive-data filtering regex is security policy, not owner intent.',
    'conversation_projection.py': 'Hangul range comparisons implement grammatical particles.',
    'drive_web_oauth.py': 'Drive scopes, MIME types and connection-status text need classification.',
    'folder_grants.py': 'Sensitive host directories are filesystem authority exclusions.',
    'intranet.py': 'Metadata hostname denylist enforces network authority.',
    'isolated_engine_gateway.py': 'Fixed worker-error sanitization strings need protocol classification.',
    'isolated_engine_sidecar.py': 'Argument-parser error strings need protocol classification.',
    'local_folder_picker.py': 'AppleScript source lines are platform syntax.',
    'personal_knowledge.py': 'Prompt-injection filtering regex needs security-policy classification.',
    'public_query.py': 'Private-key detection regex needs security-policy classification.',
    'quickstart_store.py': 'SQLite constructor error matching needs protocol classification.',
    'service_control.py': 'Service-manager error matching needs protocol classification.',
    'uninstall.py': 'Provider cleanup result key outside_google needs classification.',
}


def scanned_paths(root=guard.SRC):
    """Discover every Python module, including future modules, except explained gaps."""
    excluded = set(EXCLUDED) | set(guard.ALLOWED_MODULES)
    return sorted(path for path in root.rglob('*.py') if path.name not in excluded)


def test_all_passing_kernel_modules_are_scanned():
    paths = scanned_paths()
    assert set(guard.scanned_paths()) <= set(paths)
    assert guard.SRC / 'work_execution.py' in paths
    assert len(paths) > len(guard.scanned_paths())
    unclassified = [hit for hit in guard.scan(paths) if guard.classified_by(hit) is None]
    assert unclassified == [], 'New cue or scenario branch in the expanded kernel scan'


def test_exclusions_still_need_classification():
    for module, reason in EXCLUDED.items():
        assert reason
        assert any(guard.classified_by(hit) is None for hit in guard.scan([guard.SRC / module])), (
            module, 'now passes: remove the exclusion and scan it')


def test_a_new_module_with_a_cue_table_fails_the_expanded_guard(tmp_path):
    nested = tmp_path / 'new_kernel'
    nested.mkdir()
    planted = nested / 'work_execution.py'
    planted.write_text("CUES = ('승인', 'go ahead')\ndef route(text):\n    return text in CUES\n")
    paths = scanned_paths(tmp_path)
    assert paths == [planted], 'new modules are discovered without an opt-in list'
    hits = [hit for hit in guard.scan(paths) if guard.classified_by(hit) is None]
    assert {hit[3] for hit in hits} == {'승인', 'go ahead'}
    planted.write_text("NOTICE = '승인 go ahead'\ndef reply():\n    return NOTICE\n")
    assert guard.scan(scanned_paths(tmp_path)) == [], 'output text alone is not a steering cue'
