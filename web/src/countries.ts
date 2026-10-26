/** Country calling codes (ITU-T E.164) for the phone-number dropdown.
 *  `dial` is the full country code without "+". NANP members other than the US and Canada carry their
 *  3-digit area code, so the subscriber part is the 7-digit local number.
 *  `nsn` is the usual national number length without the trunk 0 (a placeholder hint). It is enforced only
 *  for countries in FIXED_LENGTH, where mobiles and landlines have the same length. */
export interface Country {
  iso: string;
  name: string;
  dial: string;
  nsn?: number;
}

export const DEFAULT_COUNTRY = 'IN';

export const COUNTRIES: Country[] = [
  { iso: 'AF', name: 'Afghanistan', dial: '93', nsn: 9 },
  { iso: 'AL', name: 'Albania', dial: '355' },
  { iso: 'DZ', name: 'Algeria', dial: '213', nsn: 9 },
  { iso: 'AD', name: 'Andorra', dial: '376' },
  { iso: 'AO', name: 'Angola', dial: '244', nsn: 9 },
  { iso: 'AG', name: 'Antigua and Barbuda', dial: '1268', nsn: 7 },
  { iso: 'AR', name: 'Argentina', dial: '54' },
  { iso: 'AM', name: 'Armenia', dial: '374', nsn: 8 },
  { iso: 'AU', name: 'Australia', dial: '61', nsn: 9 },
  { iso: 'AT', name: 'Austria', dial: '43' },
  { iso: 'AZ', name: 'Azerbaijan', dial: '994', nsn: 9 },
  { iso: 'BS', name: 'Bahamas', dial: '1242', nsn: 7 },
  { iso: 'BH', name: 'Bahrain', dial: '973', nsn: 8 },
  { iso: 'BD', name: 'Bangladesh', dial: '880', nsn: 10 },
  { iso: 'BB', name: 'Barbados', dial: '1246', nsn: 7 },
  { iso: 'BY', name: 'Belarus', dial: '375', nsn: 9 },
  { iso: 'BE', name: 'Belgium', dial: '32' },
  { iso: 'BZ', name: 'Belize', dial: '501', nsn: 7 },
  { iso: 'BJ', name: 'Benin', dial: '229' },
  { iso: 'BT', name: 'Bhutan', dial: '975' },
  { iso: 'BO', name: 'Bolivia', dial: '591', nsn: 8 },
  { iso: 'BA', name: 'Bosnia and Herzegovina', dial: '387' },
  { iso: 'BW', name: 'Botswana', dial: '267' },
  { iso: 'BR', name: 'Brazil', dial: '55', nsn: 11 },
  { iso: 'BN', name: 'Brunei', dial: '673', nsn: 7 },
  { iso: 'BG', name: 'Bulgaria', dial: '359' },
  { iso: 'BF', name: 'Burkina Faso', dial: '226', nsn: 8 },
  { iso: 'BI', name: 'Burundi', dial: '257', nsn: 8 },
  { iso: 'KH', name: 'Cambodia', dial: '855' },
  { iso: 'CM', name: 'Cameroon', dial: '237', nsn: 9 },
  { iso: 'CA', name: 'Canada', dial: '1', nsn: 10 },
  { iso: 'CV', name: 'Cape Verde', dial: '238', nsn: 7 },
  { iso: 'CF', name: 'Central African Republic', dial: '236', nsn: 8 },
  { iso: 'TD', name: 'Chad', dial: '235', nsn: 8 },
  { iso: 'CL', name: 'Chile', dial: '56', nsn: 9 },
  { iso: 'CN', name: 'China', dial: '86' },
  { iso: 'CO', name: 'Colombia', dial: '57', nsn: 10 },
  { iso: 'KM', name: 'Comoros', dial: '269', nsn: 7 },
  { iso: 'CG', name: 'Congo', dial: '242', nsn: 9 },
  { iso: 'CD', name: 'Congo (DRC)', dial: '243', nsn: 9 },
  { iso: 'CR', name: 'Costa Rica', dial: '506', nsn: 8 },
  { iso: 'CI', name: 'Côte d’Ivoire', dial: '225', nsn: 10 },
  { iso: 'HR', name: 'Croatia', dial: '385' },
  { iso: 'CU', name: 'Cuba', dial: '53' },
  { iso: 'CY', name: 'Cyprus', dial: '357', nsn: 8 },
  { iso: 'CZ', name: 'Czechia', dial: '420', nsn: 9 },
  { iso: 'DK', name: 'Denmark', dial: '45', nsn: 8 },
  { iso: 'DJ', name: 'Djibouti', dial: '253', nsn: 8 },
  { iso: 'DM', name: 'Dominica', dial: '1767', nsn: 7 },
  { iso: 'DO', name: 'Dominican Republic', dial: '1809', nsn: 7 },
  { iso: 'EC', name: 'Ecuador', dial: '593' },
  { iso: 'EG', name: 'Egypt', dial: '20' },
  { iso: 'SV', name: 'El Salvador', dial: '503', nsn: 8 },
  { iso: 'GQ', name: 'Equatorial Guinea', dial: '240', nsn: 9 },
  { iso: 'ER', name: 'Eritrea', dial: '291', nsn: 7 },
  { iso: 'EE', name: 'Estonia', dial: '372' },
  { iso: 'SZ', name: 'Eswatini', dial: '268', nsn: 8 },
  { iso: 'ET', name: 'Ethiopia', dial: '251', nsn: 9 },
  { iso: 'FJ', name: 'Fiji', dial: '679', nsn: 7 },
  { iso: 'FI', name: 'Finland', dial: '358' },
  { iso: 'FR', name: 'France', dial: '33', nsn: 9 },
  { iso: 'GA', name: 'Gabon', dial: '241' },
  { iso: 'GM', name: 'Gambia', dial: '220', nsn: 7 },
  { iso: 'GE', name: 'Georgia', dial: '995', nsn: 9 },
  { iso: 'DE', name: 'Germany', dial: '49' },
  { iso: 'GH', name: 'Ghana', dial: '233', nsn: 9 },
  { iso: 'GR', name: 'Greece', dial: '30', nsn: 10 },
  { iso: 'GD', name: 'Grenada', dial: '1473', nsn: 7 },
  { iso: 'GT', name: 'Guatemala', dial: '502', nsn: 8 },
  { iso: 'GN', name: 'Guinea', dial: '224', nsn: 9 },
  { iso: 'GW', name: 'Guinea-Bissau', dial: '245' },
  { iso: 'GY', name: 'Guyana', dial: '592', nsn: 7 },
  { iso: 'HT', name: 'Haiti', dial: '509', nsn: 8 },
  { iso: 'HN', name: 'Honduras', dial: '504', nsn: 8 },
  { iso: 'HK', name: 'Hong Kong', dial: '852', nsn: 8 },
  { iso: 'HU', name: 'Hungary', dial: '36', nsn: 9 },
  { iso: 'IS', name: 'Iceland', dial: '354', nsn: 7 },
  { iso: 'IN', name: 'India', dial: '91', nsn: 10 },
  { iso: 'ID', name: 'Indonesia', dial: '62' },
  { iso: 'IR', name: 'Iran', dial: '98', nsn: 10 },
  { iso: 'IQ', name: 'Iraq', dial: '964', nsn: 10 },
  { iso: 'IE', name: 'Ireland', dial: '353' },
  { iso: 'IL', name: 'Israel', dial: '972', nsn: 9 },
  { iso: 'IT', name: 'Italy', dial: '39' },
  { iso: 'JM', name: 'Jamaica', dial: '1876', nsn: 7 },
  { iso: 'JP', name: 'Japan', dial: '81' },
  { iso: 'JO', name: 'Jordan', dial: '962', nsn: 9 },
  { iso: 'KZ', name: 'Kazakhstan', dial: '7', nsn: 10 },
  { iso: 'KE', name: 'Kenya', dial: '254', nsn: 9 },
  { iso: 'KI', name: 'Kiribati', dial: '686' },
  { iso: 'XK', name: 'Kosovo', dial: '383' },
  { iso: 'KW', name: 'Kuwait', dial: '965', nsn: 8 },
  { iso: 'KG', name: 'Kyrgyzstan', dial: '996', nsn: 9 },
  { iso: 'LA', name: 'Laos', dial: '856' },
  { iso: 'LV', name: 'Latvia', dial: '371', nsn: 8 },
  { iso: 'LB', name: 'Lebanon', dial: '961' },
  { iso: 'LS', name: 'Lesotho', dial: '266', nsn: 8 },
  { iso: 'LR', name: 'Liberia', dial: '231' },
  { iso: 'LY', name: 'Libya', dial: '218', nsn: 9 },
  { iso: 'LI', name: 'Liechtenstein', dial: '423' },
  { iso: 'LT', name: 'Lithuania', dial: '370', nsn: 8 },
  { iso: 'LU', name: 'Luxembourg', dial: '352' },
  { iso: 'MO', name: 'Macau', dial: '853', nsn: 8 },
  { iso: 'MG', name: 'Madagascar', dial: '261', nsn: 9 },
  { iso: 'MW', name: 'Malawi', dial: '265' },
  { iso: 'MY', name: 'Malaysia', dial: '60' },
  { iso: 'MV', name: 'Maldives', dial: '960', nsn: 7 },
  { iso: 'ML', name: 'Mali', dial: '223', nsn: 8 },
  { iso: 'MT', name: 'Malta', dial: '356', nsn: 8 },
  { iso: 'MH', name: 'Marshall Islands', dial: '692', nsn: 7 },
  { iso: 'MR', name: 'Mauritania', dial: '222', nsn: 8 },
  { iso: 'MU', name: 'Mauritius', dial: '230', nsn: 8 },
  { iso: 'MX', name: 'Mexico', dial: '52', nsn: 10 },
  { iso: 'FM', name: 'Micronesia', dial: '691', nsn: 7 },
  { iso: 'MD', name: 'Moldova', dial: '373', nsn: 8 },
  { iso: 'MC', name: 'Monaco', dial: '377' },
  { iso: 'MN', name: 'Mongolia', dial: '976', nsn: 8 },
  { iso: 'ME', name: 'Montenegro', dial: '382' },
  { iso: 'MA', name: 'Morocco', dial: '212', nsn: 9 },
  { iso: 'MZ', name: 'Mozambique', dial: '258', nsn: 9 },
  { iso: 'MM', name: 'Myanmar', dial: '95' },
  { iso: 'NA', name: 'Namibia', dial: '264' },
  { iso: 'NR', name: 'Nauru', dial: '674', nsn: 7 },
  { iso: 'NP', name: 'Nepal', dial: '977', nsn: 10 },
  { iso: 'NL', name: 'Netherlands', dial: '31', nsn: 9 },
  { iso: 'NZ', name: 'New Zealand', dial: '64' },
  { iso: 'NI', name: 'Nicaragua', dial: '505', nsn: 8 },
  { iso: 'NE', name: 'Niger', dial: '227', nsn: 8 },
  { iso: 'NG', name: 'Nigeria', dial: '234' },
  { iso: 'KP', name: 'North Korea', dial: '850' },
  { iso: 'MK', name: 'North Macedonia', dial: '389', nsn: 8 },
  { iso: 'NO', name: 'Norway', dial: '47', nsn: 8 },
  { iso: 'OM', name: 'Oman', dial: '968', nsn: 8 },
  { iso: 'PK', name: 'Pakistan', dial: '92', nsn: 10 },
  { iso: 'PW', name: 'Palau', dial: '680', nsn: 7 },
  { iso: 'PS', name: 'Palestine', dial: '970', nsn: 9 },
  { iso: 'PA', name: 'Panama', dial: '507' },
  { iso: 'PG', name: 'Papua New Guinea', dial: '675' },
  { iso: 'PY', name: 'Paraguay', dial: '595', nsn: 9 },
  { iso: 'PE', name: 'Peru', dial: '51', nsn: 9 },
  { iso: 'PH', name: 'Philippines', dial: '63', nsn: 10 },
  { iso: 'PL', name: 'Poland', dial: '48', nsn: 9 },
  { iso: 'PT', name: 'Portugal', dial: '351', nsn: 9 },
  { iso: 'QA', name: 'Qatar', dial: '974', nsn: 8 },
  { iso: 'RO', name: 'Romania', dial: '40', nsn: 9 },
  { iso: 'RU', name: 'Russia', dial: '7', nsn: 10 },
  { iso: 'RW', name: 'Rwanda', dial: '250', nsn: 9 },
  { iso: 'KN', name: 'Saint Kitts and Nevis', dial: '1869', nsn: 7 },
  { iso: 'LC', name: 'Saint Lucia', dial: '1758', nsn: 7 },
  { iso: 'VC', name: 'Saint Vincent and the Grenadines', dial: '1784', nsn: 7 },
  { iso: 'WS', name: 'Samoa', dial: '685' },
  { iso: 'SM', name: 'San Marino', dial: '378' },
  { iso: 'ST', name: 'São Tomé and Príncipe', dial: '239', nsn: 7 },
  { iso: 'SA', name: 'Saudi Arabia', dial: '966', nsn: 9 },
  { iso: 'SN', name: 'Senegal', dial: '221', nsn: 9 },
  { iso: 'RS', name: 'Serbia', dial: '381' },
  { iso: 'SC', name: 'Seychelles', dial: '248', nsn: 7 },
  { iso: 'SL', name: 'Sierra Leone', dial: '232', nsn: 8 },
  { iso: 'SG', name: 'Singapore', dial: '65', nsn: 8 },
  { iso: 'SK', name: 'Slovakia', dial: '421', nsn: 9 },
  { iso: 'SI', name: 'Slovenia', dial: '386', nsn: 8 },
  { iso: 'SB', name: 'Solomon Islands', dial: '677' },
  { iso: 'SO', name: 'Somalia', dial: '252' },
  { iso: 'ZA', name: 'South Africa', dial: '27', nsn: 9 },
  { iso: 'KR', name: 'South Korea', dial: '82' },
  { iso: 'SS', name: 'South Sudan', dial: '211', nsn: 9 },
  { iso: 'ES', name: 'Spain', dial: '34', nsn: 9 },
  { iso: 'LK', name: 'Sri Lanka', dial: '94', nsn: 9 },
  { iso: 'SD', name: 'Sudan', dial: '249', nsn: 9 },
  { iso: 'SR', name: 'Suriname', dial: '597' },
  { iso: 'SE', name: 'Sweden', dial: '46' },
  { iso: 'CH', name: 'Switzerland', dial: '41', nsn: 9 },
  { iso: 'SY', name: 'Syria', dial: '963', nsn: 9 },
  { iso: 'TW', name: 'Taiwan', dial: '886', nsn: 9 },
  { iso: 'TJ', name: 'Tajikistan', dial: '992', nsn: 9 },
  { iso: 'TZ', name: 'Tanzania', dial: '255', nsn: 9 },
  { iso: 'TH', name: 'Thailand', dial: '66', nsn: 9 },
  { iso: 'TL', name: 'Timor-Leste', dial: '670' },
  { iso: 'TG', name: 'Togo', dial: '228', nsn: 8 },
  { iso: 'TO', name: 'Tonga', dial: '676' },
  { iso: 'TT', name: 'Trinidad and Tobago', dial: '1868', nsn: 7 },
  { iso: 'TN', name: 'Tunisia', dial: '216', nsn: 8 },
  { iso: 'TR', name: 'Türkiye', dial: '90', nsn: 10 },
  { iso: 'TM', name: 'Turkmenistan', dial: '993', nsn: 8 },
  { iso: 'TV', name: 'Tuvalu', dial: '688' },
  { iso: 'UG', name: 'Uganda', dial: '256', nsn: 9 },
  { iso: 'UA', name: 'Ukraine', dial: '380', nsn: 9 },
  { iso: 'AE', name: 'United Arab Emirates', dial: '971', nsn: 9 },
  { iso: 'GB', name: 'United Kingdom', dial: '44', nsn: 10 },
  { iso: 'US', name: 'United States', dial: '1', nsn: 10 },
  { iso: 'UY', name: 'Uruguay', dial: '598' },
  { iso: 'UZ', name: 'Uzbekistan', dial: '998', nsn: 9 },
  { iso: 'VU', name: 'Vanuatu', dial: '678' },
  { iso: 'VA', name: 'Vatican City', dial: '379' },
  { iso: 'VE', name: 'Venezuela', dial: '58', nsn: 10 },
  { iso: 'VN', name: 'Vietnam', dial: '84' },
  { iso: 'YE', name: 'Yemen', dial: '967', nsn: 9 },
  { iso: 'ZM', name: 'Zambia', dial: '260', nsn: 9 },
  { iso: 'ZW', name: 'Zimbabwe', dial: '263', nsn: 9 },
];

