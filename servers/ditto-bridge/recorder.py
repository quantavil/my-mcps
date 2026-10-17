"""Free human exploration through the MCP controller; candidates need AI review."""
from datetime import datetime, timezone
from hashlib import sha256
from html import escape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import threading
import tempfile
import time
import xml.etree.ElementTree as ET
from urllib.parse import parse_qs, quote, urlparse


PANEL = r'''<!doctype html><html lang="en"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Ditto walkthrough</title>
<style>
body{font:16px/1.45 system-ui;margin:16px;color:#191919;background:#fafafa}
h1{margin:0 0 12px;font-size:1.5rem}h2{margin:0 0 8px;font-size:1.1rem}
main{display:grid;grid-template-columns:minmax(0,1fr) minmax(270px,320px);gap:24px;align-items:start}
body.clone main{grid-template-columns:minmax(0,1fr) minmax(0,1fr) minmax(270px,320px)}
section,aside{min-width:0}aside{position:sticky;top:16px;max-height:calc(100vh - 32px);overflow:auto}
button,input{font:inherit}button{padding:8px 12px;margin:4px 4px 4px 0;cursor:pointer}
button:focus-visible,input:focus-visible,textarea:focus-visible,a:focus-visible{outline:2px solid #1d5e9b;outline-offset:2px}
label{display:flex;align-items:center;gap:10px;margin:0 0 10px}
#preview-size{width:min(220px,55vw)}#size-value{font-variant-numeric:tabular-nums}
#screen,#reference{display:block;width:min(100%,var(--preview-width,600px));height:auto;touch-action:none;user-select:none;background:#e6e6e6}
#screen.busy{opacity:.7}#guide{max-height:30vh;overflow:auto;padding-left:24px;margin:8px 0}
#guide li{margin:0 0 8px}#typing{display:flex;gap:6px;margin-top:8px}#text{min-width:0;flex:1;padding:8px}
#message{white-space:pre-wrap}#saved-path{display:block;overflow-wrap:anywhere;font:13px/1.4 ui-monospace,monospace}
#notes{box-sizing:border-box;width:100%;min-height:5em;font:inherit}#triptych{display:block;max-width:100%;height:auto}
#comparison{margin-top:20px}#progress{font-weight:600}
@media(max-width:1100px){body.clone main{grid-template-columns:repeat(2,minmax(0,1fr))}body.clone aside{grid-column:1/-1;position:static;max-height:none;overflow:visible}}
@media(max-width:760px){main,body.clone main{grid-template-columns:minmax(0,1fr)}aside,body.clone aside{position:static;max-height:none;overflow:visible;grid-column:auto}}
</style>
<h1 id="title">Explore this phase</h1><main><section id="reference-pane" hidden>
<h2>Frozen original</h2><img id="reference" alt="Selected original checkpoint" draggable="false"></section><section>
<h2 id="live-title" hidden>Live clone</h2>
<label for="preview-size">Preview size <input id="preview-size" type="range" min="320" max="960" step="20" value="600">
<output id="size-value" for="preview-size">600 px</output></label>
<img id="screen" alt="Waiting for emulator screen" draggable="false">
</section><aside><h2>Look for these states</h2><ol id="guide"></ol>
<p id="original-help">Click or drag on the preview to explore. Pauses save screens automatically;
use <strong>Save screen</strong> to bookmark one. Backtracking is fine.</p>
<p id="progress" hidden></p><p id="clone-help" hidden>Navigate the live clone to the shown state. Capture once it settles, then review the comparison below. You can retake any checkpoint.</p>
<button id="back">Android Back</button><button id="save">Save screen</button>
<div id="clone-controls" hidden><label for="notes">Notes (optional)</label><textarea id="notes" maxlength="1000"></textarea>
<button id="compare">Capture &amp; compare</button><button id="previous">Previous checkpoint</button><button id="next">Next</button></div>
<button id="done">Done</button>
<form id="typing"><input id="text" aria-label="Text to enter" placeholder="Text to enter" maxlength="500">
<button>Type</button></form><p id="message" role="status"></p><p id="counts"></p>
<p><a id="saved-files" target="_blank" rel="noopener noreferrer">Saved files</a>
<output id="saved-path"></output></p>
</aside></main><section id="comparison" hidden><h2>Comparison</h2><img id="triptych" alt="Original, clone, and difference comparison"></section><script>
const q='?token='+encodeURIComponent(__TOKEN__),$=id=>document.getElementById(id);
let busy=false,finished=false,down=null,blobUrl=null,referenceUrl=null,comparisonUrl=null,shownCheckpoint=null;
$('preview-size').oninput=e=>{const width=e.target.value;
$('screen').style.setProperty('--preview-width',width+'px');
$('reference').style.setProperty('--preview-width',width+'px');$('size-value').textContent=width+' px'};
async function api(path,data){const r=await fetch('/api/'+path+q,data===undefined?{}:
{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)});
const v=await r.json();if(!r.ok)throw Error(v.error||r.statusText);return v}
async function run(path,data){if(busy){$('message').textContent='Capture or input is still running. Wait for the result.';return}
if(finished)return;busy=true;$('screen').classList.add('busy');
$('message').textContent='Sending…';
try{const v=await api(path,data);finished=!!v.finished;
$('message').textContent=finished?'Saved. Tell the AI you are done.':path==='save'?
'Bookmark requested. Pause briefly on this screen.':path==='compare'?
'Comparison saved. Review it below or capture this checkpoint again.':path==='move'?
'Checkpoint changed.':'Input sent. Wait for the screen to respond.';
if(path==='compare'||path==='move')shownCheckpoint=null;
refresh()}
catch(e){$('message').textContent=e.message}
finally{busy=false;$('screen').classList.remove('busy')}}
function point(e){const r=$('screen').getBoundingClientRect();return {
x:Math.min($('screen').naturalWidth-1,Math.max(0,Math.round((e.clientX-r.left)*$('screen').naturalWidth/r.width))),
y:Math.min($('screen').naturalHeight-1,Math.max(0,Math.round((e.clientY-r.top)*$('screen').naturalHeight/r.height)))}}
$('screen').onpointerdown=e=>{if(busy||finished||!$('screen').naturalWidth)return;
down={...point(e),at:performance.now()};$('screen').setPointerCapture(e.pointerId)};
$('screen').onpointerup=e=>{if(!down)return;const a=down,b=point(e);down=null;
const ms=Math.min(3000,Math.max(100,Math.round(performance.now()-a.at)));
run('action',Math.hypot(b.x-a.x,b.y-a.y)>12||ms>500?
{action:'swipe',x:a.x,y:a.y,end_x:b.x,end_y:b.y,duration_ms:ms}:{action:'tap',x:a.x,y:a.y})};
$('screen').onpointercancel=()=>down=null;
$('back').onclick=()=>run('action',{action:'back'});$('save').onclick=()=>run('save',{});
$('done').onclick=()=>run('finish',{});
$('compare').onclick=()=>run('compare',{notes:$('notes').value});
$('previous').onclick=()=>run('move',{direction:-1});$('next').onclick=()=>run('move',{direction:1});
$('typing').onsubmit=e=>{e.preventDefault();run('action',{action:'type',text:$('text').value})};
let refreshTimer=null,refreshing=false;
async function refresh(){if(refreshing)return;refreshing=true;clearTimeout(refreshTimer);try{const v=await api('status');finished=v.finished;
const clone=v.role==='clone';document.body.classList.toggle('clone',clone);
$('title').textContent=clone?'Compare this phase':'Explore this phase';
$('reference-pane').hidden=!clone;$('live-title').hidden=!clone;
$('original-help').hidden=clone;$('clone-help').hidden=!clone;
$('clone-controls').hidden=!clone;$('progress').hidden=!clone;$('save').hidden=clone;
if(!$('guide').children.length)for(const c of v.checkpoints){const li=document.createElement('li');
li.textContent=c.capture_when||c.setup||c.id;li.dataset.checkpoint=c.id;$('guide').append(li)}
for(const li of $('guide').children){li.style.fontWeight=li.dataset.checkpoint===v.current_checkpoint?'700':'400';
li.style.color=v.compared.includes(li.dataset.checkpoint)?'#245f39':''}
$('progress').textContent='Checkpoint '+(v.checkpoints.findIndex(c=>c.id===v.current_checkpoint)+1)+
' of '+v.checkpoints.length+' · '+v.compared.length+' compared';
$('previous').disabled=v.current_checkpoint===v.checkpoints[0].id;
$('next').disabled=v.current_checkpoint===v.checkpoints[v.checkpoints.length-1].id;
if(clone&&shownCheckpoint!==v.current_checkpoint){shownCheckpoint=v.current_checkpoint;
const r=await fetch('/api/reference'+q);if(r.ok){const next=URL.createObjectURL(await r.blob());
$('reference').src=next;if(referenceUrl)URL.revokeObjectURL(referenceUrl);referenceUrl=next}
$('notes').value='';$('comparison').hidden=true;
const t=await fetch('/api/triptych'+q);if(t.ok){const next=URL.createObjectURL(await t.blob());
$('triptych').src=next;$('comparison').hidden=false;
if(comparisonUrl)URL.revokeObjectURL(comparisonUrl);comparisonUrl=next}}
$('counts').textContent=v.saved+' screenshots · '+v.xml_saved+' XML · '+v.actions+' inputs'+
(v.warning?' · '+v.warning:'');
const folder=v.capture_active?v.staging_dir:v.export_dir;
$('saved-path').textContent=folder||'No capture folder yet';
if(folder)$('saved-files').href='/api/files'+q;
if(!down){const r=await fetch('/api/preview'+q);if(r.ok){const next=URL.createObjectURL(await r.blob());
$('screen').src=next;if(blobUrl)URL.revokeObjectURL(blobUrl);blobUrl=next}}
}catch(e){$('counts').textContent=e.message}
finally{refreshing=false;if(!finished)refreshTimer=setTimeout(refresh,700)}}refresh();
</script></html>'''


