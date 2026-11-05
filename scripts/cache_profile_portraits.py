"""Copy sourced public portraits to Storage without changing profile JSON.

Downloads and uploads are bounded and never automatically retried. The atomic
manifest preserves attribution, content hashes, delivery errors, and progress.
Run with FU_ENV_FILE pointing to the existing server-only credentials file.
"""
import argparse
import concurrent.futures
import fcntl
import hashlib
import io
import json
import os
from pathlib import Path
import struct
import sys
import urllib.error
import urllib.parse
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from etl.common import Supabase, now_iso

MAX_BYTES = 4 * 1024 * 1024
# Explicitly approved exception for the existing NVIDIA investor-relations photo.
SIZE_EXCEPTIONS = {
    'https://s201.q4cdn.com/141608511/files/images/founders/nvidia-jensenhuang-headshots-js0400-c-w-lightened2x2.png': 8 * 1024 * 1024,
}
MIME_EXCEPTIONS = {
    'https://storage.googleapis.com/gweb-uniblog-publish-prod/images/sundar-208x208.width-400.format-webp.webp': 'https://blog.google/authors/sundar-pichai/',
    'https://storage.googleapis.com/gweb-uniblog-publish-prod/images/Ruth_Porat_1_VDuadKa.width-400.format-webp.webp': 'https://blog.google/authors/ruth-porat/',
}
USER_AGENT = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
              'AppleWebKit/537.36 (KHTML, like Gecko) '
              'Chrome/131.0.0.0 Safari/537.36')


class PortraitError(ValueError):
    def __init__(self, message, details=None):
        super().__init__(message)
        self.details = details or {}


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def public_url(value):
    if not isinstance(value, str):
        raise PortraitError('missing_source_url')
    parsed = urllib.parse.urlsplit(value)
    if (parsed.scheme not in ('http', 'https') or not parsed.hostname
            or parsed.username or parsed.password or parsed.hostname == 'localhost'):
        raise PortraitError('invalid_source_url')
    return value


def image_format(data, content_type, max_bytes=MAX_BYTES, binary_webp=False):
    """Require an image MIME and real raster bytes; store the detected MIME."""
    mime = content_type.split(';', 1)[0].strip().lower()
    if len(data) < 16 or len(data) > max_bytes:
        raise PortraitError('invalid_image_size')
    detected = None
    if data[:3] == b'\xff\xd8\xff' and b'\xff\xd9' in data[-64:]:
        detected = ('jpg', 'image/jpeg', {'image/jpeg', 'image/jpg', 'image/pjpeg'})
    elif (data[:8] == b'\x89PNG\r\n\x1a\n' and data[12:16] == b'IHDR'
          and b'IEND' in data[-32:] and len(data) >= 33):
        width, height = struct.unpack('>II', data[16:24])
        if width and height:
            detected = ('png', 'image/png', {'image/png', 'image/x-png'})
    elif (data[:4] == b'RIFF' and data[8:12] == b'WEBP'
          and data[12:16] in (b'VP8 ', b'VP8L', b'VP8X')
          and struct.unpack('<I', data[4:8])[0] + 8 == len(data)):
        detected = ('webp', 'image/webp', {'image/webp'})
    elif data[:6] in (b'GIF87a', b'GIF89a') and data[-1:] == b';':
        detected = ('gif', 'image/gif', {'image/gif'})
    elif data[4:8] == b'ftyp' and any(brand in data[8:64] for brand in (b'avif', b'avis')):
        detected = ('avif', 'image/avif', {'image/avif'})
    # Some official CDNs negotiate WebP while retaining the original PNG MIME.
    # Both signals must identify raster images; the bytes determine storage MIME.
    allowed_mimes = {'image/jpeg', 'image/jpg', 'image/pjpeg', 'image/png',
                     'image/x-png', 'image/webp', 'image/gif', 'image/avif'}
    allowed_binary = binary_webp and mime == 'application/octet-stream' and detected and detected[0] == 'webp'
    if not detected or (mime not in allowed_mimes and not allowed_binary):
        raise PortraitError('image_content_type_or_signature_mismatch')
    return detected[:2]