/** Shown first in the dropdown (most likely for CardiosensAI users), then all countries A–Z. */
export const PINNED = ['IN', 'BD', 'NP', 'LK', 'AE', 'US', 'GB'];

const BY_ISO = new Map(COUNTRIES.map((c) => [c.iso, c]));

export function country(iso: string): Country {
  return BY_ISO.get(iso) ?? BY_ISO.get(DEFAULT_COUNTRY)!;
}

/** Regional-indicator flag emoji for an ISO 3166 code. */
export function flag(iso: string): string {
  return String.fromCodePoint(...[...iso.toUpperCase()].map((ch) => 0x1f1e6 + ch.charCodeAt(0) - 65));
}

/** Join a country and a typed national number into E.164 ("+919812345678"), or "" when nothing was typed.
 *  Drops spaces, dashes, brackets and a leading trunk "0" (as in "098123 45678"). */
export function toE164(iso: string, national: string): string {
  let digits = national.replace(/\D/g, '');
  const c = country(iso);
  if (national.trim().startsWith('+')) return '+' + digits; // pasted a full international number
  digits = digits.replace(/^0+/, '');
  // Typed the country code again ("91 98123 45678" with India selected).
  if (c.nsn && digits.length === c.dial.length + c.nsn && digits.startsWith(c.dial)) digits = digits.slice(c.dial.length);
  // NANP islands: the area code is part of `dial`; drop it if the full 10-digit number was typed.
  if (c.dial.length === 4 && digits.length === 10 && digits.startsWith(c.dial.slice(1))) digits = digits.slice(3);
  return digits ? `+${c.dial}${digits}` : '';
}

