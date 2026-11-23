// App.jsx
// Root layout: Header (FY + language + new chat), then chat + calculators.
// Desktop: chat in the centre, calculators in a right sidebar.
// Mobile: a tab bar switches between chat and calculators.

import { useEffect, useMemo, useState } from 'react';
import Header from './components/Header';
import ChatBox from './components/ChatBox';
import Calculators from './components/Calculators';
import { API_URL, getDocuments, getFiscalYears } from './api';
import { I18nContext } from './i18n/context';
import strings from './i18n/strings';

const readStored = (key, fallback) => {
  try {
    return localStorage.getItem(key) || fallback;
  } catch {
    return fallback;
  }
};

const store = (key, value) => {
  try {
    localStorage.setItem(key, value);
  } catch {
    /* storage unavailable (private mode) — keep in memory only */
  }
};

const App = () => {
  const [lang, setLangState] = useState(() => (readStored('nbs-lang', 'en') === 'ne' ? 'ne' : 'en'));
  const [fyInfo, setFyInfo] = useState(null);
  const [selectedFy, setSelectedFy] = useState(() => readStored('nbs-fy', ''));
  const [isSampleCorpus, setIsSampleCorpus] = useState(false);
  const [loadError, setLoadError] = useState(API_URL ? '' : 'config');
  const [chatKey, setChatKey] = useState(0);
  const [tab, setTab] = useState('chat');

  const t = strings[lang];
  const i18n = useMemo(
    () => ({
      lang,
      t,
      setLang: (l) => {
        setLangState(l);
        store('nbs-lang', l);
      },
    }),
    [lang, t],
  );

  useEffect(() => {
    document.documentElement.lang = lang === 'ne' ? 'ne' : 'en';
  }, [lang]);

  useEffect(() => {
    if (!API_URL) return;
    getFiscalYears()
      .then((data) => {
        setFyInfo(data);
        const options = new Set([data.current, ...data.available]);
        setSelectedFy((prev) => (prev && options.has(prev) ? prev : data.default));
      })
      .catch(() => setLoadError('connect'));
    getDocuments()
      .then((docs) => setIsSampleCorpus(docs.some((d) => /SAMPLE|नमूना/.test(d.title))))
      .catch(() => {});
  }, []);

  const changeFy = (fy) => {
    setSelectedFy(fy);
    store('nbs-fy', fy);
  };

  const fyOptions = fyInfo
    ? Array.from(new Set([fyInfo.current, ...fyInfo.available]))
        .sort()
        .reverse()
        .map((fy) => ({ value: fy, isCurrent: fy === fyInfo.current, hasDocs: fyInfo.available.includes(fy) }))
    : [];

  const tabClass = (active) =>
    `flex-1 py-2.5 text-sm font-semibold border-b-2 transition-colors focus:outline-none focus-visible:bg-blue-50 ${
      active ? 'border-nepal-red text-nepal-red' : 'border-transparent text-gray-500'
    }`;

  return (
    <I18nContext.Provider value={i18n}>
      <div className="flex flex-col h-dvh bg-slate-50 overflow-hidden">
        <Header
          fyOptions={fyOptions}
          selectedFy={selectedFy}
          onFyChange={changeFy}
          onNewChat={() => {
            setChatKey((k) => k + 1);
            setTab('chat');
          }}
        />

        {loadError && (
          <div role="alert" className="bg-red-50 border-b border-red-200 text-red-800 text-sm px-4 py-2 text-center">
            {loadError === 'config' ? t.configMissing : t.errorConnect}
          </div>
        )}
        {isSampleCorpus && (
          <div className="bg-amber-50 border-b border-amber-200 text-amber-800 text-xs px-4 py-1.5 text-center">
            {t.sampleBanner}
          </div>
        )}

        {/* Mobile tabs */}
        <nav className="lg:hidden flex bg-white border-b border-gray-200" aria-label="Sections">
          <button type="button" className={tabClass(tab === 'chat')} aria-pressed={tab === 'chat'} onClick={() => setTab('chat')}>
            {t.tabChat}
          </button>
          <button type="button" className={tabClass(tab === 'calc')} aria-pressed={tab === 'calc'} onClick={() => setTab('calc')}>
            {t.tabCalculators}
          </button>
        </nav>

        <main className="flex flex-1 overflow-hidden min-h-0">
          <section className={`${tab === 'chat' ? 'flex' : 'hidden'} lg:flex flex-1 min-w-0`} aria-label={t.tabChat}>
            <ChatBox key={chatKey} fiscalYear={selectedFy} currentFy={fyInfo?.current} />
          </section>
          <aside
            className={`${tab === 'calc' ? 'flex' : 'hidden'} lg:flex flex-col w-full lg:w-[380px] lg:flex-shrink-0 lg:border-l border-gray-200 bg-white/70 overflow-y-auto chat-scroll`}
            aria-label={t.tabCalculators}
          >
            <Calculators fyOptions={fyOptions} selectedFy={selectedFy} calculatorYears={fyInfo?.calculator_years || {}} />
          </aside>
        </main>
      </div>
    </I18nContext.Provider>
  );
};

export default App;
