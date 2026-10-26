import QRCode from 'qrcode';
import { useEffect, useRef, useState } from 'react';
import { formatId, ROLE_LABEL } from '../format';

/** The account's 16-digit ID, with copy and a QR code a clinic or doctor can scan. */
export default function IdCard({ uid, name, role, extra }: { uid: string | null; name: string | null; role: string; extra?: string }) {
  const canvas = useRef<HTMLCanvasElement>(null);
  const [copied, setCopied] = useState(false);
  useEffect(() => {
    if (uid && canvas.current) QRCode.toCanvas(canvas.current, uid, { width: 104, margin: 0, errorCorrectionLevel: 'M' }).catch(() => undefined);
  }, [uid]);
  return (
    <section className="idcard" aria-label="Your CardiosensAI ID">
      <canvas ref={canvas} width={104} height={104} aria-hidden="true" />
      <div className="stack" style={{ gap: 4, flex: 1, minWidth: 220 }}>
        <span className="label">{ROLE_LABEL[role]} ID</span>
        <span className="uid">{formatId(uid)}</span>
        <span>{name}</span>
        {extra && <small style={{ color: 'var(--header-muted)' }}>{extra}</small>}
      </div>
      <button
        type="button"
        className="icon-btn"
        onClick={async () => {
          if (!uid) return;
          try {
            await navigator.clipboard.writeText(uid);
            setCopied(true);
            setTimeout(() => setCopied(false), 1500);
          } catch {
            /* clipboard blocked: the number is on screen */
          }
        }}
      >
        {copied ? 'Copied' : 'Copy ID'}
      </button>
    </section>
  );
}
