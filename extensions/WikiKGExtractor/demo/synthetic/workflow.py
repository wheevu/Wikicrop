"""Two bounded synthetic runs; no scientific or production evaluation."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

PRIVATE = Path('/private/wikikg')
WIKI = Path('/var/www/html/w')
TOOLS = WIKI / 'extensions/WikiKGExtractor/tools'
MAINTENANCE = WIKI / 'extensions/WikiKGExtractor/maintenance'
ACTOR = 'Synthetic reviewer'
ENVIRONMENT = {**os.environ, 'MW_CONFIG_FILE': str(PRIVATE / 'LocalSettings.synthetic.php'),
               'DOCUMENT_ROOT': '/var/www/html', 'WIKIKG_SYNTHETIC': '1', 'PYTHONDONTWRITEBYTECODE': '1'}
os.umask(0o077)
if os.environ.get('WIKIKG_SYNTHETIC') != '1' or (PRIVATE / 'synthetic-marker').read_text() != 'seeded':
    raise SystemExit('synthetic_workflow_refused')


def command(argv, allowed=(0,)):
    process = subprocess.run([str(arg) for arg in argv], env=ENVIRONMENT, cwd=WIKI,
                             capture_output=True, text=True, timeout=120)
    with (PRIVATE / 'workflow-commands.log').open('a') as log:
        log.write(json.dumps({'argv': [str(arg) for arg in argv], 'code': process.returncode,
                              'stdout': process.stdout, 'stderr': process.stderr}) + '\n')
    os.chmod(PRIVATE / 'workflow-commands.log', 0o600)
    if process.returncode not in allowed:
        raise RuntimeError(f'{Path(str(argv[1])).name}: code={process.returncode}')
    return process


def private_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False))
    os.chmod(path, 0o600)


def question(label, query, export, directory, allowed):
    request = directory / f'{label}-question.json'
    result = directory / f'{label}-result.json'
    private_json(request, {'version': 'wikikg.offline-question.v1', 'query': query})
    process = command([sys.executable, TOOLS / 'offline_answer.py', '--manifest', export,
        '--request', request, '--output', result, '--actor', ACTOR, '--wiki-root', WIKI,
        '--document-root', '/var/www/html', '--synthetic-only', '--offline-only',
        '--audience', 'actor-only', '--wiki-base',
        'http://127.0.0.1:' + os.environ.get('WIKIKG_HTTP_PORT', '8787')], allowed)
    document = json.loads(result.read_text())
    assert document['answer'] is None and document['execution']['generated'] is False
    assert document['execution']['authorization_calls'] == 2
    assert ACTOR not in process.stdout and str(PRIVATE) not in process.stdout
    return document


results = []
previous_snapshot_ids = None
for run_index in (1, 2):
    run = PRIVATE / 'posix' / 'runs' / f'run-{run_index}'
    run.mkdir(mode=0o700)
    common = [sys.executable, TOOLS / 'bounded_discovery.py', '--output-dir', run,
              '--actor', ACTOR, '--offline-only', '--audience', 'actor-only',
              '--corpus-manifest', PRIVATE / 'corpus-metadata.json',
              '--classifications', PRIVATE / 'classifications.json',
              '--runtime-root', WIKI, '--document-root', '/var/www/html', '--timeout', '60']
    command([*common, '--operation', 'prepare'], (3,))
    prepared = json.loads((run / 'receipt.json').read_text())
    assert prepared['prepare']['status'] == 'completed'
    assert len(prepared['inventory']['pages']) == 8
    assert sum(len(batch['pages']) for batch in prepared['batches']) == 5
    for batch in prepared['batches']:
        command([*common, '--operation', 'batch', '--batch-index', batch['index']], (0, 3))
        receipt = json.loads((run / 'receipt.json').read_text())
        assert receipt['batches'][batch['index']]['status'] == 'completed'
    receipt = json.loads((run / 'receipt.json').read_text())
    summary = json.loads(command([sys.executable, TOOLS / 'bounded_discovery.py',
                                  '--operation', 'summary', '--output-dir', run]).stdout)
    assert summary['complete'] and summary['planned_candidates'] == summary['extraction_states']['completed'] == 5
    snapshot_ids = []
    for batch in receipt['batches']:
        attempt = batch['attempts'][-1]
        for job in attempt['extraction_receipt']['jobs']:
            worker = run / attempt['attempt_dir'] / 'extraction' / job['job_dir'] / 'worker'
            process = command(['php', MAINTENANCE / 'importReviewedClaims.php', '--actor=' + ACTOR,
                '--offline-only', '--audience=actor-only', '--synthetic-only', '--confirm-import',
                '--document-root=/var/www/html', '--claims=' + str(worker / 'candidate_claims.json'),
                '--raw-data=' + str(worker / 'raw_data.json')])
            snapshot_ids.extend(json.loads(process.stdout)['snapshot_ids'])
    if previous_snapshot_ids is None:
        for snapshot in snapshot_ids:
            command(['php', MAINTENANCE / 'reviewOfflineClaim.php', '--actor=' + ACTOR,
                '--offline-only', '--audience=actor-only', '--synthetic-only', '--snapshot=' + snapshot,
                '--status=approved', '--expected-version=0', '--reason=Synthetic software fixture only'])
        previous_snapshot_ids = set(snapshot_ids)
    else:
        assert set(snapshot_ids) == previous_snapshot_ids
    evidence = PRIVATE / f'evidence-{run_index}'
    evidence.mkdir(mode=0o700)
    export = evidence / 'reviewed.json'
    command(['php', MAINTENANCE / 'exportReviewedProjection.php', '--actor=' + ACTOR,
             '--synthetic-only', '--document-root=/var/www/html', '--output=' + str(export)])
    manifest = json.loads(export.read_text())
    assert len(manifest['claims']) == 9
    assert sum(claim['claim_type'] == 'relationship' for claim in manifest['claims']) == 5
    assert sum(claim['claim_type'] == 'property' for claim in manifest['claims']) == 4
    memberships = [claim for claim in manifest['claims'] if claim['predicate'] == 'HAS_VARIETY']
    swift = next(claim for claim in memberships if any(item['page_title'] == 'Verification crop Swift' for item in claim['evidence']))
    ranged = next(claim for claim in memberships if any(item['page_title'] == 'Verification crop Range' for item in claim['evidence']))
    def entity(membership):
        return {**membership['object'], 'crop_id': membership['subject']['id']}
    lookup = question('lookup', {'intent': 'lookup', 'entities': [entity(swift)]}, export, evidence, (0,))
    supported = question('supported', {'intent': 'filter', 'purpose': 'answer', 'entities': [entity(swift)],
        'filters': [{'predicate': 'growth_duration', 'op': '<', 'value': '100 days'}]}, export, evidence, (0,))
    uncertain = question('straddling', {'intent': 'filter', 'purpose': 'answer', 'entities': [entity(ranged)],
        'filters': [{'predicate': 'growth_duration', 'op': '<', 'value': '100 days'}]}, export, evidence, (3,))
    assert lookup['status'] == 'evidence' and supported['status'] == 'evidence'
    assert lookup['query']['status'] == 'ok' and supported['query']['status'] == 'ok'
    assert uncertain['status'] == 'blocked' and 'numeric_uncertain' in {r['code'] for r in uncertain['reasons']}
    results.append({'run': run_index, 'summary': summary, 'snapshot_ids': sorted(set(snapshot_ids)),
        'exported_claims': len(manifest['claims']), 'lookup_status': lookup['status'],
        'supported_status': supported['status'], 'straddling_status': uncertain['status'],
        'fresh_authorization_calls_per_question': 2, 'generation_calls': 0})
private_json(PRIVATE / 'workflow-results.json', {'synthetic_only': True, 'runs': results,
             'same_snapshot_ids': results[0]['snapshot_ids'] == results[1]['snapshot_ids']})
print(json.dumps({'runs': len(results), 'completed_jobs_per_run': 5,
                  'exported_claims': results[-1]['exported_claims'], 'generated': False}))