def read_image(response, max_bytes=MAX_BYTES, binary_webp=False):
    if response.status != 200:
        raise PortraitError('unexpected_image_status_' + str(response.status))
    declared = response.headers.get('Content-Length')
    content_type = response.headers.get('Content-Type', '')
    if declared and int(declared) > max_bytes:
        raise PortraitError('image_exceeds_size_limit', {'content_type': content_type,
                            'declared_bytes': int(declared), 'size_limit_bytes': max_bytes})
    data = response.read(max_bytes + 1)
    if len(data) > max_bytes:
        raise PortraitError('image_exceeds_size_limit', {'size_limit_bytes': max_bytes})
    try:
        ext, mime = image_format(data, content_type, max_bytes, binary_webp=binary_webp)
    except PortraitError as error:
        raise PortraitError(str(error), {'content_type': content_type,
                            'received_bytes': len(data), 'signature_hex': data[:16].hex()}) from None
    return data, ext, mime


def download(url, source_page, timeout):
    public_url(url)
    parsed = urllib.parse.urlsplit(url)
    # The scraped Amgen page resolves its /-/media assets below /about/.
    # The same named official asset is served from the root /-/media path.
    if parsed.hostname == 'www.amgen.com' and parsed.path.startswith('/about/-/media/'):
        parsed = parsed._replace(path=parsed.path.removeprefix('/about'))
        fetch_url = urllib.parse.urlunsplit(parsed)
    else:
        fetch_url = url
    headers = {'User-Agent': USER_AGENT,
               'Accept': 'image/avif,image/webp,image/apng,image/*,*/*;q=0.8'}
    if source_page:
        headers['Referer'] = public_url(source_page)
    request = urllib.request.Request(fetch_url, headers=headers)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        binary_webp = MIME_EXCEPTIONS.get(url, '').rstrip('/') == str(source_page).rstrip('/')
        data, ext, mime = read_image(response, SIZE_EXCEPTIONS.get(url, MAX_BYTES), binary_webp=binary_webp)
        if binary_webp:
            # These two exact official Google biography assets have a generic
            # binary header. Require full raster decoding as well as WebP magic.
            try:
                from PIL import Image
                with Image.open(io.BytesIO(data)) as decoded:
                    if decoded.format != 'WEBP':
                        raise PortraitError('binary_exception_is_not_webp')
                    decoded.load()
            except ImportError:
                raise PortraitError('pillow_required_for_binary_webp_exception') from None
            except (OSError, ValueError):
                raise PortraitError('binary_webp_decode_failed') from None
        public_url(response.url)
        return data, ext, mime, response.url


def upload_verified(sb, path, data, mime, timeout, check_existing=False):
    encoded = urllib.parse.quote(path, safe='/')
    endpoint = sb.url + '/storage/v1/object/public-data/' + encoded
    cached_url = sb.url + '/storage/v1/object/public/public-data/' + encoded
    expected_hash = hashlib.sha256(data).digest()
    if check_existing:
        # A timed-out POST can have completed. Verify the deterministic public
        # object before sending the same bytes again.
        try:
            with urllib.request.urlopen(cached_url, timeout=timeout) as response:
                existing, _, _ = read_image(response, max(MAX_BYTES, len(data)))
            if hashlib.sha256(existing).digest() == expected_hash:
                return cached_url, True
        except urllib.error.HTTPError as error:
            # Supabase returns 400 or 404 for an absent public Storage object.
            if error.code not in (400, 404):
                raise
    request = urllib.request.Request(endpoint, data=data, method='POST', headers=sb._h({
        'Content-Type': mime, 'x-upsert': 'true', 'Cache-Control': 'public, max-age=604800'}))
    # Credentials are sent only to the configured Storage endpoint.
    with urllib.request.build_opener(NoRedirect).open(request, timeout=timeout) as response:
        if response.status not in (200, 201):
            raise PortraitError('storage_upload_status_' + str(response.status))
        response.read(16384)
    # Verify public delivery with GET and an exact byte hash, without credentials.
    with urllib.request.urlopen(cached_url, timeout=timeout) as response:
        readback, _, _ = read_image(response, max(MAX_BYTES, len(data)))
    if hashlib.sha256(readback).digest() != expected_hash:
        raise PortraitError('storage_readback_hash_mismatch')
    return cached_url, False


