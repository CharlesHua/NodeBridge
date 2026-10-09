/* The local WebChannel carries PTY bytes as decoded UTF-8 strings. */
(() => {
  const terminal = new Terminal({
    cursorBlink: true,
    fontFamily: 'Consolas, "Cascadia Mono", monospace',
    fontSize: 14,
    scrollback: 10000,
    theme: {
      background: '#0c0c0c', foreground: '#e1eaf4', cursor: '#dcecff',
      black: '#1b1f23', red: '#cd3131', green: '#00a800', yellow: '#c9a227',
      blue: '#2458bd', magenta: '#bc3fbc', cyan: '#20a4a4', white: '#d8dee9',
      brightBlack: '#697782', brightRed: '#f87171', brightGreen: '#00e500',
      brightYellow: '#e3c64a', brightBlue: '#247bff', brightMagenta: '#d783e8',
      brightCyan: '#56d4d4', brightWhite: '#ffffff'
    }
  });
  const fit = new FitAddon.FitAddon();
  terminal.loadAddon(fit);
  terminal.open(document.getElementById('terminal'));
  window.nodebridgeTerminal = terminal;
  let bridge = null;
  let lastSize = '';

  function fitTerminal() {
    const container = document.getElementById('terminal');
    if (!container.clientWidth || !container.clientHeight) return;
    fit.fit();
    reportSize();
  }

  function reportSize() {
    const size = `${terminal.cols}x${terminal.rows}`;
    if (bridge && size !== lastSize) {
      lastSize = size;
      bridge.resize(terminal.cols, terminal.rows);
    }
  }

  terminal.onResize(reportSize);
  new ResizeObserver(fitTerminal).observe(document.getElementById('terminal'));
  terminal.attachCustomKeyEventHandler(event => {
    if (event.type !== 'keydown') return true;
    if (event.ctrlKey && event.shiftKey && event.code === 'KeyC') {
      if (bridge && terminal.hasSelection()) bridge.copy(terminal.getSelection());
      return false;
    }
    if (event.ctrlKey && event.code === 'KeyV') {
      if (bridge) bridge.paste(text => { if (text) terminal.paste(text); });
      return false;
    }
    if (event.ctrlKey && (event.code === 'Equal' || event.code === 'NumpadAdd' ||
                          event.code === 'Minus' || event.code === 'NumpadSubtract' ||
                          event.code === 'Digit0')) {
      if (event.code === 'Digit0') terminal.options.fontSize = 14;
      else terminal.options.fontSize = Math.max(8, Math.min(32,
        terminal.options.fontSize + (event.code === 'Minus' || event.code === 'NumpadSubtract' ? -1 : 1)));
      fitTerminal();
      return false;
    }
    return true;
  });

  new QWebChannel(qt.webChannelTransport, channel => {
    bridge = channel.objects.terminalBridge;
    bridge.output.connect(data => terminal.write(data));
    bridge.reset.connect(() => terminal.reset());
    bridge.inputEnabled.connect(enabled => { terminal.options.disableStdin = !enabled; });
    bridge.fitRequested.connect(fitTerminal);
    bridge.focusRequested.connect(() => terminal.focus());
    terminal.onData(data => bridge.input(data));
    terminal.element.addEventListener('focusin', () => bridge.focused());
    terminal.onBell(() => bridge.bell());
    fitTerminal();
    bridge.ready(terminal.cols, terminal.rows);
  });
})();
