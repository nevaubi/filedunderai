// Explicit versioned migrations only. Application builds never run this script.
// The official Supabase CLI owns migration history and connection security.
import { spawnSync } from 'node:child_process';

const args = process.argv.slice(2);
const usage = `Database migrations are separate from application builds.

Install the official Supabase CLI, initialize its local configuration if needed,
and link the intended project before using this command.

  npm run migrate             Preview pending linked migrations (dry run).
  npm run migrate -- --apply  Apply the reviewed pending migrations.

Only supabase/migrations/*.sql is managed here. db/schema.sql is a legacy schema
reference and must not be replayed over an existing production database.
https://supabase.com/docs/guides/deployment/database-migrations`;

if (args.includes('--help') || args.includes('-h')) {
  console.log(usage);
} else if (args.some(arg => arg !== '--apply') || args.length > 1) {
  console.error(usage);
  process.exitCode = 2;
} else {
  const result = spawnSync('supabase', ['db', 'push', '--linked', ...(args.includes('--apply') ? [] : ['--dry-run'])], {
    stdio: 'inherit', shell: false,
  });
  if (result.error) {
    console.error(result.error.code === 'ENOENT'
      ? 'The Supabase CLI is not installed. Install it using the official documentation, or apply the reviewed migration through the connected Supabase migration tool.'
      : `Could not start the Supabase CLI (${result.error.code || 'unknown error'}).`);
    process.exitCode = 1;
  } else {
    process.exitCode = result.status ?? 1;
  }
}
