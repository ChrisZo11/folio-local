import type { Currency } from './types';

export async function api<T>(path: string, options?: RequestInit): Promise<T> {
  const response = await fetch(`/api${path}`, {
    ...options,
    headers: { 'Content-Type': 'application/json', ...options?.headers },
  });
  const body = await response.json();
  if (!response.ok) {
    const detail = Array.isArray(body.detail) ? body.detail.map((d: { msg: string; loc: string[] }) => `${d.loc.slice(1).join('.')}: ${d.msg}`).join('; ') : body.detail;
    throw new Error(body.error || detail || 'Unable to complete the request. Please try again.');
  }
  return body as T;
}

export const money = (value: number | null | undefined, currency: Currency = 'IDR', compact = false) => value == null ? '—' :
  new Intl.NumberFormat(currency === 'IDR' ? 'id-ID' : 'en-US', { style: 'currency', currency, maximumFractionDigits: compact ? 1 : currency === 'IDR' ? 0 : 2, notation: compact ? 'compact' : 'standard' }).format(value);
export const num = (value: number | null | undefined, digits = 2) => value == null ? '—' : new Intl.NumberFormat('en-US', { maximumFractionDigits: digits }).format(value);
export const pct = (value: number | null | undefined, signed = false) => value == null ? '—' : `${signed && value > 0 ? '+' : ''}${(value * 100).toFixed(2)}%`;
export const day = (value: string | null | undefined, short = false) => !value ? 'Not yet' : new Intl.DateTimeFormat('en-GB', { day: 'numeric', month: 'short', ...(short ? {} : { year: 'numeric' }), timeZone: 'Asia/Jakarta' }).format(new Date(value.length === 10 ? `${value}T12:00:00+07:00` : value));
export const timestamp = (value: string | null | undefined) => !value ? 'No cached quote' : new Intl.DateTimeFormat('en-GB', { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit', timeZone: 'Asia/Jakarta' }).format(new Date(value)) + ' WIB';
export const localDate = () => new Intl.DateTimeFormat('en-CA', { timeZone: 'Asia/Jakarta', year: 'numeric', month: '2-digit', day: '2-digit' }).format(new Date());

export function downloadCSV(name: string, rows: (string | number | null)[][]) {
  // Spreadsheet programs interpret leading formula characters even in quoted CSV cells.
  const escape = (v: string | number | null) => { let s = String(v ?? ''); if (typeof v === 'string' && /^[=+\-@\t\r]/.test(s)) s = `'${s}`; return `"${s.replaceAll('"', '""')}"`; };
  const url = URL.createObjectURL(new Blob(['\uFEFF' + rows.map(r => r.map(escape).join(',')).join('\r\n')], { type: 'text/csv;charset=utf-8' }));
  const anchor = document.createElement('a'); anchor.href = url; anchor.download = name; anchor.click(); URL.revokeObjectURL(url);
}
