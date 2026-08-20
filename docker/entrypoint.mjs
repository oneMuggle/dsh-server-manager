import { spawn } from 'node:child_process'

const children = [
  spawn(process.execPath, [
    '/opt/deepseek-harness/apps/cli/lib/bin.js',
    'web',
    '--no-open',
  ], { stdio: 'inherit' }),
  spawn('/usr/sbin/nginx', [
    '-c', '/etc/nginx/nginx.conf',
    '-g', 'daemon off;',
  ], { stdio: 'inherit' }),
]

const closed = children.map(child => new Promise((resolve) => {
  child.once('error', (error) => {
    console.error(`failed to start ${child.spawnfile}:`, error)
  })
  child.once('close', (code, signal) => {
    resolve({ child, code, signal })
  })
}))

let shutdownSignal

function stopChildren(signal = 'SIGTERM') {
  for (const child of children) {
    if (child.exitCode === null && child.signalCode === null) child.kill(signal)
  }
}

for (const signal of ['SIGINT', 'SIGTERM']) {
  process.on(signal, () => {
    if (shutdownSignal !== undefined) return
    shutdownSignal = signal
    stopChildren(signal)
  })
}

const first = await Promise.race(closed)
if (shutdownSignal === undefined) stopChildren()

const forceTimer = setTimeout(() => { stopChildren('SIGKILL') }, 8_000)
forceTimer.unref()
await Promise.all(closed)
clearTimeout(forceTimer)

process.exitCode = shutdownSignal === undefined
  ? first.code ?? 1
  : 0
