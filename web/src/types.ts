export type Currency = 'IDR' | 'USD';
export type Portfolio = { id: number; name: string; base_currency: Currency };
export type Instrument = { symbol: string; provider_symbol: string; name: string; asset_class: string; sector: string; country: string; currency: Currency; exchange: string; custom: number };
export type Quote = { stale: boolean; status: string; delay: string; as_of: string | null };
export type Holding = Instrument & {
  quantity: number; cost_basis: number | null; local_cost: number; average_cost: number | null;
  price: number | null; value: number | null; local_value: number | null; unrealized_pnl: number | null;
  realized_pnl: number | null; dividends_received: number | null; total_profit: number | null;
  profit_percent: number | null; day_change: number | null; day_percent: number | null;
  weight: number; irr: number | null; history_complete: boolean; quote: Quote;
  yield_on_cost: number | null; current_yield: number | null; dividend_growth_5y: number | null;
  annual_income_estimate: number | null; currency_effect: number | null;
};
export type Summary = {
  holdings: Holding[]; cash: { portfolio_id: number; currency: Currency; amount: number; value: number | null }[];
  total_value: number | null; securities_value: number | null; cash_value: number | null; invested: number | null;
  total_profit: number | null; profit_percent: number | null; realized_pnl: number | null; unrealized_pnl: number | null;
  dividends_received: number | null; day_change: number | null; day_percent: number | null; irr: number | null;
  current_holdings_irr: number | null; annual_income_estimate: number; income_yield: number | null;
  income_estimate_complete: boolean; complete: boolean; warnings: string[]; as_of: string;
  base_currency: Currency; cost_basis: string; assumptions: string[]; price_as_of: string | null;
  fx: { rate: number; as_of: string; fetched_at: string; stale: boolean } | null;
};
export type Transaction = {
  id: number; portfolio_id: number; portfolio: string; date: string; type: string; symbol: string | null;
  quantity: number; price: number; fee: number; tax: number; currency: Currency; fx_rate: number;
  note: string; source: string; fee_estimated: boolean; affects_cash: boolean; confirmed: boolean;
  total: number; base_total: number | null; profit: number | null;
};
export type Performance = { xirr: number | null; twr: number | null; series: { date: string; value: number; return: number; external_flow: number }[]; warnings: string[]; start: string; end: string; period: string };
export type Group = { name: string; value: number; invested: number; profit: number; weight: number; count: number };
export type Allocation = { groups: Group[]; warnings: string[]; group_by: string };
export type DividendProposal = { symbol: string; portfolio_id: number; ex_date: string; quantity: number; reported_shares: number | null; reconciliation_warning: boolean; amount_per_share: number; gross: number; currency: Currency; status: string };
export type Dividends = {
  confirmed: { id: number; symbol: string; date: string; portfolio: string; currency: Currency; gross: number; tax: number; net: number; base_net: number | null }[];
  proposals: DividendProposal[]; total_net: number; trailing_12_months: number; annual_estimate: number;
  estimate_complete: boolean; groups: { name: string; value: number }[];
  forward_estimates: { symbol: string; annual_estimate: number | null; payouts_per_year?: number; reason?: string }[];
};
export type ImportHistory = { id: number; source: string; file_name: string; imported_at: string; added: number; skipped: number; failed: number; undone: number };
export type ImportPreview = { added: number; skipped: number; failed: number; rows: { line: number; status: string; error: string | null; row: Record<string, string | number | null> | null }[] };
export type Settings = { base_currency: Currency; cost_basis: 'fifo' | 'average'; quote_ttl: number; demo: boolean };
export type Status = { demo: boolean; database: string; last_refresh: string | null; transaction_count: number; quote_count: number; warnings: string[] };
