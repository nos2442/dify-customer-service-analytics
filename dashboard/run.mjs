import { existsSync } from 'node:fs'
import path from 'node:path'
import os from 'node:os'
import { fileURLToPath } from 'node:url'
import { spawnSync } from 'node:child_process'

const root = path.dirname(fileURLToPath(import.meta.url))
const action = process.argv[2] ?? 'dev'
if (!['dev', 'build', 'preview', 'test'].includes(action)) {
  throw new Error('Use start.cmd [dev|build|preview|test].')
}
const [major, minor] = process.versions.node.split('.').map(Number)
if (major < 22 || (major === 22 && minor < 12)) throw new Error('Install Node.js 22.12+ or Node.js 24 LTS.')
const env = { ...process.env, PATH: path.dirname(process.execPath) + path.delimiter + process.env.PATH }
function run(args) {
  const result = spawnSync(process.execPath, args, { cwd: root, env, stdio: 'inherit' })
  if (result.error) throw result.error
  if (result.status !== 0) process.exit(result.status ?? 1)
}
const vite = path.join(root, 'node_modules/vite/bin/vite.js')
if (!existsSync(vite)) {
  const pnpm = path.join(os.homedir(), '.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/pnpm/bin/pnpm.cjs')
  const npm = path.join(path.dirname(process.execPath), 'node_modules/npm/bin/npm-cli.js')
  if (existsSync(pnpm)) run([pnpm, 'install', '--frozen-lockfile'])
  else if (existsSync(npm)) run([npm, 'install'])
  else throw new Error('Install Node.js with npm, then run npm install in this folder.')
}
if (action === 'test') run(['--test', 'tests/charts.test.js'])
else if (action === 'build') run([vite, 'build'])
else run([vite, ...(action === 'preview' ? ['preview'] : []), '--host', '127.0.0.1',
  '--port', action === 'preview' ? '4173' : '5173', '--strictPort'])
