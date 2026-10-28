import { Tagline } from './Layout';

const SITE = 'https://www.cardiosens.ai';

/** Same links as the cardiosens.ai footer; they open the public site in a new tab. */
const LINKS = [
  { href: `${SITE}/#how`, label: 'How it works' },
  { href: `${SITE}/#ai`, label: 'Our AI' },
  { href: `${SITE}/#team`, label: 'Team' },
  { href: `${SITE}/blog`, label: 'Blog' },
  { href: `${SITE}/#contact`, label: 'Contact' },
  { href: `${SITE}/#newsletter`, label: 'Newsletter' },
];

export default function SiteFooter() {
  return (
    <footer className="site-footer">
      <div className="site-footer-inner">
        <a className="site-footer-brand" href={`${SITE}/`} target="_blank" rel="noopener noreferrer" aria-label="CardiosensAI website">
          <span className="grad">
            Cardiosens<b>AI</b>
          </span>
          <Tagline />
        </a>
        <nav aria-label="Footer">
          {LINKS.map((l) => (
            <a key={l.href} href={l.href} target="_blank" rel="noopener noreferrer">
              {l.label}
            </a>
          ))}
        </nav>
        <small>© {new Date().getFullYear()} CardiosensAI</small>
      </div>
    </footer>
  );
}
