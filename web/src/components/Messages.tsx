import { useEffect, useRef, useState } from 'react';
import { Link, useNavigate, useParams } from 'react-router-dom';
import { api, type ChatMessage, type Conversation, type MessageThread } from '../api';
import { ago, when } from '../format';
import { Banner, errorText, useLoad } from './ui';

/*
 * Messages between a patient and a doctor they're linked with (api/v1/messages.py).
 * Thread: used on the patient's Messages page and on the doctor's patient page. Polls every 15 s.
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

/** Patient: Messages page. Linked doctors on the left, the conversation on the right. */
export function MessagesPage() {
  const { party } = useParams();
  const navigate = useNavigate();
  const [tick, setTick] = useState(0);
  const [convs, err] = useLoad(() => api<Conversation[]>('GET', '/v1/conversations'), [tick]);
  const current = party ?? (convs && convs.length > 0 && window.matchMedia('(min-width: 761px)').matches ? convs[0].partner.userId : undefined);
  const active = convs?.find((c) => c.partner.userId === current || c.partner.uniqueId === current);
  return (
    <div className="grid">
      <section className={`card span-4 chat-list-card ${party ? 'hide-narrow' : ''}`}>
        <h2>Messages</h2>
        <p className="muted small" style={{ marginTop: 0 }}>
          Write to doctors you’re linked with. For an emergency call your local emergency number, not a message.
        </p>
        {err && <Banner kind="error">{err}</Banner>}
        {convs && convs.length === 0 && (
          <div className="empty">
            No doctors yet. Link with a doctor under <Link to="/links">Care team</Link> or find one in <Link to="/doctors">Doctors</Link>.
          </div>
        )}
        <ul className="chat-list">
          {(convs ?? []).map((c) => (
            <li key={c.partner.userId}>
              <button type="button" className={`chat-item ${active?.partner.userId === c.partner.userId ? 'on' : ''}`} onClick={() => navigate(`/messages/${c.partner.userId}`)}>
                <span className="row spread" style={{ gap: 8 }}>
                  <strong>{c.partner.fullName}</strong>
                  {c.unread > 0 ? <span className="pill warn">{c.unread} new</span> : c.lastAt && <small className="muted">{ago(c.lastAt)}</small>}
                </span>
                <small className="muted chat-preview">
                  {c.lastPreview ? `${c.lastMine ? 'You: ' : ''}${c.lastPreview}` : c.canSend ? 'Start a conversation' : 'Not linked any more'}
                </small>
              </button>
            </li>
          ))}
        </ul>
      </section>
      <section className={`card span-8 ${party ? '' : 'hide-narrow'}`}>
        {active ? (
          <>
            <div className="row spread" style={{ marginBottom: 8 }}>
              <h2 style={{ margin: 0 }}>{active.partner.fullName}</h2>
              <Link to="/messages" className="btn ghost small show-narrow">
                ← All
              </Link>
            </div>
            <Thread party={active.partner.userId} onRead={() => active.unread > 0 && setTick((t) => t + 1)} onSent={() => setTick((t) => t + 1)} />
          </>
        ) : (
          <div className="empty">
            {party && convs
              ? 'You can message a doctor once you’re linked with them. Tick “Also link” when booking, or send a request under Care team.'
              : convs?.length
                ? 'Choose a doctor to see your messages.'
                : ''}
          </div>
        )}
      </section>
    </div>
  );
}