class Recorder:
    def __init__(self, mobile, contract_path, lock=None, checkpoint_ids=None,
                 role='original', project_path=None, phase_cli_path=None):
        self.mobile = mobile
        self.lock = lock or threading.RLock()
        self.contract = json.loads(Path(contract_path).read_text(encoding='utf-8'))
        checkpoints = self.contract.get('checkpoints')
        if not isinstance(checkpoints, list) or not checkpoints:
            raise ValueError('phase contract has no checkpoints')
        ids = [item['id'] for item in checkpoints]
        if len(set(ids)) != len(ids):
            raise ValueError('phase contract has duplicate checkpoint IDs')
        if checkpoint_ids is not None:
            if (not isinstance(checkpoint_ids, list) or not checkpoint_ids
                    or len(set(checkpoint_ids)) != len(checkpoint_ids)
                    or any(key not in ids for key in checkpoint_ids)):
                raise ValueError('checkpoint_ids contains unknown, duplicate, or no checkpoints')
            checkpoints = [item for item in checkpoints if item['id'] in checkpoint_ids]
        self.checkpoints = checkpoints
        if role not in ('original', 'clone'):
            raise ValueError('recorder role must be original or clone')
        self.role = role
        self.current_index = 0
        self.comparisons = {}
        self.capture_in_progress = False
        self.project_path = Path(project_path).expanduser().resolve() if project_path else None
        self.phase_cli_path = Path(phase_cli_path).expanduser().resolve() if phase_cli_path else None
        self.references = {}
        if role == 'clone':
            if self.project_path is None or self.phase_cli_path is None or not self.phase_cli_path.is_file():
                raise ValueError('clone recorder needs project_path and phase_cli_path')
            phase = self.project_path / 'phases' / self.contract['phase_id']
            original = phase / 'original'
            manifest_bytes = (original / 'manifest.json').read_bytes()
            status = json.loads((phase / 'status.json').read_text(encoding='utf-8'))
            if (status.get('state') not in ('oracle_frozen', 'implementing', 'comparing',
                                             'correcting', 'automated_ready', 'human_accepted') or
                    status.get('original_manifest_sha256') != sha256(manifest_bytes).hexdigest()):
                raise ValueError('original evidence is not frozen or its manifest changed')
            manifest = json.loads(manifest_bytes)
            for checkpoint in checkpoints:
                matching = [item for item in manifest.get('artifacts', []) if
                            item.get('checkpoint_id') == checkpoint['id'] and item.get('kind') == 'png']
                if len(matching) != 1:
                    raise ValueError(f"frozen original missing PNG for {checkpoint['id']}")
                artifact = matching[0]
                path = (original / artifact['path']).resolve()
                if path.parent != original.resolve() or not path.is_file():
                    raise ValueError('frozen original path escapes original folder')
                png = path.read_bytes()
                if sha256(png).hexdigest() != artifact['sha256']:
                    raise ValueError('frozen original PNG hash changed')
                self.references[checkpoint['id']] = (png, artifact['sha256'])
        self.shots = []
        self.events = []
        self.finished = False
        self.closed = False
        self.stopped = False
        self.token = secrets.token_urlsafe(24)
        self.server = self.thread = self.worker = self.display = None
        self.halt = threading.Event()
        self.preview = None
        self.preview_at = None
        self.preview_revision = -1
        self.stable_frames = 0
        self.revision = 0
        self.last_input = time.monotonic()
        self.bookmark = False
        self.warning = ''
        self.output = mobile.output

    def status(self):
        return {'phase_id': self.contract['phase_id'], 'saved': len(self.shots),
                'role': self.role,
                'current_checkpoint': self.checkpoints[self.current_index]['id'],
                'compared': sorted(self.comparisons),
                'xml_saved': sum(shot.get('xml') is not None for shot in self.shots),
                'actions': len(self.events), 'finished': self.finished,
                'warning': self.warning, 'capture_active': self.mobile.stage is not None,
                'staging_dir': str(self.mobile.stage) if self.mobile.stage is not None else None,
                'export_dir': str(self.output),
                'checkpoints': [{'id': c['id'], 'setup': c.get('setup'),
                                 'capture_when': c.get('capture_when')} for c in self.checkpoints]}

    def _active(self):
        if self.finished or self.closed or self.mobile.stage is None:
            raise ValueError('no active recorder; start a new walkthrough')

    def _journal(self, value):
        with (self.mobile.stage / 'actions.jsonl').open('a', encoding='utf-8') as stream:
            stream.write(json.dumps(value) + '\n')
            stream.flush()
            os.fsync(stream.fileno())

    def action(self, payload):
        with self.lock:
            self._active()
            if self.capture_in_progress:
                raise ValueError('comparison capture is still running')
            fields = {'tap': {'x', 'y'}, 'swipe': {'x', 'y', 'end_x', 'end_y', 'duration_ms'},
                      'type': {'text'}, 'back': set()}
            kind = payload.get('action')
            if kind not in fields or set(payload) != fields[kind] | {'action'}:
                raise ValueError('use tap, swipe, type, or back with their required arguments')
            if kind == 'type' and (not isinstance(payload['text'], str) or len(payload['text']) > 500):
                raise ValueError('text must be at most 500 characters')
            for key in fields[kind] - {'text'}:
                if type(payload[key]) is not int or not 0 <= payload[key] <= 30000:
                    raise ValueError('coordinates/duration must be integers in 0..30000')
            number = len(self.events) + 1
            event = {'number': number, 'input': payload, 'at': datetime.now(timezone.utc).isoformat()}
            self._journal({**event, 'status': 'requested'})
            self.revision += 1  # Even a failed transport may have changed device state.
            try:
                event['executed'] = self.mobile.perform(**payload)
                event['status'] = 'executed'
            except Exception as error:
                event.update(status='uncertain', error=str(error))
                raise
            finally:
                self.events.append(event)
                self._journal(event)
                self.last_input = time.monotonic()
            return {'number': number, 'status': event['status']}

    def save(self):
        with self.lock:
            self._active()
            if self.role == 'clone':
                raise ValueError('use Capture & compare in clone walkthrough')
            if len(self.shots) >= 100:
                raise ValueError('100-screen limit reached; finish this batch')
            self.bookmark = True
            return {'queued': True}

    def _manifest(self):
        payload = {'schema_version': 1, 'kind': 'ditto_exploration',
                   'eligible_for_phase': False, 'phase_id': self.contract['phase_id'],
                   'target': self.mobile.target, 'environment': self.mobile.environment,
                   'package_name': self.mobile.package_name,
                   'installed_package_sha256': self.mobile.package_sha,
                   'mcp_receipt': self.mobile.receipt, 'action_log': 'actions.jsonl',
                   'replay_status': 'unverified', 'checkpoints': self.checkpoints,
                   'role': self.role,
                   'shots': self.shots}
        temporary = self.mobile.stage / 'exploration.tmp'
        temporary.write_text(json.dumps(payload, indent=2) + '\n', encoding='utf-8')
        os.replace(temporary, self.mobile.stage / 'exploration.json')

    @staticmethod
    def select_candidates(exploration_dir, selections):
        """Promote reviewed, intact human captures without pretending they were replayed."""
        folder = Path(exploration_dir).expanduser().resolve(strict=True)
        pack = json.loads((folder / 'exploration.json').read_text(encoding='utf-8'))
        if pack.get('kind') != 'ditto_exploration':
            raise ValueError('selection needs a finished human exploration')
        if not isinstance(selections, list) or not selections:
            raise ValueError('selection needs at least one checkpoint')
        identifiers = [item.get('checkpoint_id') for item in selections if isinstance(item, dict)]
        if len(identifiers) != len(selections) or len(set(identifiers)) != len(identifiers):
            raise ValueError('selection has duplicate or invalid checkpoint IDs')
        allowed = {item['id'] for item in pack['checkpoints']}
        journal = folder / pack['action_log']
        if journal.resolve().parent != folder or not journal.is_file():
            raise ValueError('action journal is missing or outside exploration')
        journal_bytes = journal.read_bytes()
        latest = {}
        for line in journal_bytes.splitlines():
            event = json.loads(line)
            if isinstance(event.get('number'), int):
                latest[event['number']] = event
        chosen = []
        for item in selections:
            checkpoint_id = item['checkpoint_id']
            number = item.get('candidate_number')
            observed_state = item.get('observed_state')
            if checkpoint_id not in allowed or type(number) is not int or not isinstance(observed_state, str) or not observed_state.strip():
                raise ValueError('selection needs a known checkpoint, candidate number and observed state')
            shot = next((shot for shot in pack['shots'] if shot['number'] == number), None)
            if shot is None:
                raise ValueError(f'candidate {number} is missing')
            if shot.get('warning', '').startswith('Android ANR'):
                raise ValueError(f'candidate {number} shows an Android ANR dialog')
            paths = {}
            for kind in ('png', 'xml'):
                name = shot.get(kind)
                if name is None:
                    paths[kind] = None
                    continue
                path = (folder / name).resolve()
                if path.parent != folder or not path.is_file():
                    raise ValueError(f'candidate {number} {kind} is missing or outside exploration')
                if sha256(path.read_bytes()).hexdigest() != shot[kind + '_sha256']:
                    raise ValueError(f'candidate {number} {kind} hash changed')
                paths[kind] = str(path)
            chosen.append({'checkpoint_id': checkpoint_id, 'candidate_number': number,
                           'png': paths['png'], 'png_sha256': shot['png_sha256'],
                           'xml': paths['xml'], 'xml_sha256': shot.get('xml_sha256'),
                           'captured_at': shot['captured_at'], 'after_input': shot['after_input'],
                           'observed_state': observed_state.strip(),
                           'observed_actions': [latest[key] for key in sorted(latest)
                                                if key <= shot['after_input']]})
        receipt = pack['mcp_receipt']
        payload = {'schema_version': 1, 'kind': 'ditto_selected_original',
                   'provenance': 'mcp', 'server': receipt.get('server'),
                   'tool': receipt.get('tool'), 'session_id': receipt.get('session_id'),
                   'phase_id': pack['phase_id'], 'package_name': pack['package_name'],
                   'installed_package_sha256': pack['installed_package_sha256'],
                   'target': pack['target'], 'environment': pack['environment'],
                   'mcp_receipt': receipt,
                   'action_log': {'path': str(journal), 'sha256': sha256(journal_bytes).hexdigest()},
                   'selections': chosen}
        temporary = folder / 'selected-candidate.tmp'
        temporary.write_text(json.dumps(payload, indent=2) + '\n', encoding='utf-8')
        os.replace(temporary, folder / 'selected-candidate.json')
        return {'selection_path': str(folder / 'selected-candidate.json'),
                'selected': [item['checkpoint_id'] for item in chosen]}

    def _sample(self, force_manual=False):
        with self.lock:
            revision = self.revision
            png = self.preview
            if (self.finished or self.closed or png is None
                    or self.preview_revision != revision
                    or time.monotonic() - self.last_input < 1.2):
                return
            manual = self.bookmark or force_manual
            if self.role == 'clone' and not manual:
                return
            if not manual and self.stable_frames < 2:
                return
            if len(self.shots) >= 100:
                self.warning = '100-screen limit reached; finish this batch.'
                return
            fingerprint = sha256(png).hexdigest()
            if not manual and self.shots and fingerprint == self.shots[-1]['png_sha256']:
                return
        # Slow hierarchy capture never holds the input lock. If navigation occurs
        # concurrently, discard this sample instead of joining different states.
        xml, warning = None, ''
        try:
            xml = self.mobile._hierarchy(timeout=8)
            for node in ET.fromstring(xml).iter('node'):
                title = node.get('text', '').lower().replace('’', "'")
                if (node.get('resource-id') == 'android:id/alertTitle'
                        and ("isn't responding" in title or 'not responding' in title)):
                    warning = 'Android ANR dialog captured; finish and diagnose before replay.'
        except Exception as error:
            xml = None
            warning = f'XML unavailable: {error}'
        with self.lock:
            if self.finished or self.closed or revision != self.revision:
                return
            if self.preview != png:
                xml = None
                warning = 'Screen changed during XML capture; review image and recapture XML if needed.'
            number = len(self.shots) + 1
            stem = f'{number:03d}'
            (self.mobile.stage / f'{stem}.png').write_bytes(png)
            shot = {'number': number, 'png': f'{stem}.png', 'png_sha256': fingerprint,
                    'after_input': revision, 'manual': manual,
                    'captured_at': datetime.now(timezone.utc).isoformat(),
                    'xml': None, 'warning': warning}
            if xml is not None:
                (self.mobile.stage / f'{stem}.xml').write_bytes(xml)
                shot.update(xml=f'{stem}.xml', xml_sha256=sha256(xml).hexdigest())
            self.shots.append(shot)
            self.bookmark = False
            self.warning = warning
            self._manifest()
            return shot

    def reference_png(self):
        if self.role != 'clone':
            raise ValueError('original walkthrough has no frozen reference')
        return self.references[self.checkpoints[self.current_index]['id']][0]

    def triptych_png(self):
        if self.role != 'clone':
            raise ValueError('original walkthrough has no comparison')
        checkpoint_id = self.checkpoints[self.current_index]['id']
        path = self.comparisons.get(checkpoint_id, {}).get('triptych')
        if not path:
            raise ValueError('Capture & compare this checkpoint first')
        file = Path(path).resolve()
        phase = (self.project_path / 'phases' / self.contract['phase_id'] / 'diff').resolve()
        if file.parent != phase or not file.is_file():
            raise ValueError('comparison image is unavailable')
        return file.read_bytes()

    def move_checkpoint(self, direction):
        with self.lock:
            self._active()
            if self.capture_in_progress:
                raise ValueError('comparison capture is still running')
            if self.role != 'clone':
                raise ValueError('checkpoint navigation is only for clone comparison')
            if direction not in (-1, 1) or not 0 <= self.current_index + direction < len(self.checkpoints):
                raise ValueError('no checkpoint in that direction')
            self.current_index += direction
            return {'checkpoint_id': self.checkpoints[self.current_index]['id']}

    def capture_and_compare(self, notes=''):
        with self.lock:
            self._active()
            if self.role != 'clone':
                raise ValueError('Capture & compare is only for the clone walkthrough')
            if self.capture_in_progress:
                raise ValueError('comparison capture is already running')
            if not isinstance(notes, str) or len(notes) > 1000:
                raise ValueError('notes must be at most 1000 characters')
            self.mobile._identity(self.mobile.serial, self.mobile.target['id'])
            if self.mobile._environment() != self.mobile.environment:
                raise ValueError('emulator environment changed; begin a new clone capture')
            self.mobile._assert_installed_apk(self.mobile.package_name, self.mobile.package_sha)
            self.mobile._assert_app_focus(self.mobile.package_name)
            if (self.preview is None or self.preview_revision != self.revision
                    or self.stable_frames < 2 or self.preview_at is None
                    or time.monotonic() - self.preview_at > 3
                    or time.monotonic() - self.last_input < 1.2):
                raise ValueError('wait for the live screen to settle before Capture & compare')
            if len(self.shots) >= 100:
                raise ValueError('100-screen limit reached; finish this batch')
            checkpoint = self.checkpoints[self.current_index]
            checkpoint_id = checkpoint['id']
            self.capture_in_progress = True
        try:
            shot = self._sample(force_manual=True)
            if shot is None:
                raise ValueError('screen changed; wait for it to settle and retry')
            if 'ANR' in shot['warning'] or 'Screen changed' in shot['warning']:
                raise ValueError(shot['warning'])
            with self.lock:
                original_hash = self.references[checkpoint_id][1]
                journal = self.mobile.stage / 'actions.jsonl'
                receipt = self.mobile.receipt
                selection = {'checkpoint_id': checkpoint_id, 'candidate_number': shot['number'],
                             'png': str(self.mobile.stage / shot['png']),
                             'png_sha256': shot['png_sha256'],
                             'xml': str(self.mobile.stage / shot['xml']) if shot['xml'] else None,
                             'xml_sha256': shot.get('xml_sha256'),
                             'captured_at': shot['captured_at'], 'after_input': shot['after_input'],
                             'notes': notes.strip(),
                             'requested_state': checkpoint.get('capture_when') or checkpoint_id,
                             'observation_source': 'human_candidate',
                             'observed_actions': list(self.events),
                             'original_png_sha256': original_hash}
                request = {'schema_version': 1, 'kind': 'ditto_selected_clone',
                           'provenance': 'mcp', 'server': receipt.get('server'),
                           'tool': receipt.get('tool'), 'session_id': receipt.get('session_id'),
                           'phase_id': self.contract['phase_id'],
                           'package_name': self.mobile.package_name,
                           'installed_package_sha256': self.mobile.package_sha,
                           'target': self.mobile.target, 'environment': self.mobile.environment,
                           'mcp_receipt': receipt,
                           'action_log': {'path': str(journal),
                                          'sha256': sha256(journal.read_bytes()).hexdigest()},
                           'selections': [selection]}
                request_path = self.mobile.stage / 'comparison-request.json'
                temporary = self.mobile.stage / 'comparison-request.tmp'
                temporary.write_text(json.dumps(request, indent=2) + '\n', encoding='utf-8')
                os.replace(temporary, request_path)
            uv = shutil.which('uv')
            if not uv:
                raise ValueError('uv is required to run the Ditto comparison')
            result = subprocess.run([uv, 'run', '--project', str(self.phase_cli_path.parent.parent),
                                     '--locked', 'python', str(self.phase_cli_path), 'phase',
                                     'capture-clone', self.contract['phase_id'], '--project',
                                     str(self.project_path), '--selection', str(request_path),
                                     '--apk', str(self.mobile.apk_path)],
                                    capture_output=True, text=True, timeout=180)
            if result.returncode:
                raise ValueError(result.stderr.strip() or 'clone comparison failed')
            comparison = json.loads(result.stdout)
            if comparison.get('checkpoint_id') != checkpoint_id or not comparison.get('ok'):
                raise ValueError('comparison returned the wrong checkpoint or failed')
            with self.lock:
                self.comparisons[checkpoint_id] = comparison
            return comparison
        finally:
            with self.lock:
                self.capture_in_progress = False

    def _refresh(self):
        while not self.halt.is_set():
            try:
                with self.lock:
                    revision = self.revision
                png = self.mobile._screen(timeout=5)
                with self.lock:
                    if revision == self.revision:
                        self.stable_frames = self.stable_frames + 1 if png == self.preview else 1
                        self.preview, self.preview_revision = png, revision
                        self.preview_at = time.monotonic()
            except Exception as error:
                with self.lock:
                    self.warning = str(error)
            if self.halt.wait(0.7):
                break

    def _collect(self):
        failures = 0
        while not self.halt.is_set():
            try:
                self._sample()
                failures = 0
            except Exception as error:
                failures += 1
                with self.lock:
                    self.warning = str(error)
            if self.halt.wait(min(5, 0.7 * (failures + 1))):
                break

    def finish(self):
        with self.lock:
            if not self.finished:
                if self.mobile.stage is None:
                    raise ValueError('no active capture')
                if self.capture_in_progress:
                    raise ValueError('comparison capture is still running')
                if self.bookmark:
                    raise ValueError('bookmark is still saving; pause briefly before Done')
                if not self.shots:
                    raise ValueError('wait for a screen to save before Done')
                self._manifest()
                backup = None
                if self.output.exists():
                    if getattr(self.mobile, 'replace_output', False) is not True:
                        raise ValueError('existing walkthrough requires replace_output')
                    self.mobile._validate_replaceable_exploration(
                        self.output, self.mobile.package_name, self.mobile.target)
                    backup = Path(tempfile.mkdtemp(prefix='.previous-walkthrough-', dir=self.output.parent))
                    backup.rmdir()
                    os.replace(self.output, backup)
                try:
                    os.replace(self.mobile.stage, self.output)
                except Exception:
                    if backup is not None:
                        os.replace(backup, self.output)
                    raise
                if backup is not None:
                    shutil.rmtree(backup, ignore_errors=True)
                self.mobile.stage = self.mobile.output = None
                self.finished = True
                self.halt.set()
        self._join_workers()
        with self.lock:
            self.mobile.abort()
        return {'finished': True, 'export_dir': str(self.output), 'saved': len(self.shots),
                'eligible_for_phase': False}

    def _join_workers(self):
        for worker in (self.worker, self.display):
            if worker is not None:
                worker.join(timeout=15)
                if worker.is_alive():
                    raise RuntimeError('capture worker is still stopping; retry finish/stop')

    def start(self):
        self._active()
        if self.mobile.records:
            raise ValueError('start exploration in a fresh capture, without verified checkpoints')
        if self.server is not None:
            return self.url
        recorder = self
        # Keep setup actions performed before the panel, including declared resets.
        self._journal({'status': 'initial', 'actions': list(self.mobile.actions),
                       'fixture_note': 'begin launches existing app data; reset only if explicitly prepared'})
        self._manifest()

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def _authorized(self):
                return parse_qs(urlparse(self.path).query).get('token', [''])[0] == recorder.token

            def _send(self, code, data, mime='application/json', download_name=None):
                payload = json.dumps(data).encode() if mime == 'application/json' else data
                self.send_response(code)
                self.send_header('Content-Type', mime)
                self.send_header('Content-Length', str(len(payload)))
                self.send_header('Cache-Control', 'no-store')
                self.send_header('Referrer-Policy', 'no-referrer')
                self.send_header('X-Content-Type-Options', 'nosniff')
                if download_name is not None:
                    self.send_header('Content-Disposition', f'attachment; filename="{download_name}"')
                self.end_headers()
                self.wfile.write(payload)

            def _saved_files(self):
                folder = recorder.mobile.stage if recorder.mobile.stage is not None else (
                    recorder.output if recorder.finished else None)
                if folder is None:
                    return None, []
                names = {'actions.jsonl', 'exploration.json', 'comparison-request.json'}
                for shot in recorder.shots:
                    names.add(shot['png'])
                    if shot['xml']:
                        names.add(shot['xml'])
                root = folder.resolve()
                available = sorted(name for name in names
                                   if name == Path(name).name
                                   and (folder / name).resolve().parent == root
                                   and (folder / name).is_file())
                return folder, available

            def do_GET(self):
                if not self._authorized():
                    return self._send(403, {'error': 'invalid recorder token'})
                path = urlparse(self.path).path
                with recorder.lock:
                    if path == '/':
                        return self._send(200, PANEL.replace('__TOKEN__', json.dumps(recorder.token)).encode(),
                                          'text/html; charset=utf-8')
                    if path == '/api/status':
                        return self._send(200, recorder.status())
                    if path == '/api/preview' and recorder.preview:
                        return self._send(200, recorder.preview, 'image/png')
                    if path == '/api/reference' and recorder.role == 'clone':
                        return self._send(200, recorder.reference_png(), 'image/png')
                    if path == '/api/triptych' and recorder.role == 'clone':
                        try:
                            return self._send(200, recorder.triptych_png(), 'image/png')
                        except ValueError:
                            return self._send(404, {'error': 'comparison image unavailable'})
                    if path in ('/api/files', '/api/file'):
                        folder, names = self._saved_files()
                        if folder is None:
                            return self._send(404, {'error': 'capture folder unavailable'})
                        if path == '/api/files':
                            links = ''.join('<li><a href="/api/file?token='
                                            + quote(recorder.token, safe='') + '&amp;name='
                                            + quote(name, safe='') + '">' + escape(name) + '</a></li>'
                                            for name in names)
                            page = ('<!doctype html><html lang="en"><meta charset="utf-8">'
                                    '<title>Saved files</title><h1>Saved files</h1><p>'
                                    + escape(str(folder)) + '</p><ul>' + links + '</ul></html>')
                            return self._send(200, page.encode(), 'text/html; charset=utf-8')
                        requested = parse_qs(urlparse(self.path).query).get('name', [])
                        if len(requested) != 1 or requested[0] not in names:
                            return self._send(404, {'error': 'unknown saved file'})
                        name = requested[0]
                        mime = {'.png': 'image/png', '.xml': 'application/xml',
                                '.json': 'application/json', '.jsonl': 'application/x-ndjson'}[Path(name).suffix]
                        return self._send(200, (folder / name).read_bytes(), mime, name)
                self._send(404, {'error': 'screen not ready or unknown route'})

            def do_POST(self):
                if not self._authorized():
                    return self._send(403, {'error': 'invalid recorder token'})
                try:
                    length = int(self.headers.get('Content-Length', '0'))
                    if not 0 < length <= 65536:
                        raise ValueError('invalid request body size')
                    payload = json.loads(self.rfile.read(length))
                    if not isinstance(payload, dict):
                        raise ValueError('request must be an object')
                    path = urlparse(self.path).path
                    if path == '/api/action':
                        result = recorder.action(payload)
                    elif path == '/api/save':
                        result = recorder.save()
                    elif path == '/api/finish':
                        result = recorder.finish()
                    elif path == '/api/compare':
                        if set(payload) != {'notes'}:
                            raise ValueError('compare accepts only notes')
                        result = recorder.capture_and_compare(payload['notes'])
                    elif path == '/api/move':
                        if set(payload) != {'direction'} or type(payload['direction']) is not int:
                            raise ValueError('move needs an integer direction')
                        result = recorder.move_checkpoint(payload['direction'])
                    else:
                        return self._send(404, {'error': 'unknown route'})
                    self._send(200, result)
                except Exception as error:
                    self._send(400, {'error': str(error)})

        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.worker = threading.Thread(target=self._collect, daemon=True)
        self.display = threading.Thread(target=self._refresh, daemon=True)
        self.thread.start()
        self.display.start()
        self.worker.start()
        return self.url

    @property
    def url(self):
        return f'http://127.0.0.1:{self.server.server_port}/?token={self.token}'

    def stop(self):
        with self.lock:
            self.closed = True
            self.bookmark = False
            self.halt.set()
        if self.server is not None:
            self.server.shutdown()
            self.server.server_close()
            self.thread.join(timeout=3)
            self.server = self.thread = None
        self._join_workers()
        self.stopped = True
