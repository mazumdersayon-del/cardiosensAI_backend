import { useEffect, useRef, useState } from 'react';
import { api, type ChatMessage, type MessageThread } from '../api';
import { when } from '../format';
import { Banner, errorText, useLoad } from './ui';

/*
 * Messages between a patient and a doctor they're linked with (api/v1/messages.py).
 * Thread: used on the patient's Care team page (Message next to a linked doctor) and on the doctor's patient page. Polls every 15 s.
 */

const MAX = 2000;

/** Unread messages for the nav badge; polls every 30 s. */
export function useUnread(enabled: boolean): number {
  const [n, setN] = useState(0);
  useEffect(() => {
    if (!enabled) return;
    let stop = false;
    const load = () =>
      api<{ unread: number }>('GET', '/v1/conversations/unread')
        .then((r) => !stop && setN(r.unread))
        .catch(() => undefined);
    load();
    const t = setInterval(load, 30000);
    const onRead = () => load();
    window.addEventListener('cs:messages-read', onRead);
    return () => {
      stop = true;
      clearInterval(t);
      window.removeEventListener('cs:messages-read', onRead);
    };
  }, [enabled]);
  return n;
}

function dayOf(iso: string): string {
  return new Date(iso).toLocaleDateString(undefined, { weekday: 'short', day: 'numeric', month: 'short', year: 'numeric' });
}

function timeOf(iso: string): string {
  return new Date(iso).toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' });
}

export function Thread({ party, height = 420, onRead, onSent }: { party: string; height?: number; onRead?: () => void; onSent?: () => void }) {
  const [tick, setTick] = useState(0);
  const [data, err] = useLoad(() => api<MessageThread>('GET', `/v1/conversations/${party}`), [party, tick]);
  const [text, setText] = useState('');
  const [busy, setBusy] = useState(false);
  const [sendErr, setSendErr] = useState<string | null>(null);
  const [sent, setSent] = useState<ChatMessage[]>([]);
  const box = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const t = setInterval(() => setTick((x) => x + 1), 15000);
    return () => clearInterval(t);
  }, []);
  useEffect(() => {
    setSent([]);
  }, [data]);
  const shown = [...(data?.messages ?? []), ...sent.filter((m) => !data?.messages.some((x) => x.id === m.id))];
  const count = shown.length;
  useEffect(() => {
    if (box.current) box.current.scrollTop = box.current.scrollHeight;
  }, [count]);
  useEffect(() => {
    if (data) {
      window.dispatchEvent(new Event('cs:messages-read'));
      onRead?.();
    }
  }, [data]);
  const send = async () => {
    const body = text.trim();
    if (!body) return;
    setBusy(true);
    setSendErr(null);
    try {
      const m = await api<ChatMessage>('POST', `/v1/conversations/${party}/messages`, { body });
      setSent((s) => [...s, m]);
      setText('');
      onSent?.();
    } catch (e) {
      setSendErr(errorText(e));
    } finally {
      setBusy(false);
    }
  };
  let lastDay = '';
  return (
    <div className="chat">
      {err && <Banner kind="error">{err}</Banner>}
      <div className="chat-log" ref={box} style={{ height }} aria-live="polite">
        {data && count === 0 && <div className="muted chat-empty">No messages yet. {data.canSend ? 'Write the first one below.' : ''}</div>}
        {shown.map((m) => {
          const d = dayOf(m.createdAt);
          const head = d !== lastDay;
          lastDay = d;
          return (
            <div key={m.id}>
              {head && <div className="chat-day">{d}</div>}
              <div className={`chat-msg ${m.mine ? 'mine' : 'theirs'}`} title={when(m.createdAt)}>
                <div className="chat-body">{m.body}</div>
                <div className="chat-meta">
                  {timeOf(m.createdAt)}
                  {m.mine && (m.readAt ? ' · Seen' : ' · Sent')}
                </div>
              </div>
            </div>
          );
        })}
      </div>
      {data && !data.canSend && <p className="muted small">You’re no longer linked, so you can read these messages but not send new ones.</p>}
      {data?.canSend && (
        <form
          className="chat-compose"
          onSubmit={(e) => {
            e.preventDefault();
            void send();
          }}
        >
          <label className="sr-only" htmlFor={`msg-${party}`}>
            Message to {data.partner.fullName}
          </label>
          <textarea
            id={`msg-${party}`}
            rows={2}
            value={text}
            maxLength={MAX}
            placeholder={`Message ${data.partner.fullName}…`}
            onChange={(e) => setText(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault();
                void send();
              }
            }}
          />
          <button type="submit" className="btn primary" disabled={busy || !text.trim()}>
            {busy ? 'Sending…' : 'Send'}
          </button>
        </form>
      )}
      {sendErr && <Banner kind="error">{sendErr}</Banner>}
    </div>
  );
}
