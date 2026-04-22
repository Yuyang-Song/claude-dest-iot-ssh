import * as vscode from 'vscode';
import { BridgeManager, BridgeStatus, BridgeHealth } from './bridgeManager';

type ToWebview =
  | { type: 'update'; status: BridgeStatus; health: BridgeHealth | null; logs: string[] }
  | { type: 'log'; line: string };

type FromWebview =
  | { type: 'start' }
  | { type: 'stop' }
  | { type: 'restart' }
  | { type: 'setPort'; port: string }
  | { type: 'setHardware'; brightness?: number; led?: boolean; sound?: boolean }
  | { type: 'setSpecies'; idx: number };

export class BuddyPanelProvider implements vscode.WebviewViewProvider {
  public static readonly viewId = 'claudeBuddy.panel';
  private view?: vscode.WebviewView;

  constructor(private readonly bridge: BridgeManager) {}

  resolveWebviewView(view: vscode.WebviewView): void {
    this.view = view;
    view.webview.options = { enableScripts: true };
    view.webview.html = this._html(view.webview);

    // Forward bridge events to webview.
    this.bridge.onStatusChange(() => this._push());
    this.bridge.onLog(line => {
      const msg: ToWebview = { type: 'log', line };
      view.webview.postMessage(msg);
    });

    // Handle messages from webview.
    view.webview.onDidReceiveMessage((msg: FromWebview) => {
      switch (msg.type) {
        case 'start':   this.bridge.start(); break;
        case 'stop':    this.bridge.stop();  break;
        case 'restart': this.bridge.restart(); break;
        case 'setPort': {
          const cfg = vscode.workspace.getConfiguration('claudeBuddy');
          cfg.update('port', msg.port, vscode.ConfigurationTarget.Global);
          break;
        }
        case 'setHardware': {
          const { brightness, led, sound } = msg;
          this.bridge.setHardware({ brightness, led, sound });
          break;
        }
        case 'setSpecies': {
          this.bridge.setSpecies(msg.idx);
          break;
        }
      }
    });

    // Send initial state.
    this._push();
  }

  refresh(): void { this._push(); }

  private _push(): void {
    if (!this.view) { return; }
    const msg: ToWebview = {
      type: 'update',
      status: this.bridge.status,
      health: this.bridge.health,
      logs: this.bridge.logs.slice(-40),
    };
    this.view.webview.postMessage(msg);
  }

