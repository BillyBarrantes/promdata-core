#!/usr/bin/env node
import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import { execSync } from 'node:child_process';

const BASELINE_FILE = path.join(process.cwd(), 'scratch', '.ui_safety_baseline.json');
const FORBIDDEN_DIRS = [
  'backend',
  'lib',
  'hooks',
  'supabase',
];
const FORBIDDEN_ROOT_FILES = [
  'cloudbuild.yaml',
  'cloudbuild.worker.yaml',
  'Dockerfile',
  'docker-compose.yml',
  'docker-compose.prod.yml',
];

function getFileHash(filePath) {
  try {
    const buffer = fs.readFileSync(filePath);
    return crypto.createHash('sha1').update(buffer).digest('hex');
  } catch {
    return null;
  }
}

function collectForbiddenFiles(dirPath, fileList = []) {
  if (!fs.existsSync(dirPath)) return fileList;
  const entries = fs.readdirSync(dirPath, { withFileTypes: true });
  for (const entry of entries) {
    if (entry.name === 'node_modules' || entry.name === '.git' || entry.name === '__pycache__' || entry.name === '.pytest_cache' || entry.name === '.DS_Store') continue;
    const fullPath = path.join(dirPath, entry.name);
    if (entry.isDirectory()) {
      collectForbiddenFiles(fullPath, fileList);
    } else if (entry.isFile()) {
      const relPath = path.relative(process.cwd(), fullPath);
      fileList.push(relPath);
    }
  }
  return fileList;
}

function buildCurrentFingerprint() {
  const files = [];
  for (const dir of FORBIDDEN_DIRS) {
    collectForbiddenFiles(path.join(process.cwd(), dir), files);
  }
  for (const rootFile of FORBIDDEN_ROOT_FILES) {
    if (fs.existsSync(path.join(process.cwd(), rootFile))) {
      files.push(rootFile);
    }
  }

  const fingerprint = {};
  for (const file of files) {
    const fullPath = path.join(process.cwd(), file);
    const hash = getFileHash(fullPath);
    if (hash) {
      fingerprint[file] = hash;
    }
  }
  return fingerprint;
}

const args = process.argv.slice(2);
const isInit = args.includes('--init') || !fs.existsSync(BASELINE_FILE);

if (isInit) {
  if (!fs.existsSync(path.join(process.cwd(), 'scratch'))) {
    fs.mkdirSync(path.join(process.cwd(), 'scratch'), { recursive: true });
  }
  const fingerprint = buildCurrentFingerprint();
  fs.writeFileSync(BASELINE_FILE, JSON.stringify(fingerprint, null, 2), 'utf-8');
  console.log('🛡️  [PROMDATA FORTRESS] Punto de referencia de seguridad inicializado exitosamente.');
  console.log(`📁 Registrados ${Object.keys(fingerprint).length} archivos protegidos del cerebro (backend, lib, hooks, supabase).\n`);
  console.log('A partir de ahora, ejecuta "npm run verify:ui" para comprobar que Open Design no haya tocado ningún archivo protegido.\n');
  process.exit(0);
}

console.log('🛡️  [PROMDATA FORTRESS] Verificando blindaje del cerebro y contratos...\n');

// 1. Comparar contra el baseline
let baseline = {};
try {
  baseline = JSON.parse(fs.readFileSync(BASELINE_FILE, 'utf-8'));
} catch (error) {
  console.warn('⚠️  No se pudo leer el archivo baseline, regenerándolo...');
}

const currentFingerprint = buildCurrentFingerprint();
const modifiedProtectedFiles = [];

for (const [file, originalHash] of Object.entries(baseline)) {
  const currentHash = currentFingerprint[file];
  if (!currentHash) {
    modifiedProtectedFiles.push(`${file} (ELIMINADO)`);
  } else if (currentHash !== originalHash) {
    modifiedProtectedFiles.push(`${file} (MODIFICADO)`);
  }
}

// Comprobar archivos nuevos creados en zonas prohibidas
for (const file of Object.keys(currentFingerprint)) {
  if (!baseline[file]) {
    modifiedProtectedFiles.push(`${file} (NUEVO ARCHIVO EN ZONA PROTEGIDA)`);
  }
}

if (modifiedProtectedFiles.length > 0) {
  console.error('🚨 [VIOLACIÓN DE BLINDAJE DETECTADA]');
  console.error('Se detectaron alteraciones en archivos protegidos del cerebro/lógica:');
  modifiedProtectedFiles.forEach((file) => console.error(`  ❌ ${file}`));
  console.error('\nPara descartar los cambios no deseados en esos archivos:');
  console.error('git checkout HEAD -- [archivos afectados]\n');
  process.exit(1);
}

console.log('✅ Archivos protegidos del cerebro: 100% intactos (0 alteraciones en backend, lib, hooks ni supabase).');

// 2. Verificar integridad de TypeScript
console.log('🔍 Verificando contratos de componentes y props con TypeScript...');
try {
  execSync('node ./node_modules/typescript/bin/tsc --noEmit', { stdio: 'inherit' });
  console.log('✅ Compilación TypeScript: 0 errores (contratos de props y handlers 100% estables).\n');
} catch {
  console.error('\n❌ [ERROR DE CONTRATOS] Se detectaron errores de compilación TypeScript. Algún botón o prop fue alterado.');
  process.exit(1);
}

// 3. Resumen de archivos visuales modificados
try {
  const statOutput = execSync('git diff --stat -- components/ app/ styles/ public/ ":!**/.DS_Store" ":!*.DS_Store"', { encoding: 'utf-8' }).trim();
  if (statOutput) {
    console.log('🎨 Resumen de cambios visuales detectados:');
    console.log(statOutput);
  }
} catch {
  // Ignorar si falla diff parcial
}

console.log('\n🎉 [BLINDAJE CONFIRMADO] La interfaz es segura y el cerebro de PromData está 100% protegido.\n');
