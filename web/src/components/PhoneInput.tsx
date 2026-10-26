import { useEffect, useId, useMemo, useState } from 'react';
import { COUNTRIES, country, flag, fromE164, PINNED, phoneProblem, toE164 } from '../countries';

/** Phone number as a country-code dropdown + national number. Reports E.164 ("+919812345678") or "" upward.
 *  Problems are shown under the field and set as the input's custom validity, so the form won't submit. */
export default function PhoneInput({
  label,
  hint,
  value,
  onChange,
  required = false,
}: {
  label: string;
  hint?: string;
  value: string;
  onChange: (e164: string) => void;
  required?: boolean;
}) {
  const id = useId();
  const initial = useMemo(() => fromE164(value), []); // eslint-disable-line react-hooks/exhaustive-deps
  const [iso, setIso] = useState(initial.iso);
  const [national, setNational] = useState(initial.national);
  const [touched, setTouched] = useState(false);

  // A value loaded later (e.g. clinic details fetched after mount) replaces an untouched, empty field.
  useEffect(() => {
    if (!touched && value && !national) {
      const parsed = fromE164(value, iso);
      setIso(parsed.iso);
      setNational(parsed.national);
    }
  }, [value]); // eslint-disable-line react-hooks/exhaustive-deps

  const problem = phoneProblem(iso, national, required);
  const c = country(iso);
  const sorted = useMemo(() => {
    const pinned = PINNED.map((p) => country(p));
    const rest = COUNTRIES.filter((x) => !PINNED.includes(x.iso));
    return { pinned, rest };
  }, []);

  function update(nextIso: string, nextNational: string) {
    setIso(nextIso);
    setNational(nextNational);
    onChange(toE164(nextIso, nextNational));
  }

  const option = (x: (typeof COUNTRIES)[number]) => (
    <option key={x.iso} value={x.iso}>
      {flag(x.iso)} +{x.dial} {x.name}
    </option>
  );

  return (
    <div className="field" role="group" aria-labelledby={`${id}-label`}>
      <span id={`${id}-label`}>{label}</span>
      {hint && <small>{hint}</small>}
      <div className="phone-row">
        <select
          aria-label="Country code"
          value={iso}
          onChange={(e) => update(e.target.value, national)}
          className="phone-cc"
          title={`${c.name} (+${c.dial})`}
        >
          <optgroup label="Common">{sorted.pinned.map(option)}</optgroup>
          <optgroup label="All countries">{sorted.rest.map(option)}</optgroup>
        </select>
        <input
          id={id}
          type="tel"
          inputMode="tel"
          autoComplete="tel-national"
          aria-label={`${label}, number without country code`}
          aria-invalid={touched && !!problem}
          placeholder={c.nsn ? '9'.repeat(c.nsn).replace(/(\d{5})(?=\d)/, '$1 ') : 'Phone number'}
          value={national}
          required={required}
          ref={(el) => el?.setCustomValidity(problem ?? '')}
          onBlur={() => setTouched(true)}
          onChange={(e) => update(iso, e.target.value)}
        />
      </div>
      {touched && problem && (
        <small role="alert" style={{ color: 'var(--danger)' }}>
          {problem}
        </small>
      )}
    </div>
  );
}