  private _html(_webview: vscode.Webview): string {
    return `<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline';">
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body {
    font-family: var(--vscode-font-family);
    font-size: var(--vscode-font-size);
    color: var(--vscode-foreground);
    background: var(--vscode-sideBar-background);
    padding: 10px;
    display: flex;
    flex-direction: column;
    gap: 10px;
  }
  .row { display: flex; align-items: center; gap: 6px; flex-wrap: wrap; }
  .dot {
    width: 10px; height: 10px; border-radius: 50%;
    background: var(--vscode-descriptionForeground);
    flex-shrink: 0;
  }
  .dot.connected    { background: #4caf50; }
  .dot.waiting      { background: #ff9800; }
  .dot.disconnected { background: #f44336; }
  .dot.starting     { background: #2196f3; }
  .dot.stopped      { background: #9e9e9e; }
  .status-text { font-weight: 600; }
  .meta { color: var(--vscode-descriptionForeground); font-size: 0.88em; }
  .alert {
    display: none;
    font-size: 0.82em;
    color: var(--vscode-errorForeground, #f48771);
    border: 1px solid var(--vscode-errorForeground, #f48771);
    border-radius: 2px;
    padding: 4px 6px;
    background: color-mix(in srgb, var(--vscode-editor-background) 85%, #f48771 15%);
  }
  .alert.show { display: block; }
  label { color: var(--vscode-descriptionForeground); font-size: 0.85em; }
  input[type=text] {
    background: var(--vscode-input-background);
    color: var(--vscode-input-foreground);
    border: 1px solid var(--vscode-input-border, transparent);
    border-radius: 2px;
    padding: 3px 6px;
    font-size: 0.9em;
    flex: 1;
    min-width: 0;
  }
  button {
    background: var(--vscode-button-background);
    color: var(--vscode-button-foreground);
    border: none;
    border-radius: 2px;
    padding: 4px 10px;
    cursor: pointer;
    font-size: 0.88em;
    white-space: nowrap;
  }
  button:hover { background: var(--vscode-button-hoverBackground); }
  button.secondary {
    background: var(--vscode-button-secondaryBackground);
    color: var(--vscode-button-secondaryForeground);
  }
  button.secondary:hover { background: var(--vscode-button-secondaryHoverBackground); }
  .log-area {
    font-family: var(--vscode-editor-font-family, monospace);
    font-size: 0.8em;
    color: var(--vscode-terminal-foreground, var(--vscode-foreground));
    background: var(--vscode-terminal-background, var(--vscode-editor-background));
    border: 1px solid var(--vscode-panel-border, transparent);
    border-radius: 2px;
    padding: 6px;
    height: 200px;
    overflow-y: auto;
    white-space: pre-wrap;
    word-break: break-all;
  }
  hr { border: none; border-top: 1px solid var(--vscode-panel-border, #444); }
  .hw-section { display: flex; flex-direction: column; gap: 6px; }
  .hw-row { display: flex; align-items: center; gap: 6px; }
  .hw-label { color: var(--vscode-descriptionForeground); font-size: 0.85em; width: 52px; flex-shrink: 0; }
  .bright-dots { display: flex; gap: 3px; cursor: pointer; }
  .bright-dot {
    width: 12px; height: 12px; border-radius: 50%;
    background: var(--vscode-descriptionForeground);
    opacity: 0.25;
    transition: opacity 0.1s;
  }
  .bright-dot.on { opacity: 1; }
  .toggle-btn {
    padding: 2px 8px;
    font-size: 0.82em;
    opacity: 0.5;
  }
  .toggle-btn.active {
    opacity: 1;
    outline: 1px solid var(--vscode-focusBorder, #007fd4);
  }
</style>
</head>
<body>

<div class="row">
  <div class="dot stopped" id="dot"></div>
  <span class="status-text" id="statusText">stopped</span>
</div>
<div class="meta" id="metaText">—</div>
<div class="alert" id="alertText"></div>

<hr>

<div class="row">
  <label>Port</label>
  <input type="text" id="portInput" placeholder="auto" value="auto">
  <button onclick="setPort()">Set</button>
</div>

<div class="row">
  <button onclick="send('start')">Start</button>
  <button onclick="send('restart')">Restart</button>
  <button class="secondary" onclick="send('stop')">Stop</button>
</div>

<hr>

<div class="hw-section">
  <label>Hardware</label>
  <div class="hw-row">
    <span class="hw-label">Brightness</span>
    <div class="bright-dots" id="brightDots">
      <div class="bright-dot" data-v="0" onclick="setBright(0)"></div>
      <div class="bright-dot" data-v="1" onclick="setBright(1)"></div>
      <div class="bright-dot" data-v="2" onclick="setBright(2)"></div>
      <div class="bright-dot" data-v="3" onclick="setBright(3)"></div>
      <div class="bright-dot" data-v="4" onclick="setBright(4)"></div>
    </div>
  </div>
  <div class="hw-row">
    <span class="hw-label">LED</span>
    <button class="toggle-btn active" id="ledOn"  onclick="setLed(true)">ON</button>
    <button class="toggle-btn"        id="ledOff" onclick="setLed(false)">OFF</button>
  </div>
  <div class="hw-row">
    <span class="hw-label">Sound</span>
    <button class="toggle-btn active" id="sndOn"  onclick="setSound(true)">ON</button>
    <button class="toggle-btn"        id="sndOff" onclick="setSound(false)">OFF</button>
  </div>
  <div class="hw-row">
    <span class="hw-label">Pet</span>
    <select id="speciesSel" onchange="setSpecies(parseInt(this.value))" style="flex:1;background:var(--vscode-input-background);color:var(--vscode-input-foreground);border:1px solid var(--vscode-input-border,transparent);border-radius:2px;padding:2px 4px;font-size:0.85em;">
      <option value="0">Capybara</option>
      <option value="1">Duck</option>
      <option value="2">Goose</option>
      <option value="3">Blob</option>
      <option value="4">Cat</option>
      <option value="5">Dragon</option>
      <option value="6">Octopus</option>
      <option value="7">Owl</option>
      <option value="8">Penguin</option>
      <option value="9">Turtle</option>
      <option value="10">Snail</option>
      <option value="11">Ghost</option>
      <option value="12">Axolotl</option>
      <option value="13">Cactus</option>
      <option value="14">Robot</option>
      <option value="15">Rabbit</option>
      <option value="16">Mushroom</option>
      <option value="17">Chonk</option>
    </select>
  </div>
</div>

<hr>

<label>Log</label>
<div class="log-area" id="log"></div>

<script>
const vscode = acquireVsCodeApi();

function send(type, extra) {
  vscode.postMessage(Object.assign({ type }, extra || {}));
}

function setPort() {
  const port = document.getElementById('portInput').value.trim() || 'auto';
  send('setPort', { port });
}

let _bright = 4, _led = true, _sound = true;
let _alertTimer = 0;

function _renderBright() {
  document.querySelectorAll('.bright-dot').forEach(el => {
    el.classList.toggle('on', parseInt(el.dataset.v) <= _bright);
  });
}
function _renderToggle(onId, offId, val) {
  document.getElementById(onId).classList.toggle('active', val);
  document.getElementById(offId).classList.toggle('active', !val);
}

function setBright(v) {
  _bright = v; _renderBright();
  send('setHardware', { brightness: v });
}
function setLed(v) {
  _led = v; _renderToggle('ledOn', 'ledOff', v);
  send('setHardware', { led: v });
}
function setSound(v) {
  _sound = v; _renderToggle('sndOn', 'sndOff', v);
  send('setHardware', { sound: v });
}
function setSpecies(idx) {
  send('setSpecies', { idx });
}

function showAlert(text) {
  const el = document.getElementById('alertText');
  el.textContent = text;
  el.classList.add('show');
  if (_alertTimer) { clearTimeout(_alertTimer); }
  _alertTimer = setTimeout(() => {
    el.classList.remove('show');
    el.textContent = '';
    _alertTimer = 0;
  }, 3000);
}

_renderBright();

const STATUS_LABELS = {
  stopped:      'stopped',
  starting:     'starting…',
  connected:    'connected',
  disconnected: 'no device',
  waiting:      'waiting approval…',
};

window.addEventListener('message', e => {
  const msg = e.data;
  if (msg.type === 'update') {
    const { status, health } = msg;
    const dot = document.getElementById('dot');
    const statusText = document.getElementById('statusText');
    const metaText = document.getElementById('metaText');

    dot.className = 'dot ' + status;
    statusText.textContent = status === 'waiting' && health && health.waiting > 1
      ? health.waiting + ' waiting…'
      : STATUS_LABELS[status] || status;

    if (health) {
      const parts = [];
      if (health.mode) { parts.push('mode: ' + health.mode); }
      if (health.serial_connected) { parts.push('serial: ok'); }
      metaText.textContent = parts.join('  ·  ') || '—';
    } else {
      metaText.textContent = '—';
    }

    // Replace log content on full update.
    const logEl = document.getElementById('log');
    logEl.textContent = msg.logs.join('\\n');
    logEl.scrollTop = logEl.scrollHeight;
  } else if (msg.type === 'log') {
    const logEl = document.getElementById('log');
    logEl.textContent += (logEl.textContent ? '\\n' : '') + msg.line;
    if (msg.line.startsWith('[control]')) {
      showAlert(msg.line.replace('[control] ', ''));
    }
    // Trim to last 200 lines.
    const lines = logEl.textContent.split('\\n');
    if (lines.length > 200) { logEl.textContent = lines.slice(-200).join('\\n'); }
    logEl.scrollTop = logEl.scrollHeight;
  }
});
</script>
</body>
</html>`;
  }
}