def collect(profiles):
    photos = {}
    files = sorted(Path(profiles).glob('*.json'))
    for path in files:
        if path.name == 'index.json':
            continue
        profile = json.loads(path.read_text())
        for person in profile.get('leadership', []):
            url = person.get('photo_original_url') or person.get('photo_url')
            if not url:
                continue
            public_url(url)
            reference = {key: value for key, value in {
                'ticker': profile.get('ticker'), 'name': person.get('name'),
                'photo_source_url': person.get('photo_source_url') or person.get('source_url'),
                'photo_credit': person.get('photo_credit'),
                'photo_license': person.get('photo_license'),
                'photo_license_url': person.get('photo_license_url'),
            }.items() if value}
            if not reference.get('photo_source_url'):
                raise PortraitError('portrait_missing_attribution')
            photos.setdefault(url, []).append(reference)
    return photos


def save(path, manifest):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.tmp')
    with temporary.open('w') as handle:
        json.dump(manifest, handle, ensure_ascii=False, indent=2)
        handle.write('\n')
        handle.flush()
        os.fsync(handle.fileno())
    temporary.replace(path)


def timeout_failure(row):
    if row.get('error') == 'TimeoutError':
        return True
    if row.get('error') != 'URLError':
        return False
    details = row.get('error_details') or {}
    # Legacy rows predate stage logging; their Storage timeout was diagnosed
    # with a bounded source-download/upload probe. New rows must say timeout.
    if not details and not row.get('error_stage'):
        return True
    reason = str(details.get('reason', '')).lower()
    return details.get('reason_type') in ('TimeoutError', 'timeout') or 'timed out' in reason


def portrait_identities(references):
    return {(str(ref.get('ticker', '')).upper(),
             ' '.join(str(ref.get('name', '')).casefold().split())) for ref in references}


def cache_one(sb, url, references, timeout, storage_timeout, check_existing=False):
    row = {'source_url': url, 'references': references, 'attempted_at': now_iso(),
           'size_limit_bytes': SIZE_EXCEPTIONS.get(url, MAX_BYTES)}
    stage = 'source_download'
    try:
        data, ext, mime, fetched_url = download(url, references[0]['photo_source_url'], timeout)
        source_hash = hashlib.sha256(url.encode()).hexdigest()
        path = 'company-portraits/' + source_hash[:24] + '.' + ext
        stage = 'storage_upload_and_readback'
        cached_url, reused = upload_verified(sb, path, data, mime, storage_timeout,
                                             check_existing=check_existing)
        row.update(status='cached', cached_url=cached_url, storage_path=path,
                   source_url_sha256=source_hash, sha256=hashlib.sha256(data).hexdigest(),
                   fetched_url=fetched_url, content_type=mime, bytes=len(data),
                   verified_at=now_iso(), public_readback='sha256_matched',
                   storage_reused_existing=reused)
        if url in MIME_EXCEPTIONS:
            row['raster_validation'] = 'Pillow full WebP decode; exact official biography source'
    except urllib.error.HTTPError as error:
        row.update(status='error', error='http_' + str(error.code), error_stage=stage)
    except urllib.error.URLError as error:
        reason = error.reason
        row.update(status='error', error='URLError', error_stage=stage,
                   error_details={'reason_type': type(reason).__name__,
                                  'reason': str(reason)[:200]})
    except PortraitError as error:
        row.update(status='error', error=str(error), error_details=error.details,
                   error_stage=stage)
    except (OSError, ValueError) as error:
        row.update(status='error', error=type(error).__name__, error_stage=stage)
    return row


