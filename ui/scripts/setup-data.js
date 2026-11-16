import fs from 'fs'
import path from 'path'
import { fileURLToPath } from 'url'

const __dirname = path.dirname(fileURLToPath(import.meta.url))
const projectRoot = path.resolve(__dirname, '../..')
const sourceFile = path.join(projectRoot, 'raw.json')
const publicDir = path.join(__dirname, '../public')
const destFile = path.join(publicDir, 'raw.json')

try {
  // Create public directory if it doesn't exist
  if (!fs.existsSync(publicDir)) {
    fs.mkdirSync(publicDir, { recursive: true })
  }

  // Read the source file
  const data = fs.readFileSync(sourceFile, 'utf-8')

  // Write to public directory
  fs.writeFileSync(destFile, data, 'utf-8')

  console.log('✓ Synced raw.json to public directory')
} catch (error) {
  console.error('✗ Failed to sync raw.json:', error.message)
  process.exit(1)
}
