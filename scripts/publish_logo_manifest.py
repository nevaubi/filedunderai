"""Publish a locally reviewed logo manifest; credentials come from the environment."""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from etl.common import Supabase
from etl.logos import publish_logo_assets, _png_size


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', required=True)
    parser.add_argument('--receipt', required=True)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    path = Path(args.manifest).resolve()
    manifest = json.loads(path.read_text())
    entries = []
    for item in manifest['entries']:
        asset = Path(item['path'])
        if not asset.is_absolute():
            asset = path.parent / asset
        entry = {key: value for key, value in item.items() if key != 'path'}
        entry['body'] = asset.read_bytes()
        _png_size(entry['body'])
        entries.append(entry)
    if not args.apply:
        print(json.dumps({'status': 'staged', 'entries': len(entries)}))
        return
    receipt = publish_logo_assets(Supabase(), entries)
    receipt['held'] = manifest.get('held', {})
    receipt_path = Path(args.receipt)
    receipt_path.parent.mkdir(parents=True, exist_ok=True)
    receipt_path.write_text(json.dumps(receipt, indent=2))
    print(json.dumps({'status': receipt['status'], 'counts': receipt['counts'],
                      'verified_at': receipt['verified_at']}, indent=2))


if __name__ == '__main__':
    main()
