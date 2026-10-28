import { useEffect, useRef, useState } from 'react';
import { api, apiBinary, apiUpload, type User } from '../api';
import { useAuth } from '../auth';
import { Banner, errorText } from './ui';

// One loaded picture per version, shared by the header and the account page.
const urls = new Map<string, Promise<string>>();

function pictureUrl(path: string, version: string): Promise<string> {
  const key = `${path}?v=${version}`;
  let url = urls.get(key);
  if (!url) {
    // a data: URL, because the page's CSP allows img-src 'self' data: (not blob:)
    url = apiBinary(path).then(
      ({ data, headers }) =>
        new Promise<string>((ok, fail) => {
          const r = new FileReader();
          r.onload = () => ok(r.result as string);
          r.onerror = () => fail(r.error);
          r.readAsDataURL(new Blob([data], { type: headers.get('content-type') ?? 'image/jpeg' }));
        }),
    );
    url.catch(() => urls.delete(key));
    urls.set(key, url);
  }
  return url;
}

function initials(name: string | null | undefined): string {
  const parts = (name ?? '').replace(/\(.*?\)/g, '').trim().split(/\s+/).filter(Boolean);
  return (parts[0]?.[0] ?? '') + (parts.length > 1 ? parts[parts.length - 1][0] : '') || '•';
}

/** A profile picture loaded from `path` (with the session), or the initials of `name`. */
export function Picture({ path, version, name, size = 36 }: { path: string; version?: string | null; name?: string | null; size?: number }) {
  const [src, setSrc] = useState<string | null>(null);
  useEffect(() => {
    let live = true;
    setSrc(null);
    if (version) pictureUrl(path, version).then((u) => live && setSrc(u), () => undefined);
    return () => {
      live = false;
    };
  }, [path, version]);
  return (
    <span className="avatar" style={{ width: size, height: size, fontSize: size * 0.4 }} aria-hidden="true">
      {src ? <img src={src} alt="" /> : initials(name)}
    </span>
  );
}

/** The signed-in person's profile picture, or their initials. */
export default function Avatar({ user, size = 36 }: { user: Pick<User, 'fullName' | 'avatarVersion'>; size?: number }) {
  return <Picture path="/v1/me/avatar" version={user.avatarVersion} name={user.fullName} size={size} />;
}

/** Square-crop and shrink a photo in the browser, so the server stores a small JPEG (≤ 512 KB). */
async function toSquareJpeg(file: File, px = 320): Promise<Blob> {
  const img = await createImageBitmap(file);
  const side = Math.min(img.width, img.height);
  const canvas = document.createElement('canvas');
  canvas.width = canvas.height = px;
  canvas.getContext('2d')!.drawImage(img, (img.width - side) / 2, (img.height - side) / 2, side, side, 0, 0, px, px);
  img.close();
  return new Promise((ok, fail) => canvas.toBlob((b) => (b ? ok(b) : fail(new Error('Could not read that picture'))), 'image/jpeg', 0.86));
}

/** Account page: change or remove the profile picture. */
export function AvatarEditor() {
  const { user, reload } = useAuth();
  const input = useRef<HTMLInputElement>(null);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<{ kind: 'ok' | 'error'; text: string } | null>(null);
  if (!user) return null;
  const run = async (fn: () => Promise<unknown>, done: string) => {
    setBusy(true);
    setMsg(null);
    try {
      await fn();
      await reload();
      setMsg({ kind: 'ok', text: done });
    } catch (e) {
      setMsg({ kind: 'error', text: e instanceof Error && !('status' in e) ? e.message : errorText(e) });
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="stack" style={{ gap: 10 }}>
      <div className="row" style={{ gap: 16 }}>
        <Avatar user={user} size={72} />
        <div className="row" style={{ gap: 8 }}>
          <button type="button" className="btn ghost small" disabled={busy} onClick={() => input.current?.click()}>
            {busy ? 'Saving…' : user.avatarVersion ? 'Change picture' : 'Add a picture'}
          </button>
          {user.avatarVersion && (
            <button type="button" className="btn ghost small" disabled={busy} onClick={() => run(() => api('DELETE', '/v1/me/avatar'), 'Picture removed.')}>
              Remove
            </button>
          )}
        </div>
        <input
          ref={input}
          type="file"
          accept="image/jpeg,image/png,image/webp"
          hidden
          onChange={(e) => {
            const file = e.target.files?.[0];
            e.target.value = '';
            if (!file) return;
            if (!file.type.startsWith('image/')) return setMsg({ kind: 'error', text: 'Choose a photo (JPEG, PNG or WebP).' });
            void run(async () => {
              const form = new FormData();
              form.append('file', await toSquareJpeg(file), 'avatar.jpg');
              await apiUpload('/v1/me/avatar', form, 'PUT');
            }, 'Picture saved.');
          }}
        />
      </div>
      <small className="muted">Shown next to your name while you're signed in. Cropped to a square.</small>
      {msg && <Banner kind={msg.kind}>{msg.text}</Banner>}
    </div>
  );
}
