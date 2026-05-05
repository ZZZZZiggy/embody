const os = require('os');
const path = require('path');
const express = require('express');
const { SerialPort } = require('serialport');
const { ReadlineParser } = require('@serialport/parser-readline');

const HTTP_PORT = 3000;
const BAUD_RATE = 9600;
const LINE_RE = /Distance:\s*([\d.]+)\s*cm.*STATUS:\s*(ON|OFF)/;

const app = express();
app.use(express.static(path.join(__dirname, 'public')));

let latest = { status: 0, distance: 0, ts: Date.now() };
const clients = new Set();

app.get('/events', (req, res) => {
  res.set({
    'Content-Type': 'text/event-stream',
    'Cache-Control': 'no-cache, no-transform',
    'Connection': 'keep-alive',
    'X-Accel-Buffering': 'no',
  });
  res.flushHeaders();
  res.write(`data: ${JSON.stringify(latest)}\n\n`);
  clients.add(res);
  req.on('close', () => clients.delete(res));
});

function broadcast(state) {
  const payload = `data: ${JSON.stringify(state)}\n\n`;
  for (const c of clients) c.write(payload);
}

async function findArduinoPort() {
  const ports = await SerialPort.list();
  const match = ports.find(p =>
    /usbmodem|usbserial|wchusb|tty\.usb/i.test(p.path) ||
    /Arduino|wch|silicon/i.test(p.manufacturer || '')
  );
  if (!match) {
    console.error('[serial] No Arduino-like port found. Available:');
    ports.forEach(p => console.error('  -', p.path, '|', p.manufacturer || '(no manufacturer)'));
    console.error('Set SERIAL_PORT env var to override, e.g. SERIAL_PORT=/dev/cu.usbmodem1101 npm start');
    return null;
  }
  return match.path;
}

function getLocalIPs() {
  const out = [];
  const ifaces = os.networkInterfaces();
  for (const name of Object.keys(ifaces)) {
    for (const iface of ifaces[name]) {
      if (iface.family === 'IPv4' && !iface.internal) out.push(iface.address);
    }
  }
  return out;
}

async function startSerial() {
  const portPath = process.env.SERIAL_PORT || await findArduinoPort();
  if (!portPath) return;

  const port = new SerialPort({ path: portPath, baudRate: BAUD_RATE });
  const parser = port.pipe(new ReadlineParser({ delimiter: '\n' }));

  port.on('open', () => console.log(`[serial] Connected: ${portPath} @ ${BAUD_RATE}`));
  port.on('error', (err) => console.error('[serial] Error:', err.message));
  port.on('close', () => console.warn('[serial] Port closed'));

  parser.on('data', (line) => {
    const m = line.match(LINE_RE);
    if (!m) return;
    const distance = parseFloat(m[1]);
    const status = m[2] === 'ON' ? 1 : 0;
    if (status !== latest.status || Math.abs(distance - latest.distance) > 0.1) {
      latest = { status, distance, ts: Date.now() };
      broadcast(latest);
    }
  });
}

app.listen(HTTP_PORT, () => {
  console.log(`[http]   Local:   http://localhost:${HTTP_PORT}`);
  for (const ip of getLocalIPs()) {
    console.log(`[http]   Phone:   http://${ip}:${HTTP_PORT}`);
  }
});

startSerial();
