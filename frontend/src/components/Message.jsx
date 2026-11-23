// Message.jsx
// One chat bubble. Bot answers render markdown (tables included) and carry the
// FY badge, the FY warning banner, a calculation card, source cards and a copy button.

import { useState } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import CalculationCard from './CalculationCard';
import SourceCards from './SourceCards';
import { useI18n } from '../i18n/context';

const markdownComponents = {
  p: (props) => <p className="mb-3 last:mb-0" {...props} />,
  ul: (props) => <ul className="list-disc ml-5 mb-3" {...props} />,
  ol: (props) => <ol className="list-decimal ml-5 mb-3" {...props} />,
  li: (props) => <li className="mb-1" {...props} />,
  h1: (props) => <h3 className="text-base font-bold mb-2" {...props} />,
  h2: (props) => <h3 className="text-base font-bold mb-2" {...props} />,
  h3: (props) => <h4 className="font-bold mb-1.5" {...props} />,
  strong: (props) => <strong className="font-bold text-nepal-blue" {...props} />,
  table: (props) => (
    <div className="overflow-x-auto">
      <table {...props} />
    </div>
  ),
  a: (props) => <a target="_blank" rel="noopener noreferrer" {...props} />,
};

const CopyButton = ({ text }) => {
  const { t } = useI18n();
  const [copied, setCopied] = useState(false);
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      setCopied(false);
    }
  };
  return (
    <button
      type="button"
      onClick={copy}
      aria-label={t.copyLabel}
      className="text-[11px] font-medium text-gray-500 hover:text-nepal-blue px-2 py-0.5 rounded focus:outline-none focus-visible:ring-2 focus-visible:ring-nepal-blue/50"
    >
      {copied ? `✓ ${t.copied}` : `⧉ ${t.copy}`}
    </button>
  );
};

const BotAvatar = () => (
  <div
    className="flex-shrink-0 w-10 h-10 rounded-xl bg-gradient-to-br from-nepal-blue to-[#00246b] border-2 border-nepal-blue/20 shadow-sm flex items-center justify-center text-white text-lg"
    aria-hidden="true"
  >
    ₨
  </div>
);

const Message = ({ message }) => {
  const { t } = useI18n();
  const { sender, text, timestamp, fyUsed, fyWarning, sources, calculation, streaming, isError } = message;
  const isUser = sender === 'user';

  return (
    <div className={`flex ${isUser ? 'justify-end' : 'justify-start'} mb-6 items-end gap-3 group animate-bubble`}>
      {!isUser && <BotAvatar />}

      <div className={`flex flex-col ${isUser ? 'items-end' : 'items-start'} max-w-[88%] sm:max-w-[82%] min-w-0`}>
        <span className="text-[11px] font-bold mb-1.5 px-1 text-gray-500 uppercase tracking-widest opacity-70">
          {isUser ? t.you : `🇳🇵 ${t.bot}`}
        </span>

        <div
          className={`px-5 py-3.5 rounded-2xl text-[15px] leading-relaxed shadow-md min-w-0 max-w-full ${
            isUser
              ? 'bg-gradient-to-br from-nepal-red to-[#b31031] text-white rounded-br-none'
              : `bg-white text-gray-800 border rounded-bl-none ${isError ? 'border-red-200' : 'border-gray-100'}`
          }`}
        >
          {isUser ? (
            <p className="whitespace-pre-wrap break-words">{text}</p>
          ) : (
            <>
              {(fyUsed || fyWarning) && (
                <div className="flex flex-wrap items-center gap-2 mb-2">
                  {fyUsed && (
                    <span className="inline-flex items-center gap-1 text-[11px] font-semibold px-2 py-0.5 rounded-full bg-blue-50 text-nepal-blue border border-nepal-blue/20">
                      📅 {t.appliesTo(fyUsed)}
                    </span>
                  )}
                </div>
              )}
              {fyWarning && (
                <div role="alert" className="mb-3 rounded-lg border border-yellow-300 bg-yellow-50 px-3 py-2 text-sm text-yellow-900">
                  ⚠️ {fyWarning}
                </div>
              )}
              <div className={`answer break-words ${isError ? 'text-red-700' : ''}`}>
                <ReactMarkdown remarkPlugins={[remarkGfm]} components={markdownComponents}>
                  {text || ''}
                </ReactMarkdown>
                {streaming && <span className="inline-block w-2 h-4 bg-nepal-blue/60 animate-pulse align-middle ml-0.5" />}
              </div>
              <CalculationCard calculation={calculation} />
              <SourceCards sources={sources} />
            </>
          )}
        </div>

        <div className="flex items-center gap-1 mt-1.5 px-1">
          {timestamp && <span className="text-[10px] font-medium text-gray-400 italic">{timestamp}</span>}
          {!isUser && !streaming && !isError && text && <CopyButton text={text} />}
        </div>
      </div>

      {isUser && (
        <div className="flex-shrink-0 w-10 h-10 rounded-xl bg-gradient-to-br from-gray-600 to-gray-800 flex items-center justify-center shadow-md border border-gray-300" aria-hidden="true">
          <span className="text-white text-sm font-black">{t.you.charAt(0)}</span>
        </div>
      )}
    </div>
  );
};

export default Message;