def run(args):
    photos = collect(args.profiles)
    if len(photos) > args.max_images:
        raise ValueError('Portrait count exceeds the explicit image limit')
    sb = Supabase()
    manifest = json.loads(args.manifest.read_text()) if args.manifest.exists() else {
        'schema_version': 1, 'created_at': now_iso(), 'entries': {}}
    if manifest.get('schema_version') != 1:
        raise ValueError('Unsupported portrait-cache manifest')
    entries = manifest['entries']
    pending = [(url, refs) for url, refs in sorted(photos.items()) if url not in entries
               # A generic official asset URL can later belong to a successor.
               # Never reuse cached bytes for a different named person blindly.
               or (entries[url].get('status') == 'cached' and
                   portrait_identities(entries[url].get('references', [])) != portrait_identities(refs))
               or (entries[url].get('status') == 'error' and
                   (args.retry_errors or (args.retry_confirmed_fixes and
                    url in set(SIZE_EXCEPTIONS) | set(MIME_EXCEPTIONS)) or
                    (args.retry_transport_errors and
                     timeout_failure(entries[url]) and
                     entries[url].get('transport_retry_count', 0) < 1)))]
    manifest.update(source_portraits=len(photos), max_image_bytes=MAX_BYTES,
                    size_exceptions=SIZE_EXCEPTIONS,
                    binary_webp_exceptions=MIME_EXCEPTIONS,
                    max_workers=args.workers, timeout_seconds=args.timeout,
                    storage_timeout_seconds=args.storage_timeout)
    print(json.dumps({'portraits': len(photos), 'pending': len(pending),
                      'already_recorded': len(photos) - len(pending)}), flush=True)
    def update():
        active = [entries[url] for url in photos if url in entries]
        manifest['updated_at'] = now_iso()
        manifest['counts'] = {'source_portraits': len(photos),
                              'cached': sum(row.get('status') == 'cached' for row in active),
                              'errors': sum(row.get('status') == 'error' for row in active),
                              'pending': len(photos) - len(active),
                              'cached_bytes': sum(row.get('bytes', 0) for row in active)}
        save(args.manifest, manifest)
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(cache_one, sb, url, refs, args.timeout,
                               args.storage_timeout, url in entries): url
                   for url, refs in pending}
        for index, future in enumerate(concurrent.futures.as_completed(futures), 1):
            url = futures[future]
            previous = entries.get(url, {})
            entry = future.result()
            retry_count = previous.get('transport_retry_count', 0)
            if previous.get('status') == 'error' and timeout_failure(previous):
                retry_count += 1
                entry['previous_transport_error'] = {
                    key: previous[key] for key in ('error', 'error_stage', 'error_details', 'attempted_at')
                    if key in previous}
            if retry_count:
                entry['transport_retry_count'] = retry_count
            entries[url] = entry
            update()
            if index % 20 == 0 or entries[url]['status'] == 'error':
                print(json.dumps({'completed': index, **manifest['counts'],
                                  'latest_status': entries[url]['status'],
                                  'latest_tickers': sorted({r['ticker'] for r in entries[url]['references']}),
                                  'latest_error': entries[url].get('error')}), flush=True)
    update()
    print(json.dumps({'manifest': str(args.manifest), **manifest['counts']}), flush=True)
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--profiles', type=Path, default=ROOT / 'public/data/company-profiles')
    parser.add_argument('--manifest', type=Path, default=ROOT / 'cache/company-profile-backfill/portrait-cache.json')
    parser.add_argument('--max-images', type=int, default=500)
    parser.add_argument('--workers', type=int, default=6)
    parser.add_argument('--timeout', type=int, default=12)
    parser.add_argument('--storage-timeout', type=int, default=60,
                        help='Owned Storage upload/readback timeout; external downloads keep --timeout')
    parser.add_argument('--retry-errors', action='store_true', help='Explicitly retry previously recorded delivery errors once')
    parser.add_argument('--retry-confirmed-fixes', action='store_true',
                        help='Retry only exact previously diagnosed MIME/size exceptions')
    parser.add_argument('--retry-transport-errors', action='store_true',
                        help='Retry timeouts at most once per source; never HTTP, TLS, or content rejections')
    args = parser.parse_args()
    if (not 1 <= args.workers <= 6 or not 10 <= args.timeout <= 15
            or not 10 <= args.storage_timeout <= 60 or not 1 <= args.max_images <= 1000):
        raise ValueError('Require 1-6 workers, 10-15 second source and 10-60 second Storage timeouts, and at most 1000 portraits')
    args.manifest.parent.mkdir(parents=True, exist_ok=True)
    with args.manifest.with_suffix('.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        run(args)


if __name__ == '__main__':
    main()
