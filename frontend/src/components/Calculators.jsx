// Calculators.jsx
// Two calculators (loan-loss provision, cash-payment limit) that call the backend's deterministic
// endpoints. Regulatory figures are never computed or stored in the browser.

import { useState } from 'react';
import CalculationCard from './CalculationCard';
import { ApiError, calculateLoanProvision, checkCashLimit } from '../api';
import { useI18n } from '../i18n/context';

const inputClass =
  'w-full rounded-lg border border-gray-300 bg-white px-3 py-2 text-sm focus:outline-none focus-visible:ring-2 focus-visible:ring-nepal-blue/40 focus:border-nepal-blue';
const labelClass = 'flex flex-col gap-1 text-xs font-medium text-gray-600';

const useCalculator = (fn) => {
  const { t } = useI18n();
  const [result, setResult] = useState(null);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);

  const run = async (payload) => {
    setLoading(true);
    setError('');
    setResult(null);
    try {
      setResult(await fn(payload));
    } catch (err) {
      setError(err instanceof ApiError && err.status === 0 ? t.errorConnect : err.message || t.errorGeneric);
    } finally {
      setLoading(false);
    }
  };
  return { result, error, loading, run };
};

const FySelect = ({ id, value, onChange, fyOptions, years }) => {
  const { t } = useI18n();
  return (
    <label className={labelClass} htmlFor={id}>
      {t.fiscalYear}
      <select id={id} className={inputClass} value={value} onChange={(e) => onChange(e.target.value)}>
        {fyOptions.map((o) => (
          <option key={o.value} value={o.value}>
            {o.value} {o.isCurrent ? t.currentSuffix : ''} {years.includes(o.value) ? '' : '⚠'}
          </option>
        ))}
      </select>
    </label>
  );
};

const Panel = ({ title, intro, children }) => (
  <section className="bg-white rounded-2xl border border-gray-200 shadow-sm p-4">
    <h2 className="text-sm font-bold text-nepal-blue">{title}</h2>
    {intro && <p className="text-xs text-gray-500 mt-0.5 mb-3">{intro}</p>}
    {children}
  </section>
);

const SubmitButton = ({ loading, label }) => {
  const { t } = useI18n();
  return (
    <button
      type="submit"
      disabled={loading}
      className="w-full mt-1 bg-gradient-to-br from-nepal-red to-nepal-red-dark text-white text-sm font-semibold rounded-lg py-2 disabled:opacity-50 focus:outline-none focus-visible:ring-2 focus-visible:ring-nepal-red/50"
    >
      {loading ? t.calculating : label}
    </button>
  );
};

const newLoan = () => ({ id: Math.random().toString(36).slice(2), amount: '', months: '' });

const ProvisionCalculator = ({ fyOptions, defaultFy, years }) => {
  const { t } = useI18n();
  const [fy, setFy] = useState(defaultFy);
  const [family, setFamily] = useState('abc');
  const [loans, setLoans] = useState([newLoan()]);
  const calc = useCalculator(calculateLoanProvision);
  const activeFy = fy || defaultFy;

  const update = (id, key, value) => setLoans((ls) => ls.map((l) => (l.id === id ? { ...l, [key]: value } : l)));

  return (
    <Panel title={`🏦 ${t.provisionTitle}`} intro={t.provisionIntro}>
      <form
        className="flex flex-col gap-3"
        onSubmit={(e) => {
          e.preventDefault();
          calc.run({
            fiscal_year: activeFy,
            family,
            loans: loans.map((l) => ({ amount: Number(l.amount || 0), months_overdue: Number(l.months || 0) })),
          });
        }}
      >
        <FySelect id="lp-fy" value={activeFy} onChange={setFy} fyOptions={fyOptions} years={years} />
        <label className={labelClass}>
          {t.institutionType}
          <select className={inputClass} value={family} onChange={(e) => setFamily(e.target.value)}>
            <option value="abc">{t.familyAbc}</option>
            <option value="mfi">{t.familyMfi}</option>
            <option value="nifra">{t.familyNifra}</option>
          </select>
        </label>
        {loans.map((l, i) => (
          <div key={l.id} className="grid grid-cols-[1fr_7rem_auto] gap-2 items-end">
            <label className={labelClass}>
              {i === 0 ? t.loanAmount : <span className="sr-only">{t.loanAmount}</span>}
              <input className={inputClass} type="number" min="0" step="1" inputMode="numeric" required value={l.amount}
                onChange={(e) => update(l.id, 'amount', e.target.value)} />
            </label>
            <label className={labelClass}>
              {i === 0 ? t.monthsOverdue : <span className="sr-only">{t.monthsOverdue}</span>}
              <input className={inputClass} type="number" min="0" step="0.5" inputMode="decimal" value={l.months}
                placeholder="0" onChange={(e) => update(l.id, 'months', e.target.value)} />
            </label>
            <button type="button" aria-label={t.removeLoan} disabled={loans.length === 1}
              onClick={() => setLoans((ls) => ls.filter((x) => x.id !== l.id))}
              className="h-9 px-2 text-gray-400 hover:text-nepal-red disabled:opacity-30">✕</button>
          </div>
        ))}
        <button type="button" onClick={() => setLoans((ls) => [...ls, newLoan()])} disabled={loans.length >= 20}
          className="self-start text-xs font-semibold text-nepal-blue hover:underline">{t.addLoan}</button>
        {!years.includes(activeFy) && <p className="text-xs text-amber-700">{t.noVerified(activeFy)}</p>}
        <SubmitButton loading={calc.loading} label={t.calculate} />
      </form>
      {calc.error && <p role="alert" className="mt-3 text-sm text-red-700">{calc.error}</p>}
      <CalculationCard calculation={calc.result} />
    </Panel>
  );
};

const CashCalculator = ({ fyOptions, defaultFy, years }) => {
  const { t } = useI18n();
  const [fy, setFy] = useState(defaultFy);
  const [amount, setAmount] = useState('');
  const calc = useCalculator(checkCashLimit);
  const activeFy = fy || defaultFy;

  return (
    <Panel title={`💵 ${t.cashTitle}`} intro={t.cashIntro}>
      <form
        className="flex flex-col gap-3"
        onSubmit={(e) => {
          e.preventDefault();
          calc.run({ fiscal_year: activeFy, amount: Number(amount || 0) });
        }}
      >
        <FySelect id="cash-fy" value={activeFy} onChange={setFy} fyOptions={fyOptions} years={years} />
        <label className={labelClass}>
          {t.cashAmount}
          <input className={inputClass} type="number" min="0" step="1" inputMode="numeric" required value={amount}
            onChange={(e) => setAmount(e.target.value)} />
        </label>
        {!years.includes(activeFy) && <p className="text-xs text-amber-700">{t.noVerified(activeFy)}</p>}
        <SubmitButton loading={calc.loading} label={t.check} />
      </form>
      {calc.error && <p role="alert" className="mt-3 text-sm text-red-700">{calc.error}</p>}
      <CalculationCard calculation={calc.result} />
    </Panel>
  );
};

const Calculators = ({ fyOptions, selectedFy, calculatorYears }) => {
  const { t } = useI18n();
  return (
    <div className="flex flex-col gap-4 p-4">
      <ProvisionCalculator fyOptions={fyOptions} defaultFy={selectedFy} years={calculatorYears.loan_provision || []} />
      <CashCalculator fyOptions={fyOptions} defaultFy={selectedFy} years={calculatorYears.cash_limit || []} />
      <p className="text-[11px] text-gray-500 px-1">{t.calcNote}</p>
    </div>
  );
};

export default Calculators;