/** Where several countries share a code, which one a stored number is shown as. */
const SHARED_CODE_DEFAULT: Record<string, string> = { '1': 'US', '7': 'RU' };

/** Split a stored E.164 number back into a country and national part. The longest matching code wins;
 *  a shared code (+1, +7) goes to `preferIso` if it uses that code, otherwise to the usual default. */
export function fromE164(value: string | null | undefined, preferIso = DEFAULT_COUNTRY): { iso: string; national: string } {
  const digits = (value ?? '').replace(/\D/g, '');
  if (!value || !value.trim().startsWith('+') || !digits) return { iso: preferIso, national: '' };
  const matches = COUNTRIES.filter((c) => digits.startsWith(c.dial)).sort((a, b) => b.dial.length - a.dial.length);
  if (!matches.length) return { iso: preferIso, national: digits };
  const dial = matches[0].dial;
  const same = matches.filter((c) => c.dial === dial);
  const pick = same.find((c) => c.iso === preferIso) ?? same.find((c) => c.iso === SHARED_CODE_DEFAULT[dial]) ?? same[0];
  return { iso: pick.iso, national: digits.slice(dial.length) };
}

/** Countries where every number (mobile and landline) has exactly `nsn` digits. */
const FIXED_LENGTH = new Set(['IN', 'US', 'CA', 'AG', 'BS', 'BB', 'DM', 'DO', 'GD', 'JM', 'KN', 'LC', 'VC', 'TT', 'FR', 'ES', 'PT', 'PL', 'NL', 'CH', 'CZ', 'DK', 'NO', 'SG', 'HK', 'QA', 'KW', 'MX', 'ZA', 'LK', 'RU', 'KZ']);

/** null when OK, otherwise a short message. Server rule: E.164, 8–15 digits after "+". */
export function phoneProblem(iso: string, national: string, required: boolean): string | null {
  const e164 = toE164(iso, national);
  if (!e164) return required ? 'Enter a phone number' : null;
  const total = e164.length - 1;
  const c = country(iso);
  const nsn = total - c.dial.length;
  if (total < 8 || total > 15) return 'This number has too few or too many digits';
  if (c.nsn && FIXED_LENGTH.has(c.iso) && !national.trim().startsWith('+') && nsn !== c.nsn)
    return `${c.name} numbers have ${c.nsn} digits after +${c.dial}`;
  return null;
}
